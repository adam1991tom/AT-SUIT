"""Presenter management, from AT-Presenter: events and their running order,
a no-login portal where each presenter uploads their slides and checks in,
file review by the AV team, show files per session, schedule import
(spreadsheet, or a local AI model for PDFs and Word files), room sync for the
presentation laptops, and sending a room's sessions to its timer."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .. import config, db, schedule
from ..hub import hub
from ..security import Principal, decrypt, encrypt, principal, require_admin, require_node, require_tech
from .core import require_module, room_or_404, site_ok

router = APIRouter(dependencies=[Depends(require_module("presenter"))])

EVENT_STATES = ("planning", "live", "finished")
REVIEW = ("pending", "approved", "rejected")
SHOW_KINDS = ("presentation", "video", "picture", "audio", "other")
DEFAULTS = {"upload_limit_mb": 2048, "ai_url": "", "ai_model": "llama3.1:8b", "portal_note": ""}


def files_root() -> Path:
    """Where presenter files live. Set ATSUIT_PRESENTER_FILES to a NAS mount
    to keep them off the server's disk."""
    d = Path(os.getenv("ATSUIT_PRESENTER_FILES") or config.cfg.data / "presenter-files")
    d.mkdir(parents=True, exist_ok=True)
    return d


def prefs(c) -> dict:
    return {**DEFAULTS, **(db.get_setting(c, "presenter", {}) or {})}


def _safe(name: str) -> str:
    return "".join(ch for ch in (name or "file") if ch.isalnum() or ch in "._- ")[:150].strip() or "file"


async def _save(file: UploadFile, folder: str, limit_mb: int) -> dict:
    name = _safe(os.path.basename(file.filename or "file"))
    rel = f"{folder}/{uuid.uuid4().hex}_{name}"
    dest = files_root() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    size, h = 0, hashlib.sha256()
    try:
        with dest.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit_mb * 1024 * 1024:
                    raise HTTPException(413, f"Files can be up to {limit_mb} MB")
                h.update(chunk)
                f.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    if not size:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "That file is empty")
    return {"path": rel, "name": os.path.basename(file.filename or name)[:200], "mime": (file.content_type or "")[:100],
            "size": size, "sha256": h.hexdigest()}


def _remove(rel: str | None) -> None:
    if rel:
        try:
            (files_root() / rel).unlink(missing_ok=True)
        except OSError:
            pass


def _send(rel: str, name: str, mime: str):
    f = files_root() / rel
    if not f.is_file():
        raise HTTPException(410, "The file is missing from storage")
    return FileResponse(f, media_type=mime or "application/octet-stream", filename=name)


async def _changed(event_id: int | None, room_id: int | None = None) -> None:
    data = {"event_id": event_id, "room_id": room_id}
    await hub.publish("presenter", "presenter.changed", data)
    if room_id:
        await hub.publish(f"room:{room_id}", "presenter.changed", data)


# ------------------------------------------------------------- read models --
def _event(c, event_id: int, p: Principal) -> dict:
    e = c.execute("SELECT * FROM pr_events WHERE id=?", (event_id,)).fetchone()
    if not e or not site_ok(p, e["site_id"]):
        raise HTTPException(404, "Event not found")
    return dict(e)


def _file_out(f) -> dict:
    d = dict(f)
    d.pop("stored_path", None)
    return d


def presenter_out(c, pr, full: bool = True) -> dict:
    d = dict(pr)
    d["email"], d["phone"] = decrypt(d.pop("email_enc") or ""), decrypt(d.pop("phone_enc") or "")
    files = [_file_out(f) for f in c.execute("SELECT * FROM pr_files WHERE presenter_id=? ORDER BY id DESC", (pr["id"],))]
    for i, f in enumerate(files):
        f["version"] = len(files) - i
    d["files"] = files if full else files[:1]
    if not full:
        d.pop("token", None)
    return d


def session_out(c, s) -> dict:
    d = dict(s)
    room = c.execute("SELECT name FROM rooms WHERE id=?", (s["room_id"],)).fetchone() if s["room_id"] else None
    d["room_name"] = room["name"] if room else ""
    d["presenters"] = [presenter_out(c, pr) for pr in c.execute("SELECT * FROM pr_presenters WHERE session_id=? ORDER BY id", (s["id"],))]
    d["show_files"] = [_file_out(f) for f in c.execute("SELECT * FROM pr_show_files WHERE session_id=? ORDER BY position, id", (s["id"],))]
    latest = [pr["files"][0] for pr in d["presenters"] if pr["files"]]
    d["ready"] = {
        "presenters": len(d["presenters"]),
        "checked_in": sum(1 for pr in d["presenters"] if pr["checked_in_at"]),
        "uploaded": len(latest),
        "approved": sum(1 for f in latest if f["review_status"] == "approved"),
        "rejected": sum(1 for f in latest if f["review_status"] == "rejected"),
        "pending": sum(1 for f in latest if f["review_status"] == "pending"),
        "show_files": len(d["show_files"]),
    }
    return d


