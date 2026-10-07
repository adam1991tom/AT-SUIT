"""Room timers with Ontime-style cue lists. Replaces the per-room Ontime
containers: each room has one timer and a cue list (rundown). Techs load,
start, pause and go to the next cue; cues can stop, load or play the next one
when they end. Stage screens, the built-in views and the team's own custom
HTML views all read the same server-authoritative state.

Custom views written for Ontime keep working: they are served under
/external/<view>/ like Ontime does, and a small shim points their websocket at
/ontime/<room>/ws, which speaks Ontime's `runtime-data` protocol."""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from .. import config, db
from ..hub import hub
from ..security import Principal, require_admin, require_tech
from .core import require_module, room_or_404

router = APIRouter(dependencies=[Depends(require_module("timers"))])
public = APIRouter()  # custom views and the Ontime-compatible feed (module check inside)

TIMER_TYPES = ("count-down", "count-up", "clock", "none")
END_ACTIONS = ("none", "stop", "load-next", "play-next")
BUILTIN_VIEWS = {"stage": "Stage timer", "minimal": "Minimal timer", "clock": "Clock", "backstage": "Backstage (cue list)"}
MAX_DURATION = 24 * 3600 * 1000


# ------------------------------------------------------------------ state --
def _row(c, room_id: int):
    r = c.execute("SELECT * FROM timers WHERE room_id=?", (room_id,)).fetchone()
    if r:
        return r
    c.execute("INSERT INTO timers(room_id,updated_at) VALUES(?,?)", (room_id, db.now_iso()))
    return c.execute("SELECT * FROM timers WHERE room_id=?", (room_id,)).fetchone()


def remaining(r, at: float | None = None) -> int:
    if r["running"] and r["started_at"]:
        return int(r["remaining_ms"] - ((at or time.time()) - r["started_at"]) * 1000)
    return int(r["remaining_ms"])


def cue_out(q) -> dict:
    return {
        "id": q["id"], "cue": q["cue"], "title": q["title"], "note": q["note"], "duration_ms": q["duration_ms"],
        "time_start": q["time_start"], "timer_type": q["timer_type"], "end_action": q["end_action"],
        "skip": bool(q["skip"]), "colour": q["colour"], "warn_ms": q["warn_ms"], "danger_ms": q["danger_ms"],
        "custom": json.loads(q["custom_json"] or "{}"),
    }


def cue_list(c, room_id: int) -> list:
    return c.execute("SELECT * FROM cues WHERE room_id=? ORDER BY sort, id", (room_id,)).fetchall()


def _neighbours(cues, cue_id, step: int = 1):
    """The next (or previous) cue that isn't skipped."""
    ids = [q["id"] for q in cues]
    if cue_id in ids:
        i = ids.index(cue_id) + step
    else:
        i = 0 if step > 0 else len(cues) - 1
    while 0 <= i < len(cues):
        if not cues[i]["skip"]:
            return cues[i]
        i += step
    return None


def playback(r) -> str:
    if r["running"]:
        return "play"
    if r["first_started_at"]:
        return "pause"
    if r["cue_id"] or r["duration_ms"]:
        return "armed"
    return "stop"


def state(c, r, room_name: str = "") -> dict:
    now = time.time()
    cues = cue_list(c, r["room_id"])
    current = next((q for q in cues if q["id"] == r["cue_id"]), None)
    nxt = _neighbours(cues, r["cue_id"]) if cues else None
    left = remaining(r, now)
    elapsed = max(0, r["duration_ms"] + r["added_ms"] - left) if r["first_started_at"] else 0
    return {
        "room_id": r["room_id"], "room": room_name, "title": r["title"], "duration_ms": r["duration_ms"],
        "remaining_ms": left, "elapsed_ms": elapsed, "running": bool(r["running"]), "playback": playback(r),
        "timer_type": r["timer_type"], "end_action": r["end_action"], "added_ms": r["added_ms"],
        "started_at": r["first_started_at"],
        "message": r["message"], "message_visible": bool(r["message_visible"]), "message_blink": bool(r["message_blink"]),
        "blackout": bool(r["blackout"]), "warn_ms": r["warn_ms"], "danger_ms": r["danger_ms"],
        "flash_danger": bool(r["flash_danger"]),
        "cue": cue_out(current) if current else None, "next": cue_out(nxt) if nxt else None,
        "cue_index": [q["id"] for q in cues].index(current["id"]) if current else None, "cue_count": len(cues),
        "server_time": now,
    }


