"""Handover notes: what one tech leaves for the next in a room ("clicker 2 needs batteries",
"the client wants the lectern mic"). A note stays until someone ticks it off, so it carries over
between shifts and days. Pinned notes are standing info for the room and stay at the top."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..hub import hub
from ..security import Principal, decrypt, encrypt, require_tech
from .core import room_or_404

router = APIRouter()

DONE_SHOWN_DAYS = 7  # ticked-off notes stay visible (greyed) this long, so a mistake can be undone
DONE_KEPT_DAYS = 30  # and are deleted after this long (main.housekeeping)


def note_out(n) -> dict:
    return {
        "id": n["id"], "room_id": n["room_id"], "body": decrypt(n["body_enc"]), "pinned": bool(n["pinned"]),
        "author": n["author"], "created_at": n["created_at"], "edited_at": n["edited_at"],
        "done": bool(n["done_at"]), "done_at": n["done_at"], "done_by": n["done_by"],
    }


def room_for(c, room_id: int, p: Principal):
    """The room, if this person may see its notes. A tech laptop only sees the room it is in today."""
    r = room_or_404(c, room_id, p)
    if p.kind == "node" and p.room_id and p.room_id != r["id"]:
        raise HTTPException(403, "This laptop is in another room today")
    return r


def note_or_404(c, note_id: int, p: Principal):
    n = c.execute("SELECT * FROM room_notes WHERE id=?", (note_id,)).fetchone()
    if not n:
        raise HTTPException(404, "Note not found")
    room_for(c, n["room_id"], p)
    return n


async def changed(room_id: int) -> None:
    await hub.publish(f"room:{room_id}", "notes.changed", {"room_id": room_id})


@router.get("/api/rooms/{room_id}/notes")
def list_notes(room_id: int, p: Principal = Depends(require_tech)):
    since = (datetime.now(timezone.utc) - timedelta(days=DONE_SHOWN_DAYS)).isoformat(timespec="seconds")
    with db.ro() as c:
        room_for(c, room_id, p)
        open_ = c.execute("SELECT * FROM room_notes WHERE room_id=? AND done_at IS NULL ORDER BY pinned DESC, id DESC",
                          (room_id,)).fetchall()
        done = c.execute("SELECT * FROM room_notes WHERE room_id=? AND done_at>=? ORDER BY done_at DESC LIMIT 20",
                         (room_id, since)).fetchall()
        return {"open": [note_out(n) for n in open_], "done": [note_out(n) for n in done]}


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    pinned: bool = False


@router.post("/api/rooms/{room_id}/notes")
async def add_note(room_id: int, body: NoteIn, p: Principal = Depends(require_tech)):
    text = body.body.strip()
    if not text:
        raise HTTPException(400, "Write the note first")
    with db.tx() as c:
        room_for(c, room_id, p)
        cur = c.execute("INSERT INTO room_notes(room_id,body_enc,pinned,author,created_at) VALUES(?,?,?,?,?)",
                        (room_id, encrypt(text), int(body.pinned), p.name, db.now_iso()))
        out = note_out(c.execute("SELECT * FROM room_notes WHERE id=?", (cur.lastrowid,)).fetchone())
    await changed(room_id)
    return out


class NoteUpdate(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=2000)
    pinned: bool | None = None
    done: bool | None = None


@router.put("/api/notes/{note_id}")
async def update_note(note_id: int, body: NoteUpdate, p: Principal = Depends(require_tech)):
    with db.tx() as c:
        n = note_or_404(c, note_id, p)
        if body.body is not None and body.body.strip():
            c.execute("UPDATE room_notes SET body_enc=?, edited_at=? WHERE id=?", (encrypt(body.body.strip()), db.now_iso(), note_id))
        if body.pinned is not None:
            c.execute("UPDATE room_notes SET pinned=? WHERE id=?", (int(body.pinned), note_id))
        if body.done is True and not n["done_at"]:
            c.execute("UPDATE room_notes SET done_at=?, done_by=? WHERE id=?", (db.now_iso(), p.name, note_id))
        elif body.done is False:
            c.execute("UPDATE room_notes SET done_at=NULL, done_by=NULL WHERE id=?", (note_id,))
        out = note_out(c.execute("SELECT * FROM room_notes WHERE id=?", (note_id,)).fetchone())
    await changed(out["room_id"])
    return out


@router.delete("/api/notes/{note_id}")
async def delete_note(note_id: int, p: Principal = Depends(require_tech)):
    with db.tx() as c:
        n = note_or_404(c, note_id, p)
        c.execute("DELETE FROM room_notes WHERE id=?", (note_id,))
        db.audit(c, p.name, "note.delete", f"room {n['room_id']}, note {note_id}")
    await changed(n["room_id"])
    return {"ok": True}