# ------------------------------------------------------------------ events --
class EventIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    client: str = Field(default="", max_length=120)
    colour: str = Field(default="#8b5cf6", pattern=r"^#[0-9a-fA-F]{6}$")
    starts_on: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    ends_on: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    status: str = "planning"
    site_id: int | None = None
    archived: bool = False


def _check_event(c, body: EventIn, p: Principal) -> int:
    if body.status not in EVENT_STATES:
        raise HTTPException(400, "Status must be planning, live or finished")
    site_id = body.site_id or p.site_id or c.execute("SELECT id FROM sites ORDER BY id LIMIT 1").fetchone()["id"]
    if not site_ok(p, site_id) or not c.execute("SELECT 1 FROM sites WHERE id=?", (site_id,)).fetchone():
        raise HTTPException(404, "Site not found")
    return site_id


@router.get("/api/presenter/events")
def list_events(archived: bool = False, p: Principal = Depends(require_tech)):
    with db.ro() as c:
        rows = db.rows(c.execute(
            "SELECT e.*, (SELECT COUNT(*) FROM pr_sessions s WHERE s.event_id=e.id) AS sessions, "
            "(SELECT COUNT(*) FROM pr_presenters pp WHERE pp.event_id=e.id) AS presenters, "
            "(SELECT COUNT(*) FROM pr_files f JOIN pr_presenters pp ON pp.id=f.presenter_id WHERE pp.event_id=e.id AND f.review_status='pending') AS pending "
            "FROM pr_events e WHERE e.archived=? ORDER BY e.starts_on DESC, e.id DESC", (int(archived),)))
    return [r for r in rows if site_ok(p, r["site_id"])]