def _room_state(c, room_id: int) -> dict:
    room = c.execute("SELECT name FROM rooms WHERE id=?", (room_id,)).fetchone()
    return state(c, _row(c, room_id), room["name"] if room else "")


async def publish(room_id: int) -> dict:
    with db.ro() as c:
        out = _room_state(c, room_id)
    await hub.publish(f"timer:{room_id}", "timer", out)
    return out


def _update(c, room_id: int, **fields) -> None:
    fields["updated_at"] = db.now_iso()
    sets = ",".join(f"{k}=?" for k in fields)
    c.execute(f"UPDATE timers SET {sets} WHERE room_id=?", (*fields.values(), room_id))


def _load(c, room_id: int, q, start: bool) -> None:
    now = time.time()
    _update(c, room_id, cue_id=q["id"], title=q["title"], duration_ms=q["duration_ms"], remaining_ms=q["duration_ms"],
            timer_type=q["timer_type"], end_action=q["end_action"], added_ms=0,
            warn_ms=q["warn_ms"] if q["warn_ms"] is not None else 300000,
            danger_ms=q["danger_ms"] if q["danger_ms"] is not None else 60000,
            running=1 if start else 0, started_at=now if start else None, first_started_at=now if start else None)


def _stop(c, room_id: int) -> None:
    _update(c, room_id, cue_id=None, title="", duration_ms=0, remaining_ms=0, running=0, started_at=None,
            first_started_at=None, added_ms=0, timer_type="count-down", end_action="none")


# --------------------------------------------------------------- reading --
@router.get("/api/timers/{room_id}")
def get_timer(room_id: int):
    """Public, so stage screens and kiosks need no sign-in."""
    with db.tx() as c:
        room = room_or_404(c, room_id)
        return state(c, _row(c, room_id), room["name"])


@router.get("/api/timers/{room_id}/cues")
def get_cues(room_id: int):
    """Public like the timer: backstage screens show the running order."""
    with db.ro() as c:
        room_or_404(c, room_id)
        return [cue_out(q) for q in cue_list(c, room_id)]


# --------------------------------------------------------------- control --
class TimerAction(BaseModel):
    duration_ms: int | None = Field(default=None, ge=0, le=MAX_DURATION)
    delta_ms: int | None = Field(default=None, ge=-MAX_DURATION, le=MAX_DURATION)
    title: str | None = Field(default=None, max_length=200)
    message: str | None = Field(default=None, max_length=500)
    message_visible: bool | None = None
    message_blink: bool | None = None
    blackout: bool | None = None
    warn_ms: int | None = Field(default=None, ge=0)
    danger_ms: int | None = Field(default=None, ge=0)
    flash_danger: bool | None = None
    cue_id: int | None = None


ACTIONS = ("set", "start", "pause", "toggle", "reset", "add", "message", "thresholds",
           "load", "go", "next", "previous", "stop")


