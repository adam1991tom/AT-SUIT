"""The pre-show check on the console dashboard: every tech laptop, screen and
caption mic, room by room, green when it's ready and red when it isn't.

good: ready. warn: on, but something to look at. bad: not working. off: not in
use (a room with captions turned off, say), never counted as a problem."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from .. import db
from ..security import Principal, require_manager
from .core import modules_enabled, site_ok

router = APIRouter()

QUIET_DB = -55.0  # a live mic quieter than this is probably muted, unplugged or the wrong input


def _laptop(c, n, online: bool) -> tuple[str, str]:
    from .fleet import current_room_id

    today = current_room_id(c, n) is not None
    who = n["operator"] or "nobody yet"
    if not online:
        return "bad", "Offline"
    if not today:
        return "warn", "On, but not started today: the tech picks their name and room"
    return "good", " · ".join(x for x in (who, n["version"] and f"v{n['version']}") if x)


def _screen(n, online: bool, views: dict) -> tuple[str, str]:
    if not online:
        return "bad", "Offline"
    if n["legacy"]:  # an old kiosk agent: it shows its own address
        return "good", n["current_url"] or "Old kiosk agent"
    if not n["screen_view"]:
        return "warn", "On, showing nothing yet: pick what it shows"
    return "good", views.get(n["screen_view"], n["screen_view"].removeprefix("url:"))


def check(c, p: Principal) -> dict:
    from . import captions, fleet
    from .timers import list_views

    now = time.time()
    mods = modules_enabled(c)
    rooms = [r for r in db.rows(c.execute("SELECT id,name,site_id FROM rooms WHERE enabled=1 ORDER BY sort,name"))
             if site_ok(p, r["site_id"])]
    by_room = {r["id"]: {**r, "items": []} for r in rooms}
    unplaced: list[dict] = []

    if mods.get("fleet"):
        views = {v["id"]: v["name"] for v in list_views(c, tests=True) + fleet.caption_views()}
        for n in c.execute("SELECT * FROM nodes ORDER BY name").fetchall():
            if not site_ok(p, n["site_id"]):
                continue
            online = bool(n["last_seen"] and now - n["last_seen"] < fleet.ONLINE_SECONDS)
            if n["kind"] == "tech":
                kind, (state, detail) = "laptop", _laptop(c, n, online)
                room = n["room_id"]  # a laptop not started today still shows where it was last
            elif n["kind"] == "kiosk":
                kind, (state, detail) = "screen", _screen(n, online, views)
                room = n["room_id"]
            else:
                kind, (state, detail) = "source", (("good", "Online") if online else ("bad", "Offline"))
                room = n["room_id"]
            item = {"kind": kind, "id": n["id"], "name": n["name"], "state": state, "detail": detail,
                    "mode": n["mode"] if kind == "laptop" else ""}
            if room in by_room:
                by_room[room]["items"].append(item)
            else:
                if state == "bad":  # off and in no room: not in use today, not a fault
                    item.update(state="off", detail="Offline, not in a room")
                unplaced.append(item)

    engine = None
    if mods.get("captions"):
        engine = captions.engine_status()
        ready = engine.get("state") == "ready"
        for r in rooms:
            if not captions.room_settings(c, r["id"]).get("enabled"):
                state, detail = "off", "Captions are off for this room"
            else:
                st = captions.rooms.get(r["id"])
                if st and st.ws:
                    quiet = st.level <= QUIET_DB
                    state = "warn" if quiet else "good"
                    detail = (f"Live from {st.source}" if st.source else "Live") + (
                        ": very quiet, check the mic and its input" if quiet else f", level {st.level:.0f} dB")
                elif not ready:
                    state, detail = "bad", "The caption engine isn't ready"
                else:
                    state, detail = "bad", "Not live: start the mic in the room's workspace (Captions)"
            by_room[r["id"]]["items"].append({"kind": "mic", "id": r["id"], "name": "Caption mic", "state": state, "detail": detail})

    order = {"laptop": 0, "screen": 1, "source": 2, "mic": 3}
    out = []
    for r in by_room.values():
        r["items"].sort(key=lambda i: (order[i["kind"]], i.get("mode") != "main", i["name"].lower()))
        out.append(r)
    everything = [i for r in out for i in r["items"]] + unplaced
    counts = {s: sum(1 for i in everything if i["state"] == s) for s in ("good", "warn", "bad", "off")}
    return {"checked_at": now, "rooms": out, "unplaced": unplaced, "counts": counts,
            "engine": {"state": engine.get("state"), "detail": engine.get("detail", "")} if engine else None}


@router.get("/api/preshow")
def preshow(p: Principal = Depends(require_manager)):
    with db.ro() as c:
        return check(c, p)