@router.post("/api/presenter/events")
async def add_event(body: EventIn, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        site_id = _check_event(c, body, p)
        eid = c.execute("INSERT INTO pr_events(site_id,name,client,colour,starts_on,ends_on,status,archived,created_at) VALUES(?,?,?,?,?,?,?,0,?)",
                        (site_id, body.name.strip(), body.client.strip(), body.colour, body.starts_on, body.ends_on, body.status,
                         db.now_iso())).lastrowid
        db.audit(c, p.name, "presenter.event_add", body.name)
    await _changed(eid)
    return {"id": eid}


@router.put("/api/presenter/events/{event_id}")
async def edit_event(event_id: int, body: EventIn, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        _event(c, event_id, p)
        site_id = _check_event(c, body, p)
        c.execute("UPDATE pr_events SET site_id=?,name=?,client=?,colour=?,starts_on=?,ends_on=?,status=?,archived=? WHERE id=?",
                  (site_id, body.name.strip(), body.client.strip(), body.colour, body.starts_on, body.ends_on, body.status,
                   int(body.archived), event_id))
    await _changed(event_id)
    return {"ok": True}


@router.delete("/api/presenter/events/{event_id}")
async def delete_event(event_id: int, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        e = _event(c, event_id, p)
        paths = [r[0] for r in c.execute("SELECT f.stored_path FROM pr_files f JOIN pr_presenters pp ON pp.id=f.presenter_id WHERE pp.event_id=?", (event_id,))]
        paths += [r[0] for r in c.execute("SELECT f.stored_path FROM pr_show_files f JOIN pr_sessions s ON s.id=f.session_id WHERE s.event_id=?", (event_id,))]
        c.execute("DELETE FROM pr_events WHERE id=?", (event_id,))
        db.audit(c, p.name, "presenter.event_delete", e["name"])
    for path in paths:
        _remove(path)
    await _changed(event_id)
    return {"ok": True}


@router.get("/api/presenter/events/{event_id}")
def event_detail(event_id: int, p: Principal = Depends(require_tech)):
    """The event's running order with every session's readiness."""
    with db.ro() as c:
        e = _event(c, event_id, p)
        e["sessions"] = [session_out(c, s) for s in c.execute(
            "SELECT * FROM pr_sessions WHERE event_id=? ORDER BY starts_at='', starts_at, sort, id", (event_id,))]
        e["unassigned"] = [presenter_out(c, pr) for pr in c.execute(
            "SELECT * FROM pr_presenters WHERE event_id=? AND session_id IS NULL ORDER BY full_name", (event_id,))]
        e["rooms"] = db.rows(c.execute("SELECT id,name FROM rooms WHERE site_id=? AND enabled=1 ORDER BY sort,name", (e["site_id"],)))
    return e


@router.get("/api/presenter/events/{event_id}/schedule.csv")
def event_csv(event_id: int, p: Principal = Depends(require_tech)):
    e = event_detail(event_id, p)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Room", "Start", "End", "Session", "Presenter", "Checked in", "File", "Review", "Show files"])
    for s in e["sessions"]:
        for pr in s["presenters"] or [None]:
            f = pr["files"][0] if pr and pr["files"] else None
            w.writerow([s["room_name"], s["starts_at"], s["ends_at"], s["title"], pr["full_name"] if pr else "",
                        "yes" if pr and pr["checked_in_at"] else "", f["original_name"] if f else "",
                        f["review_status"] if f else "", s["ready"]["show_files"]])
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", e["name"]).strip("_") or "event"
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}_schedule.csv"'})


# ---------------------------------------------------------------- sessions --
class SessionIn(BaseModel):
    event_id: int
    room_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    starts_at: str = Field(default="", max_length=25)
    ends_at: str = Field(default="", max_length=25)
    notes: str = Field(default="", max_length=4000)


def _when(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    try:
        return datetime.fromisoformat(v).isoformat(timespec="minutes")
    except ValueError:
        if re.match(r"^\d{1,2}:\d{2}$", v):
            return v.zfill(5)
        raise HTTPException(400, f"Times look like 2026-10-02T09:30 (got {v})") from None


def _check_session(c, body: SessionIn, p: Principal) -> dict:
    e = _event(c, body.event_id, p)
    if body.room_id is not None:
        r = c.execute("SELECT site_id FROM rooms WHERE id=?", (body.room_id,)).fetchone()
        if not r or r["site_id"] != e["site_id"]:
            raise HTTPException(400, "That room isn't at this event's site")
    return e


def _session(c, session_id: int, p: Principal):
    s = c.execute("SELECT * FROM pr_sessions WHERE id=?", (session_id,)).fetchone()
    if not s:
        raise HTTPException(404, "Session not found")
    _event(c, s["event_id"], p)
    return s


@router.post("/api/presenter/sessions")
async def add_session(body: SessionIn, p: Principal = Depends(require_tech)):
    with db.tx() as c:
        _check_session(c, body, p)
        sid = c.execute("INSERT INTO pr_sessions(event_id,room_id,title,starts_at,ends_at,notes,sort,created_at) VALUES(?,?,?,?,?,?,0,?)",
                        (body.event_id, body.room_id, body.title.strip(), _when(body.starts_at), _when(body.ends_at),
                         body.notes, db.now_iso())).lastrowid
        out = session_out(c, c.execute("SELECT * FROM pr_sessions WHERE id=?", (sid,)).fetchone())
    await _changed(body.event_id, body.room_id)
    return out


@router.put("/api/presenter/sessions/{session_id}")
async def edit_session(session_id: int, body: SessionIn, p: Principal = Depends(require_tech)):
    with db.tx() as c:
        old = _session(c, session_id, p)
        if body.event_id != old["event_id"]:
            raise HTTPException(400, "A session can't move to another event")
        _check_session(c, body, p)
        c.execute("UPDATE pr_sessions SET room_id=?,title=?,starts_at=?,ends_at=?,notes=? WHERE id=?",
                  (body.room_id, body.title.strip(), _when(body.starts_at), _when(body.ends_at), body.notes, session_id))
        out = session_out(c, c.execute("SELECT * FROM pr_sessions WHERE id=?", (session_id,)).fetchone())
    await _changed(old["event_id"], body.room_id)
    if old["room_id"] and old["room_id"] != body.room_id:
        await _changed(old["event_id"], old["room_id"])
    return out


@router.delete("/api/presenter/sessions/{session_id}")
async def delete_session(session_id: int, p: Principal = Depends(require_tech)):
    """Removes the session and its show files. Its presenters stay, unassigned."""
    with db.tx() as c:
        s = _session(c, session_id, p)
        paths = [r[0] for r in c.execute("SELECT stored_path FROM pr_show_files WHERE session_id=?", (session_id,))]
        c.execute("UPDATE pr_presenters SET session_id=NULL WHERE session_id=?", (session_id,))
        c.execute("DELETE FROM pr_sessions WHERE id=?", (session_id,))
    for path in paths:
        _remove(path)
    await _changed(s["event_id"], s["room_id"])
    return {"ok": True}


# -------------------------------------------------------------- presenters --
class PresenterIn(BaseModel):
    event_id: int
    session_id: int | None = None
    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=60)


def _presenter(c, presenter_id: int, p: Principal):
    pr = c.execute("SELECT * FROM pr_presenters WHERE id=?", (presenter_id,)).fetchone()
    if not pr:
        raise HTTPException(404, "Presenter not found")
    _event(c, pr["event_id"], p)
    return pr


def _room_of(c, session_id) -> int | None:
    r = c.execute("SELECT room_id FROM pr_sessions WHERE id=?", (session_id,)).fetchone() if session_id else None
    return r["room_id"] if r else None


def _check_presenter(c, body: PresenterIn, p: Principal) -> None:
    _event(c, body.event_id, p)
    if body.session_id is not None:
        s = c.execute("SELECT event_id FROM pr_sessions WHERE id=?", (body.session_id,)).fetchone()
        if not s or s["event_id"] != body.event_id:
            raise HTTPException(400, "That session isn't in this event")


@router.post("/api/presenter/presenters")
async def add_presenter(body: PresenterIn, p: Principal = Depends(require_tech)):
    with db.tx() as c:
        _check_presenter(c, body, p)
        pid = c.execute("INSERT INTO pr_presenters(event_id,session_id,full_name,email_enc,phone_enc,token,created_at) VALUES(?,?,?,?,?,?,?)",
                        (body.event_id, body.session_id, body.full_name.strip(), encrypt(body.email.strip()),
                         encrypt(body.phone.strip()), secrets.token_urlsafe(24), db.now_iso())).lastrowid
        out = presenter_out(c, c.execute("SELECT * FROM pr_presenters WHERE id=?", (pid,)).fetchone())
        room = _room_of(c, body.session_id)
    await _changed(body.event_id, room)
    return out


@router.put("/api/presenter/presenters/{presenter_id}")
async def edit_presenter(presenter_id: int, body: PresenterIn, p: Principal = Depends(require_tech)):
    with db.tx() as c:
        old = _presenter(c, presenter_id, p)
        if body.event_id != old["event_id"]:
            raise HTTPException(400, "A presenter can't move to another event")
        _check_presenter(c, body, p)
        c.execute("UPDATE pr_presenters SET session_id=?,full_name=?,email_enc=?,phone_enc=? WHERE id=?",
                  (body.session_id, body.full_name.strip(), encrypt(body.email.strip()), encrypt(body.phone.strip()), presenter_id))
        out = presenter_out(c, c.execute("SELECT * FROM pr_presenters WHERE id=?", (presenter_id,)).fetchone())
        rooms = {_room_of(c, body.session_id), _room_of(c, old["session_id"])}
    for room in rooms:
        await _changed(body.event_id, room)
    return out


@router.post("/api/presenter/presenters/{presenter_id}/new-link")
async def new_link(presenter_id: int, p: Principal = Depends(require_tech)):
    """Replace a presenter's portal link (the old one stops working)."""
    with db.tx() as c:
        pr = _presenter(c, presenter_id, p)
        c.execute("UPDATE pr_presenters SET token=? WHERE id=?", (secrets.token_urlsafe(24), presenter_id))
        db.audit(c, p.name, "presenter.new_link", pr["full_name"])
        out = presenter_out(c, c.execute("SELECT * FROM pr_presenters WHERE id=?", (presenter_id,)).fetchone())
    await _changed(pr["event_id"])
    return out


@router.post("/api/presenter/presenters/{presenter_id}/checkin")
async def staff_checkin(presenter_id: int, undo: bool = False, p: Principal = Depends(require_tech)):
    with db.tx() as c:
        pr = _presenter(c, presenter_id, p)
        c.execute("UPDATE pr_presenters SET checked_in_at=? WHERE id=?", (None if undo else db.now_iso(), presenter_id))
        room = _room_of(c, pr["session_id"])
    await _changed(pr["event_id"], room)
    return {"ok": True}


@router.delete("/api/presenter/presenters/{presenter_id}")
async def delete_presenter(presenter_id: int, p: Principal = Depends(require_tech)):
    with db.tx() as c:
        pr = _presenter(c, presenter_id, p)
        paths = [r[0] for r in c.execute("SELECT stored_path FROM pr_files WHERE presenter_id=?", (presenter_id,))]
        c.execute("DELETE FROM pr_presenters WHERE id=?", (presenter_id,))
        room = _room_of(c, pr["session_id"])
    for path in paths:
        _remove(path)
    await _changed(pr["event_id"], room)
    return {"ok": True}


async def _add_file(c_presenter, file: UploadFile, by: str) -> dict:
    with db.ro() as c:
        limit = int(prefs(c)["upload_limit_mb"])
    saved = await _save(file, f"presenters/{c_presenter['id']}", limit)
    with db.tx() as c:
        c.execute("INSERT INTO pr_files(presenter_id,original_name,stored_path,mime,size,sha256,review_status,review_note,uploaded_at,uploaded_by) "
                  "VALUES(?,?,?,?,?,?,'pending','',?,?)",
                  (c_presenter["id"], saved["name"], saved["path"], saved["mime"], saved["size"], saved["sha256"], db.now_iso(), by))
        room = _room_of(c, c_presenter["session_id"])
    await _changed(c_presenter["event_id"], room)
    return saved


@router.post("/api/presenter/presenters/{presenter_id}/files")
async def staff_upload(presenter_id: int, file: UploadFile, p: Principal = Depends(require_tech)):
    """The AV desk uploads a file for a presenter (from a USB stick, an email)."""
    with db.ro() as c:
        pr = _presenter(c, presenter_id, p)
    await _add_file(pr, file, p.name)
    with db.ro() as c:
        return presenter_out(c, c.execute("SELECT * FROM pr_presenters WHERE id=?", (presenter_id,)).fetchone())


# ------------------------------------------------------------- file review --
def _file(c, file_id: int, p: Principal):
    f = c.execute("SELECT f.*, pp.event_id, pp.session_id FROM pr_files f JOIN pr_presenters pp ON pp.id=f.presenter_id WHERE f.id=?",
                  (file_id,)).fetchone()
    if not f:
        raise HTTPException(404, "File not found")
    _event(c, f["event_id"], p)
    return f


@router.get("/api/presenter/files/{file_id}")
def get_file(file_id: int, p: Principal = Depends(require_tech)):
    with db.ro() as c:
        f = _file(c, file_id, p)
    return _send(f["stored_path"], f["original_name"], f["mime"])


class ReviewIn(BaseModel):
    status: str
    note: str = Field(default="", max_length=1000)


@router.put("/api/presenter/files/{file_id}/review")
async def review_file(file_id: int, body: ReviewIn, p: Principal = Depends(require_tech)):
    if body.status not in REVIEW:
        raise HTTPException(400, "Status must be pending, approved or rejected")
    with db.tx() as c:
        f = _file(c, file_id, p)
        c.execute("UPDATE pr_files SET review_status=?,review_note=?,reviewed_at=?,reviewed_by=? WHERE id=?",
                  (body.status, body.note.strip(), db.now_iso(), p.name, file_id))
        room = _room_of(c, f["session_id"])
    await _changed(f["event_id"], room)
    return {"ok": True}


@router.get("/api/presenter/events/{event_id}/review")
def review_queue(event_id: int, p: Principal = Depends(require_tech)):
    """Every upload across the event, newest first, with its version number."""
    with db.ro() as c:
        _event(c, event_id, p)
        rows = db.rows(c.execute(
            "SELECT f.*, pp.full_name AS presenter_name, pp.session_id, s.title AS session_title, s.starts_at, r.name AS room_name "
            "FROM pr_files f JOIN pr_presenters pp ON pp.id=f.presenter_id LEFT JOIN pr_sessions s ON s.id=pp.session_id "
            "LEFT JOIN rooms r ON r.id=s.room_id WHERE pp.event_id=? ORDER BY f.id", (event_id,)))
    seen: dict[int, int] = {}
    latest: dict[int, int] = {}
    for f in rows:
        seen[f["presenter_id"]] = f["version"] = seen.get(f["presenter_id"], 0) + 1
        latest[f["presenter_id"]] = f["id"]
        f.pop("stored_path", None)
    for f in rows:
        f["is_latest"] = latest[f["presenter_id"]] == f["id"]
    return sorted(rows, key=lambda f: f["id"], reverse=True)


# -------------------------------------------------------------- show files --
def _show(c, show_id: int, p: Principal):
    f = c.execute("SELECT f.*, s.event_id, s.room_id FROM pr_show_files f JOIN pr_sessions s ON s.id=f.session_id WHERE f.id=?",
                  (show_id,)).fetchone()
    if not f:
        raise HTTPException(404, "Show file not found")
    _event(c, f["event_id"], p)
    return f


@router.post("/api/presenter/sessions/{session_id}/show-files")
async def add_show_file(session_id: int, file: UploadFile, kind: str = "presentation", label: str = "",
                        p: Principal = Depends(require_tech)):
    """The show's own files for a session (walk-in video, stings, the final
    deck), in running order. Separate from what the presenter uploaded."""
    if kind not in SHOW_KINDS:
        raise HTTPException(400, f"Kind must be one of {', '.join(SHOW_KINDS)}")
    with db.ro() as c:
        s = _session(c, session_id, p)
        limit = int(prefs(c)["upload_limit_mb"])
    saved = await _save(file, f"show/{session_id}", limit)
    with db.tx() as c:
        pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM pr_show_files WHERE session_id=?", (session_id,)).fetchone()[0]
        cur = c.execute("INSERT INTO pr_show_files(session_id,kind,label,original_name,stored_path,mime,size,sha256,position,created_at,created_by) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (session_id, kind, label.strip()[:120] or saved["name"], saved["name"], saved["path"], saved["mime"], saved["size"],
                   saved["sha256"], pos, db.now_iso(), p.name))
    await _changed(s["event_id"], s["room_id"])
    return {"ok": True, "id": cur.lastrowid}


class ShowEdit(BaseModel):
    label: str = Field(default="", max_length=120)
    kind: str = "presentation"


@router.put("/api/presenter/show-files/{show_id}")
async def edit_show_file(show_id: int, body: ShowEdit, p: Principal = Depends(require_tech)):
    if body.kind not in SHOW_KINDS:
        raise HTTPException(400, "Unknown kind")
    with db.tx() as c:
        f = _show(c, show_id, p)
        c.execute("UPDATE pr_show_files SET label=?, kind=? WHERE id=?", (body.label.strip() or f["original_name"], body.kind, show_id))
    await _changed(f["event_id"], f["room_id"])
    return {"ok": True}


class OrderIn(BaseModel):
    ids: list[int]


@router.post("/api/presenter/sessions/{session_id}/show-files/order")
async def order_show_files(session_id: int, body: OrderIn, p: Principal = Depends(require_tech)):
    with db.tx() as c:
        s = _session(c, session_id, p)
        have = {r[0] for r in c.execute("SELECT id FROM pr_show_files WHERE session_id=?", (session_id,))}
        if set(body.ids) != have:
            raise HTTPException(400, "Send every show file of the session, in the new order")
        for i, fid in enumerate(body.ids):
            c.execute("UPDATE pr_show_files SET position=? WHERE id=?", (i, fid))
    await _changed(s["event_id"], s["room_id"])
    return {"ok": True}


@router.get("/api/presenter/show-files/{show_id}")
def get_show_file(show_id: int, p: Principal = Depends(require_tech)):
    with db.ro() as c:
        f = _show(c, show_id, p)
    return _send(f["stored_path"], f["original_name"], f["mime"])


@router.delete("/api/presenter/show-files/{show_id}")
async def delete_show_file(show_id: int, p: Principal = Depends(require_tech)):
    with db.tx() as c:
        f = _show(c, show_id, p)
        c.execute("DELETE FROM pr_show_files WHERE id=?", (show_id,))
    _remove(f["stored_path"])
    await _changed(f["event_id"], f["room_id"])
    return {"ok": True}


# ------------------------------------------------------------ room schedule --
def _room_sessions(c, room_id: int) -> list[dict]:
    return [session_out(c, s) for s in c.execute(
        "SELECT s.* FROM pr_sessions s JOIN pr_events e ON e.id=s.event_id WHERE s.room_id=? AND e.archived=0 "
        "ORDER BY s.starts_at='', s.starts_at, s.sort, s.id", (room_id,))]


@router.get("/api/presenter/rooms/{room_id}/schedule")
def room_schedule(room_id: int, p: Principal = Depends(require_tech)):
    """One room's sessions across the current events, for the tech workspace."""
    with db.ro() as c:
        room_or_404(c, room_id, p)
        return _room_sessions(c, room_id)


class ToTimerIn(BaseModel):
    day: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    replace: bool = True


@router.post("/api/presenter/rooms/{room_id}/to-timer")
async def to_timer(room_id: int, body: ToTimerIn, p: Principal = Depends(require_tech)):
    """Turn the room's sessions (for one day) into its timer cue list: one cue
    per session, timed from its start and end, the presenter in the note."""
    from . import timers

    with db.tx() as c:
        room_or_404(c, room_id, p)
        sessions = [s for s in _room_sessions(c, room_id) if not body.day or s["starts_at"].startswith(body.day)]
        if not sessions:
            raise HTTPException(400, "No sessions in this room for that day")
        if body.replace:
            c.execute("DELETE FROM cues WHERE room_id=?", (room_id,))
            c.execute("UPDATE timers SET cue_id=NULL WHERE room_id=?", (room_id,))
        for i, s in enumerate(sessions[:500], start=1):
            dur = 0
            try:
                if s["starts_at"] and s["ends_at"]:
                    dur = max(0, int((datetime.fromisoformat(s["ends_at"]) - datetime.fromisoformat(s["starts_at"])).total_seconds() * 1000))
            except ValueError:
                pass
            start = s["starts_at"][11:16] if "T" in s["starts_at"] else s["starts_at"][:5]
            note = ", ".join(pr["full_name"] for pr in s["presenters"])
            timers._insert_cue(c, room_id, timers.CueIn(cue=str(i), title=s["title"][:200], note=note[:2000],
                                                         duration_ms=min(dur, timers.MAX_DURATION),
                                                         time_start=start if re.match(r"^\d{2}:\d{2}$", start) else "",
                                                         end_action="load-next"))
        db.audit(c, p.name, "presenter.to_timer", f"room {room_id}: {len(sessions)} sessions")
    await timers._cues_changed(room_id)
    return {"cues": min(len(sessions), 500)}


# ----------------------------------------------------------------- imports --
@router.post("/api/presenter/events/{event_id}/import")
async def import_schedule(event_id: int, file: UploadFile, p: Principal = Depends(require_tech)):
    """Read a running order. Nothing is saved until the rows are committed."""
    data = await file.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "Schedule files can be up to 20 MB")
    with db.ro() as c:
        e = _event(c, event_id, p)
        cfg = prefs(c)
        rooms = [r[0] for r in c.execute("SELECT name FROM rooms WHERE site_id=? AND enabled=1", (e["site_id"],))]
    name = os.path.basename(file.filename or "schedule")[:200]
    import asyncio

    try:
        rows, method = await asyncio.to_thread(schedule.parse, name, data, event=e["name"], starts=e["starts_on"], rooms=rooms,
                                               ai_url=cfg["ai_url"], ai_model=cfg["ai_model"])
    except schedule.ScheduleError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception as exc:  # a damaged file shouldn't be a 500
        raise HTTPException(422, f"Couldn't read that file ({exc.__class__.__name__})") from None
    with db.tx() as c:
        iid = c.execute("INSERT INTO pr_imports(event_id,filename,method,rows_json,status,created_by,created_at) VALUES(?,?,?,?,'review',?,?)",
                        (event_id, name, method, json.dumps(rows), p.name, db.now_iso())).lastrowid
    known = {r.lower() for r in rooms}
    for r in rows:
        r["room_known"] = not r["room_name"] or r["room_name"].lower() in known
    return {"id": iid, "method": method, "rows": rows}