async def timer_action(room_id: int, action: str, body: TimerAction | None = None, p: Principal = Depends(require_tech)):
    body = body or TimerAction()
    if action not in ACTIONS:
        raise HTTPException(404, "Unknown timer action")
    now = time.time()
    with db.tx() as c:
        room_or_404(c, room_id, p)
        r = _row(c, room_id)
        left = remaining(r, now)
        running = bool(r["running"])
        cues = cue_list(c, room_id)
        if action == "set":
            dur = body.duration_ms if body.duration_ms is not None else r["duration_ms"]
            fields = {"cue_id": None, "duration_ms": dur, "remaining_ms": dur, "running": 0, "started_at": None,
                      "first_started_at": None, "added_ms": 0, "timer_type": "count-down", "end_action": "none"}
            if body.title is not None:
                fields["title"] = body.title
            _update(c, room_id, **fields)
        elif action in ("start", "toggle") and not running:
            if not r["cue_id"] and not r["duration_ms"] and cues and (q := _neighbours(cues, None)):
                _load(c, room_id, q, start=True)  # nothing loaded: start the first cue
            else:
                _update(c, room_id, running=1, started_at=now, remaining_ms=left,
                        first_started_at=r["first_started_at"] or now)
        elif action in ("pause", "toggle") and running:
            _update(c, room_id, running=0, started_at=None, remaining_ms=left)
        elif action == "reset":
            _update(c, room_id, running=0, started_at=None, remaining_ms=r["duration_ms"], first_started_at=None, added_ms=0)
        elif action == "add":
            delta = body.delta_ms or 0
            _update(c, room_id, remaining_ms=left + delta, started_at=now if running else None, added_ms=r["added_ms"] + delta)
        elif action == "message":
            fields = {}
            if body.message is not None:
                fields["message"] = body.message
            for k in ("message_visible", "message_blink", "blackout"):
                if getattr(body, k) is not None:
                    fields[k] = int(getattr(body, k))
            if fields:
                _update(c, room_id, **fields)
        elif action == "thresholds":
            fields = {k: getattr(body, k) for k in ("warn_ms", "danger_ms") if getattr(body, k) is not None}
            if body.flash_danger is not None:
                fields["flash_danger"] = int(body.flash_danger)
            if fields:
                _update(c, room_id, **fields)
        elif action == "load":
            q = next((q for q in cues if q["id"] == body.cue_id), None)
            if not q:
                raise HTTPException(404, "Cue not found")
            _load(c, room_id, q, start=False)
        elif action in ("go", "next"):
            q = _neighbours(cues, r["cue_id"])
            if q:
                _load(c, room_id, q, start=action == "go")
            elif action == "go":
                _stop(c, room_id)
        elif action == "previous":
            q = _neighbours(cues, r["cue_id"], -1)
            if q:
                _load(c, room_id, q, start=False)
        elif action == "stop":
            _stop(c, room_id)
    return await publish(room_id)


_flash_seen: dict[int, tuple] = {}  # room -> (run it fired for, danger already flashed)


def _flash(c, now: float) -> list[int]:
    """Rooms set to flash at danger (the Ontime automation the venue used):
    when a timer starts the blink goes off, and when it reaches its danger
    time the stage message blinks, once per run so a tech can turn it off."""
    changed = []
    for r in c.execute("SELECT * FROM timers WHERE flash_danger=1 AND first_started_at IS NOT NULL").fetchall():
        run = (r["cue_id"], r["first_started_at"])
        seen = _flash_seen.get(r["room_id"])
        if not seen or seen[0] != run:
            _flash_seen[r["room_id"]] = seen = (run, False)
            if r["message_blink"]:
                _update(c, r["room_id"], message_blink=0)
                changed.append(r["room_id"])
        if (r["running"] and not seen[1] and r["timer_type"] == "count-down"
                and remaining(r, now) <= r["danger_ms"]):
            _flash_seen[r["room_id"]] = (run, True)
            _update(c, r["room_id"], message_blink=1)
            changed.append(r["room_id"])
    return changed


async def end_actions() -> None:
    """When a running cue reaches zero, do what the cue says: stop, load the
    next cue, or play it. Cues set to 'none' run on into overtime, like Ontime.
    Also runs flash-at-danger."""
    while True:
        await asyncio.sleep(0.25)
        try:
            changed = []
            now = time.time()
            with db.tx() as c:
                changed += _flash(c, now)
                for r in c.execute("SELECT * FROM timers WHERE running=1 AND end_action!='none'").fetchall():
                    if remaining(r, now) > 0:
                        continue
                    nxt = _neighbours(cue_list(c, r["room_id"]), r["cue_id"])
                    if r["end_action"] == "stop" or not nxt:
                        _update(c, r["room_id"], running=0, started_at=None, remaining_ms=0)
                    else:
                        _load(c, r["room_id"], nxt, start=r["end_action"] == "play-next")
                    changed.append(r["room_id"])
            for room_id in dict.fromkeys(changed):
                await publish(room_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # keep ticking whatever happens
            print("timers.end_actions:", exc)


# ------------------------------------------------------------------ cues --
class CueIn(BaseModel):
    cue: str = Field(default="", max_length=20)
    title: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=2000)
    duration_ms: int = Field(default=0, ge=0, le=MAX_DURATION)
    time_start: str = Field(default="", pattern=r"^$|^\d{1,2}:\d{2}(:\d{2})?$")
    timer_type: str = "count-down"
    end_action: str = "none"
    skip: bool = False
    colour: str = Field(default="", pattern=r"^$|^#[0-9a-fA-F]{3,8}$")
    warn_ms: int | None = Field(default=None, ge=0)
    danger_ms: int | None = Field(default=None, ge=0)
    custom: dict = {}
    after_id: int | None = None  # insert after this cue (default: at the end)


def _check_cue(body: CueIn) -> None:
    if body.timer_type not in TIMER_TYPES:
        raise HTTPException(400, f"Timer type must be one of {', '.join(TIMER_TYPES)}")
    if body.end_action not in END_ACTIONS:
        raise HTTPException(400, f"End action must be one of {', '.join(END_ACTIONS)}")


def _resort(c, room_id: int, ids: list[int]) -> None:
    for i, cid in enumerate(ids):
        c.execute("UPDATE cues SET sort=? WHERE id=? AND room_id=?", (i, cid, room_id))


def _insert_cue(c, room_id: int, body: CueIn) -> int:
    cid = c.execute(
        "INSERT INTO cues(room_id,sort,cue,title,note,duration_ms,time_start,timer_type,end_action,skip,colour,warn_ms,danger_ms,custom_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (room_id, 1 << 30, body.cue, body.title, body.note, body.duration_ms, body.time_start, body.timer_type,
         body.end_action, int(body.skip), body.colour, body.warn_ms, body.danger_ms, json.dumps(body.custom)[:4000])).lastrowid
    ids = [q["id"] for q in cue_list(c, room_id) if q["id"] != cid]
    pos = ids.index(body.after_id) + 1 if body.after_id in ids else len(ids)
    ids.insert(pos, cid)
    _resort(c, room_id, ids)
    return cid


@router.post("/api/timers/{room_id}/cues")
async def add_cue(room_id: int, body: CueIn, p: Principal = Depends(require_tech)):
    _check_cue(body)
    with db.tx() as c:
        room_or_404(c, room_id, p)
        if c.execute("SELECT COUNT(*) FROM cues WHERE room_id=?", (room_id,)).fetchone()[0] >= 500:
            raise HTTPException(400, "A room can have up to 500 cues")
        cid = _insert_cue(c, room_id, body)
    await _cues_changed(room_id)
    return {"id": cid}


@router.put("/api/timers/{room_id}/cues/{cue_id}")
async def edit_cue(room_id: int, cue_id: int, body: CueIn, p: Principal = Depends(require_tech)):
    _check_cue(body)
    with db.tx() as c:
        room_or_404(c, room_id, p)
        if not c.execute("SELECT id FROM cues WHERE id=? AND room_id=?", (cue_id, room_id)).fetchone():
            raise HTTPException(404, "Cue not found")
        c.execute("UPDATE cues SET cue=?,title=?,note=?,duration_ms=?,time_start=?,timer_type=?,end_action=?,skip=?,colour=?,"
                  "warn_ms=?,danger_ms=?,custom_json=? WHERE id=?",
                  (body.cue, body.title, body.note, body.duration_ms, body.time_start, body.timer_type, body.end_action,
                   int(body.skip), body.colour, body.warn_ms, body.danger_ms, json.dumps(body.custom)[:4000], cue_id))
        r = _row(c, room_id)
        if r["cue_id"] == cue_id:  # keep the loaded cue's title and end action in step
            _update(c, room_id, title=body.title, end_action=body.end_action, timer_type=body.timer_type)
            if not r["first_started_at"]:
                _update(c, room_id, duration_ms=body.duration_ms, remaining_ms=body.duration_ms)
    await _cues_changed(room_id)
    return {"ok": True}


@router.delete("/api/timers/{room_id}/cues/{cue_id}")
async def delete_cue(room_id: int, cue_id: int, p: Principal = Depends(require_tech)):
    with db.tx() as c:
        room_or_404(c, room_id, p)
        c.execute("DELETE FROM cues WHERE id=? AND room_id=?", (cue_id, room_id))
        if _row(c, room_id)["cue_id"] == cue_id:
            _update(c, room_id, cue_id=None)
    await _cues_changed(room_id)
    return {"ok": True}