class ImportRow(BaseModel):
    room_name: str = ""
    title: str = ""
    starts_at: str = ""
    ends_at: str = ""
    presenter_name: str = ""
    presenter_email: str = ""
    presenter_phone: str = ""


class CommitIn(BaseModel):
    rows: list[ImportRow] = Field(max_length=2000)


@router.post("/api/presenter/imports/{import_id}/commit")
async def commit_import(import_id: int, body: CommitIn, p: Principal = Depends(require_tech)):
    with db.tx() as c:
        imp = c.execute("SELECT * FROM pr_imports WHERE id=?", (import_id,)).fetchone()
        if not imp:
            raise HTTPException(404, "Import not found")
        e = _event(c, imp["event_id"], p)
        if imp["status"] != "review":
            raise HTTPException(409, "This import has already been used")
        rooms = {r["name"].strip().lower(): r["id"] for r in c.execute("SELECT id,name FROM rooms WHERE site_id=?", (e["site_id"],))}
        unknown, created = set(), 0
        for row in body.rows:
            if not row.title.strip():
                continue
            room_id = rooms.get(row.room_name.strip().lower())
            if row.room_name.strip() and room_id is None:
                unknown.add(row.room_name.strip())
            try:
                start, end = _when(row.starts_at), _when(row.ends_at)
            except HTTPException:
                start, end = "", ""
            sid = c.execute("INSERT INTO pr_sessions(event_id,room_id,title,starts_at,ends_at,notes,sort,created_at) VALUES(?,?,?,?,?,'',0,?)",
                            (e["id"], room_id, row.title.strip()[:200], start, end, db.now_iso())).lastrowid
            for name in [n.strip() for n in re.split(r"\s*(?:;|&| and )\s*", row.presenter_name) if n.strip()][:10]:
                c.execute("INSERT INTO pr_presenters(event_id,session_id,full_name,email_enc,phone_enc,token,created_at) VALUES(?,?,?,?,?,?,?)",
                          (e["id"], sid, name[:120], encrypt(row.presenter_email.strip()[:200]), encrypt(row.presenter_phone.strip()[:60]),
                           secrets.token_urlsafe(24), db.now_iso()))
            created += 1
        c.execute("UPDATE pr_imports SET status='committed', committed_at=? WHERE id=?", (db.now_iso(), import_id))
        db.audit(c, p.name, "presenter.import", f"{e['name']}: {created} sessions from {imp['filename']}")
    await _changed(e["id"])
    return {"sessions": created, "unknown_rooms": sorted(unknown)}


@router.post("/api/presenter/imports/{import_id}/discard")
def discard_import(import_id: int, p: Principal = Depends(require_tech)):
    with db.tx() as c:
        imp = c.execute("SELECT event_id FROM pr_imports WHERE id=?", (import_id,)).fetchone()
        if not imp:
            raise HTTPException(404, "Import not found")
        _event(c, imp["event_id"], p)
        c.execute("UPDATE pr_imports SET status='discarded' WHERE id=? AND status='review'", (import_id,))
    return {"ok": True}


# ---------------------------------------------------------------- settings --
class PrefsIn(BaseModel):
    upload_limit_mb: int = Field(default=2048, ge=1, le=20000)
    ai_url: str = Field(default="", max_length=300, pattern=r"^$|^https?://\S+$")
    ai_model: str = Field(default="llama3.1:8b", max_length=100)
    portal_note: str = Field(default="", max_length=1000)


@router.get("/api/presenter/settings")
def get_prefs(p: Principal = Depends(require_admin)):
    with db.ro() as c:
        out = prefs(c)
        out["rooms"] = db.rows(c.execute(
            "SELECT r.id, r.name, s.name AS site, r.sync_code FROM rooms r JOIN sites s ON s.id=r.site_id WHERE r.enabled=1 ORDER BY s.id, r.sort, r.name"))
    out["storage"] = str(files_root())
    return out