class Reorder(BaseModel):
    ids: list[int]


@router.post("/api/timers/{room_id}/cues/reorder")
async def reorder_cues(room_id: int, body: Reorder, p: Principal = Depends(require_tech)):
    with db.tx() as c:
        room_or_404(c, room_id, p)
        have = [q["id"] for q in cue_list(c, room_id)]
        if sorted(have) != sorted(body.ids):
            raise HTTPException(400, "Send every cue id of this room once")
        _resort(c, room_id, body.ids)
    await _cues_changed(room_id)
    return {"ok": True}


def ontime_events(data: dict) -> list[dict]:
    """Events from an Ontime project file (v3 `rundown` list or v4 `rundowns`)."""
    if isinstance(data.get("rundowns"), dict):
        rundown = next(iter(data["rundowns"].values()), {}) or {}
        entries = rundown.get("entries", {})
        order = rundown.get("flatOrder") or rundown.get("order") or list(entries)
        items = [entries[i] for i in order if i in entries]
    else:
        items = data.get("rundown", [])
    return [e for e in items if isinstance(e, dict) and e.get("type") == "event"]


def ontime_flashes(data: dict) -> bool | None:
    """Whether the project blinks the timer when it reaches danger: an
    onDanger trigger whose automation calls /api/message/timer?blink=true
    (on any Ontime server, since AT-SUIT keeps it inside the room). None if
    the file has no automations at all."""
    auto = data.get("automation")
    if not isinstance(auto, dict) or not isinstance(auto.get("triggers"), list):
        return None
    if auto.get("enabledAutomations") is False:
        return False
    automations = auto.get("automations") or {}
    for t in auto["triggers"]:
        if not isinstance(t, dict) or t.get("trigger") != "onDanger":
            continue
        for out in (automations.get(t.get("automationId")) or {}).get("outputs") or []:
            url = str(out.get("url", "")) if isinstance(out, dict) else ""
            if "/api/message/timer" in url and re.search(r"[?&]blink=true", url):
                return True
    return False