@router.put("/api/presenter/settings")
def set_prefs(body: PrefsIn, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        db.set_setting(c, "presenter", body.model_dump())
        db.audit(c, p.name, "presenter.settings")
    return {"ok": True}


def _new_code() -> str:
    return f"{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"


@router.post("/api/presenter/rooms/{room_id}/sync-code")
def new_sync_code(room_id: int, p: Principal = Depends(require_admin)):
    """A room's code for the sync tool on a presentation laptop that isn't an
    AT-SUIT node. Making a new one stops the old one."""
    with db.tx() as c:
        room_or_404(c, room_id, p)
        for _ in range(5):
            code = _new_code()
            if not c.execute("SELECT 1 FROM rooms WHERE sync_code=?", (code,)).fetchone():
                break
        c.execute("UPDATE rooms SET sync_code=? WHERE id=?", (code, room_id))
        db.audit(c, p.name, "presenter.sync_code", str(room_id))
    return {"sync_code": code}


# -------------------------------------------------------------- room sync --
def _sync_room(c, request: Request, code: str | None):
    """A room by its sync code, or the calling node's room."""
    if code:
        room = c.execute("SELECT * FROM rooms WHERE sync_code=? AND enabled=1", (code.strip().upper(),)).fetchone()
        if not room or not code.strip():
            raise HTTPException(404, "Unknown room code")
        return room
    p = principal(request)
    if not p or p.kind != "node":
        raise HTTPException(401, "Room code or node sign-in required")
    from .fleet import current_room_id

    n = c.execute("SELECT * FROM nodes WHERE id=?", (p.id,)).fetchone()
    rid = current_room_id(c, n)
    room = c.execute("SELECT * FROM rooms WHERE id=?", (rid,)).fetchone() if rid else None
    if not room:
        raise HTTPException(409, "Pick a room on this laptop first")
    return room


def _manifest(c, room) -> dict:
    files, show = [], []
    for s in _room_sessions(c, room["id"]):
        for pr in s["presenters"]:
            f = next((f for f in pr["files"] if f["review_status"] == "approved"), None)
            if f:
                files.append({"session_id": s["id"], "session_title": s["title"], "starts_at": s["starts_at"],
                              "presenter": pr["full_name"], "file_id": f["id"], "original_name": f["original_name"],
                              "sha256": f["sha256"], "size": f["size"]})
        for f in s["show_files"]:
            show.append({"session_id": s["id"], "session_title": s["title"], "starts_at": s["starts_at"], "show_id": f["id"],
                         "kind": f["kind"], "label": f["label"], "position": f["position"], "original_name": f["original_name"],
                         "sha256": f["sha256"], "size": f["size"]})
    return {"room_id": room["id"], "room_name": room["name"], "files": files, "show_files": show}


@router.get("/api/presenter/sync")
@router.get("/api/presenter/sync/{code}")
def sync_manifest(request: Request, code: str | None = None):
    """What a presentation laptop should have on disk: each session's latest
    approved presenter file and its show files."""
    with db.ro() as c:
        return _manifest(c, _sync_room(c, request, code))


def _sync_file(request: Request, code: str | None, kind: str, item_id: int):
    with db.ro() as c:
        room = _sync_room(c, request, code)
        m = _manifest(c, room)
        if kind == "files" and any(f["file_id"] == item_id for f in m["files"]):
            f = c.execute("SELECT * FROM pr_files WHERE id=?", (item_id,)).fetchone()
        elif kind == "show" and any(f["show_id"] == item_id for f in m["show_files"]):
            f = c.execute("SELECT * FROM pr_show_files WHERE id=?", (item_id,)).fetchone()
        else:
            raise HTTPException(404, "Not a file for this room")
    return _send(f["stored_path"], f["original_name"], f["mime"])


@router.get("/api/presenter/sync/{code}/{kind}/{item_id}")
def sync_file_by_code(code: str, kind: str, item_id: int, request: Request):
    return _sync_file(request, code, kind, item_id)


@router.get("/api/presenter/sync-node/{kind}/{item_id}")
def sync_file_by_node(kind: str, item_id: int, request: Request, p: Principal = Depends(require_node)):
    return _sync_file(request, None, kind, item_id)


@router.get("/api/presenter/room-sync/atsuit_room_sync.py")
def sync_tool():
    here = Path(__file__).resolve()
    for f in (here.parents[1] / "agent" / "atsuit_room_sync.py", here.parents[3] / "room-sync" / "atsuit_room_sync.py"):
        if f.is_file():
            return FileResponse(f, media_type="text/plain; charset=utf-8", filename="atsuit_room_sync.py")
    raise HTTPException(404, "Not on this server")


# ---------------------------------------------------------- presenter portal --
def _by_token(c, token: str):
    pr = c.execute("SELECT * FROM pr_presenters WHERE token=?", (token,)).fetchone() if len(token) >= 20 else None
    e = c.execute("SELECT * FROM pr_events WHERE id=?", (pr["event_id"],)).fetchone() if pr else None
    if not pr or not e or e["archived"]:
        raise HTTPException(404, "This link isn't valid. Ask the event team for a new one.")
    return pr, e


def _portal(c, pr, e) -> dict:
    s = c.execute("SELECT * FROM pr_sessions WHERE id=?", (pr["session_id"],)).fetchone() if pr["session_id"] else None
    room = c.execute("SELECT name FROM rooms WHERE id=?", (s["room_id"],)).fetchone() if s and s["room_id"] else None
    files = db.rows(c.execute("SELECT id, original_name, size, review_status, review_note, uploaded_at FROM pr_files "
                              "WHERE presenter_id=? ORDER BY id DESC", (pr["id"],)))
    from .core import get_branding

    cfg = prefs(c)
    return {"full_name": pr["full_name"], "checked_in_at": pr["checked_in_at"], "event": e["name"], "colour": e["colour"],
            "session": {"title": s["title"], "starts_at": s["starts_at"], "ends_at": s["ends_at"], "room": room["name"] if room else ""} if s else None,
            "files": files, "upload_limit_mb": cfg["upload_limit_mb"], "note": cfg["portal_note"], "branding": get_branding(c)}


@router.get("/api/present/{token}")
def portal(token: str):
    with db.ro() as c:
        return _portal(c, *_by_token(c, token))


@router.post("/api/present/{token}/upload")
async def portal_upload(token: str, file: UploadFile):
    with db.ro() as c:
        pr, e = _by_token(c, token)
    await _add_file(pr, file, f"presenter:{pr['full_name']}")
    with db.ro() as c:
        return _portal(c, pr, e)


@router.post("/api/present/{token}/checkin")
async def portal_checkin(token: str):
    with db.tx() as c:
        pr, e = _by_token(c, token)
        if not pr["checked_in_at"]:
            c.execute("UPDATE pr_presenters SET checked_in_at=? WHERE id=?", (db.now_iso(), pr["id"]))
        room = _room_of(c, pr["session_id"])
    await _changed(pr["event_id"], room)
    with db.ro() as c:
        return _portal(c, *_by_token(c, token))