def _ms_to_hhmm(ms) -> str:
    if not isinstance(ms, (int, float)) or ms < 0:
        return ""
    m = int(ms // 60000)
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


@router.post("/api/timers/{room_id}/cues/import")
async def import_ontime(room_id: int, file: UploadFile, replace: bool = True, p: Principal = Depends(require_tech)):
    """Bring in a room's running order from an Ontime project file (db.json)."""
    try:
        data = json.loads(await file.read(10 * 1024 * 1024))
    except ValueError:
        raise HTTPException(400, "That isn't an Ontime project file (JSON)")
    events = ontime_events(data)
    if not events:
        raise HTTPException(400, "No events found in that Ontime file")
    with db.tx() as c:
        room_or_404(c, room_id, p)
        if replace:
            c.execute("DELETE FROM cues WHERE room_id=?", (room_id,))
            _update(c, room_id, cue_id=None)
        for e in events[:500]:
            end = e.get("endAction", "none")
            body = CueIn(
                cue=str(e.get("cue", ""))[:20], title=str(e.get("title", ""))[:200], note=str(e.get("note", ""))[:2000],
                duration_ms=max(0, min(int(e.get("duration") or 0), MAX_DURATION)), time_start=_ms_to_hhmm(e.get("timeStart")),
                timer_type=e.get("timerType") if e.get("timerType") in TIMER_TYPES else "count-down",
                end_action=end if end in END_ACTIONS else "none", skip=bool(e.get("skip")),
                colour=c_ if re.match(r"^#[0-9a-fA-F]{3,8}$", c_ := str(e.get("colour") or "")) else "", warn_ms=e.get("timeWarning"), danger_ms=e.get("timeDanger"),
                custom=e.get("custom") if isinstance(e.get("custom"), dict) else {})
            _insert_cue(c, room_id, body)
        flash = ontime_flashes(data)
        if flash is not None:
            _row(c, room_id)
            _update(c, room_id, flash_danger=int(flash))
        fmt = (data.get("settings") or {}).get("timeFormat")
        if fmt in ("12", "24"):
            db.set_setting(c, "ontime_time_format", fmt)
        db.audit(c, p.name, "timers.import_ontime", f"room {room_id}: {len(events)} cues")
    await _cues_changed(room_id)
    return {"imported": min(len(events), 500), "flash_danger": flash}


async def _cues_changed(room_id: int) -> None:
    with db.ro() as c:
        cues = [cue_out(q) for q in cue_list(c, room_id)]
    await hub.publish(f"timer:{room_id}", "cues", {"room_id": room_id, "cues": cues})
    await publish(room_id)


# ---------------------------------------------------------- custom views --
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def views_dir() -> Path:
    d = config.cfg.data / "views"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_views(c) -> list[dict]:
    custom = db.rows(c.execute("SELECT slug,name,created_at FROM timer_views ORDER BY name"))
    return [{"id": k, "name": v, "builtin": True} for k, v in BUILTIN_VIEWS.items()] + \
           [{"id": f"view:{v['slug']}", "slug": v["slug"], "name": v["name"], "builtin": False, "created_at": v["created_at"]} for v in custom]


@router.get("/api/timers-views")
def get_views():
    with db.ro() as c:
        return list_views(c)


@router.post("/api/timers-views")
async def upload_view(name: str, file: UploadFile, p: Principal = Depends(require_admin)):
    """A custom timer view: a single .html file, or a .zip with index.html and
    its css/js/images (the same folder you'd put in Ontime's external/)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    if not SLUG.match(slug or ""):
        raise HTTPException(400, "Give the view a name with letters or numbers")
    data = await file.read(30 * 1024 * 1024)
    target = views_dir() / slug
    tmp = views_dir() / f".{slug}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    fname = (file.filename or "").lower()
    if fname.endswith(".zip"):
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise HTTPException(400, "That zip file is damaged")
        names = [n for n in z.namelist() if not n.endswith("/")]
        # A zip of the folder itself has every path under one top folder: drop it.
        tops = {n.split("/", 1)[0] for n in names}
        strip = len(tops) == 1 and all("/" in n for n in names)
        for n in names:
            rel = n.split("/", 1)[1] if strip else n
            dest = (tmp / rel).resolve()
            if not str(dest).startswith(str(tmp.resolve()) + os.sep) or rel.startswith("__MACOSX"):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(n))
    elif fname.endswith((".html", ".htm")):
        (tmp / "index.html").write_bytes(data)
    else:
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(400, "Upload an .html file or a .zip of the view's folder")
    if not (tmp / "index.html").is_file():
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(400, "The view needs an index.html")
    shutil.rmtree(target, ignore_errors=True)
    tmp.replace(target)
    with db.tx() as c:
        c.execute("INSERT INTO timer_views(slug,name,created_at) VALUES(?,?,?) ON CONFLICT(slug) DO UPDATE SET name=excluded.name",
                  (slug, name.strip()[:80], db.now_iso()))
        db.audit(c, p.name, "timers.view_upload", slug)
    return {"id": f"view:{slug}", "slug": slug, "url": f"/room/<room>/external/{slug}/"}


@router.delete("/api/timers-views/{slug}")
def delete_view(slug: str, p: Principal = Depends(require_admin)):
    if not SLUG.match(slug):
        raise HTTPException(404)
    shutil.rmtree(views_dir() / slug, ignore_errors=True)
    with db.tx() as c:
        c.execute("DELETE FROM timer_views WHERE slug=?", (slug,))
        db.audit(c, p.name, "timers.view_delete", slug)
    return {"ok": True}


SHIM = '<script src="/static/ontime-shim.js"></script>'


@public.get("/room/{room_id}/external/{slug}", include_in_schema=False)
@public.get("/external/{slug}", include_in_schema=False)
def view_slash(request: Request, slug: str, room_id: int | None = None):
    # Relative links in a view (styles.css, assets/...) need the trailing slash.
    url = request.url
    return RedirectResponse(url.replace(path=url.path + "/"), status_code=307)


@public.get("/room/{room_id}/external/{slug}/{path:path}", include_in_schema=False)
def serve_room_view(room_id: int, slug: str, path: str = ""):
    """A custom view for one room. The room is in the path, as each room had
    its own Ontime server, so a view's own ?room=... setting still works."""
    return serve_view(slug, path)


@public.get("/external/{slug}/{path:path}", include_in_schema=False)
def serve_view(slug: str, path: str = ""):
    """Custom views at the same path Ontime uses. Pages get the shim that
    points their Ontime websocket at the room (from /room/<id>/..., or ?room=)."""
    if not SLUG.match(slug):
        raise HTTPException(404)
    root = (views_dir() / slug).resolve()
    f = (root / (path or "index.html")).resolve()
    if f.is_dir():
        f = f / "index.html"
    if not str(f).startswith(str(root) + os.sep) or not f.is_file():
        raise HTTPException(404)
    if f.suffix.lower() in (".html", ".htm"):
        html = f.read_text(errors="replace")
        m = re.search(r"<head[^>]*>", html, re.I)
        html = html[:m.end()] + SHIM + html[m.end():] if m else SHIM + html
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
    return FileResponse(f, headers={"Cache-Control": "no-cache"})


# ------------------------------------------------- Ontime-compatible feed --
def _site_zone(c, room_id: int):
    row = c.execute("SELECT s.timezone FROM rooms r JOIN sites s ON s.id=r.site_id WHERE r.id=?", (room_id,)).fetchone()
    try:
        return ZoneInfo((row and row["timezone"]) or os.getenv("TZ") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _hhmm_to_ms(s: str):
    if not s:
        return None
    parts = [int(x) for x in s.split(":")]
    return (parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)) * 1000


def ontime_event(q) -> dict | None:
    if not q:
        return None
    start = _hhmm_to_ms(q["time_start"])
    return {
        "type": "event", "id": str(q["id"]), "cue": q["cue"], "title": q["title"], "note": q["note"],
        "endAction": q["end_action"] if q["end_action"] != "stop" else "none", "timerType": q["timer_type"],
        "countToEnd": False, "linkStart": False, "timeStrategy": "lock-duration", "flag": False,
        "timeStart": start or 0, "timeEnd": (start or 0) + q["duration_ms"], "duration": q["duration_ms"],
        "skip": bool(q["skip"]), "colour": q["colour"], "timeWarning": q["warn_ms"] if q["warn_ms"] is not None else 300000,
        "timeDanger": q["danger_ms"] if q["danger_ms"] is not None else 60000,
        "custom": json.loads(q["custom_json"] or "{}"), "triggers": [], "parent": None, "revision": 0,
        "delay": 0, "dayOffset": 0, "gap": 0,
    }


def ontime_runtime(c, room_id: int) -> dict:
    """The room's state in Ontime's RuntimeStore shape (v4)."""
    r = _row(c, room_id)
    now = time.time()
    cues = cue_list(c, room_id)
    current = next((q for q in cues if q["id"] == r["cue_id"]), None)
    nxt = _neighbours(cues, r["cue_id"]) if cues else None
    left = remaining(r, now)
    pb = playback(r)
    loaded = pb != "stop"
    total = r["duration_ms"] + r["added_ms"]
    if not loaded:
        phase = "none"
    elif left < 0:
        phase = "overtime"
    elif left <= r["danger_ms"]:
        phase = "danger"
    elif left <= r["warn_ms"]:
        phase = "warning"
    else:
        phase = "default"
    local = datetime.now(_site_zone(c, room_id))
    clock = ((local.hour * 60 + local.minute) * 60 + local.second) * 1000 + local.microsecond // 1000
    # Ontime gives moments as milliseconds since local midnight, like its clock.
    def of_day(t):
        return int(clock - (now - t) * 1000) % 86400000 if t else None
    idle = {"current": 0, "duration": 0, "playback": "stop", "direction": "count-down"}
    return {
        "clock": clock,
        "timer": {
            "addedTime": r["added_ms"], "current": left if loaded else None, "duration": total if loaded else None,
            "elapsed": max(0, total - left) if r["first_started_at"] else None,
            "expectedFinish": (clock + left) % 86400000 if r["running"] else None,
            "phase": phase, "playback": pb, "secondaryTimer": None,
            "startedAt": of_day(r["first_started_at"]),
        },
        "message": {"timer": {"text": r["message"], "visible": bool(r["message_visible"]), "blink": bool(r["message_blink"]),
                              "blackout": bool(r["blackout"]), "secondarySource": None}, "secondary": ""},
        "rundown": {"selectedEventIndex": [q["id"] for q in cues].index(current["id"]) if current else None,
                    "numEvents": len(cues), "plannedStart": _hhmm_to_ms(cues[0]["time_start"]) if cues else None,
                    "plannedEnd": None, "actualStart": of_day(r["first_started_at"]),
                    "currentDay": 0, "actualGroupStart": None},
        "offset": {"absolute": 0, "relative": 0, "mode": "absolute", "expectedGroupEnd": None,
                   "expectedRundownEnd": None, "expectedFlagStart": None},
        "eventNow": ontime_event(current) if current else (
            {"type": "event", "id": "manual", "cue": "", "title": r["title"], "note": "", "duration": r["duration_ms"],
             "timerType": r["timer_type"], "endAction": "none", "colour": "", "timeStart": 0, "timeEnd": 0,
             "timeWarning": r["warn_ms"], "timeDanger": r["danger_ms"], "custom": {}, "skip": False} if loaded else None),
        "eventNext": ontime_event(nxt),
        "eventFlag": None, "groupNow": None,
        "auxtimer1": idle, "auxtimer2": idle, "auxtimer3": idle,
        "ping": 0,
    }


def _timers_on() -> bool:
    from .core import modules_enabled

    with db.ro() as c:
        return modules_enabled(c).get("timers", False)


@public.websocket("/ontime/{room_id}/ws")
async def ontime_ws(ws: WebSocket, room_id: int):
    """Ontime's websocket protocol for one room: `runtime-data` with the full
    store on connect, then patches of what changed (clock and timer at 4 Hz)."""
    with db.ro() as c:
        exists = c.execute("SELECT 1 FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not exists or not _timers_on():
        await ws.close(code=4404)
        return
    await ws.accept()
    sent: dict = {}

    async def push(full: bool = False):
        with db.tx() as c:
            data = ontime_runtime(c, room_id)
        patch = data if full else {k: v for k, v in data.items() if sent.get(k) != v}
        if patch:
            await ws.send_text(json.dumps({"tag": "runtime-data", "payload": patch}))
            sent.update(patch)

    async def reader():
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except ValueError:
                continue
            if msg.get("tag") == "ping" or msg.get("type") == "ping":
                await ws.send_text(json.dumps({"tag": "pong", "payload": msg.get("payload")}))

    try:
        await ws.send_text(json.dumps({"tag": "client-init", "payload": {"clientId": f"atsuit-{id(ws)}", "clientName": "AT-SUIT view"}}))
        await push(full=True)
        task = asyncio.create_task(reader())
        try:
            while not task.done():
                await asyncio.sleep(0.25)
                await push()
        finally:
            task.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass


@public.get("/ontime/{room_id}/data/runtime", include_in_schema=False)
def ontime_rest_runtime(room_id: int):
    with db.tx() as c:
        room_or_404(c, room_id)
        return ontime_runtime(c, room_id)


@public.get("/ontime/{room_id}/data/settings", include_in_schema=False)
def ontime_rest_settings(room_id: int):
    with db.ro() as c:
        room_or_404(c, room_id)
        fmt = db.get_setting(c, "ontime_time_format", "24")
    return {"version": "4.14.1", "serverPort": 4001, "editorKey": None, "operatorKey": None,
            "timeFormat": fmt if fmt in ("12", "24") else "24", "language": "en", "auxTimerNames": ["", "", ""]}


@public.get("/ontime/{room_id}/data/rundowns/current", include_in_schema=False)
def ontime_rest_rundown(room_id: int):
    with db.ro() as c:
        room_or_404(c, room_id)
        events = [ontime_event(q) for q in cue_list(c, room_id)]
    return {"id": "default", "title": "", "order": [e["id"] for e in events], "flatOrder": [e["id"] for e in events],
            "entries": {e["id"]: e for e in events}, "revision": 0}


# Registered last so /api/timers/<room>/cues/... routes win over /<action>.
router.add_api_route("/api/timers/{room_id}/{action}", timer_action, methods=["POST"])
