"""Crew chat: a channel per site and per room, DMs, attachments, help requests.
Replaces AT-RoomComms."""
from __future__ import annotations

import mimetypes
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import config, db
from ..hub import hub
from ..security import Principal, decrypt, encrypt, require_tech, require_user
from .core import require_module, room_or_404, site_ok

router = APIRouter(dependencies=[Depends(require_module("comms"))])

PRIORITIES = ("normal", "important", "urgent")
HELP_STATUSES = ("open", "acknowledged", "resolved")


def channel_topic(ch) -> str:
    if ch["kind"] == "room":
        return f"room:{ch['room_id']}"
    if ch["kind"] == "site":
        return f"site:{ch['site_id']}"
    return f"dmchan:{ch['id']}"


def dm_members(ch) -> list[int]:
    return [int(x) for x in (ch["dm_key"] or "").split(":") if x]


def can_see(p: Principal, ch) -> bool:
    if ch["kind"] == "dm":
        return p.kind == "account" and p.id in dm_members(ch)
    return site_ok(p, ch["site_id"])


def get_channel(c, channel_id: int, p: Principal):
    ch = c.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
    if not ch or not can_see(p, ch):
        raise HTTPException(404, "Channel not found")
    return ch


def message_out(c, m) -> dict:
    atts = db.rows(c.execute("SELECT id,original_name,mime,size FROM attachments WHERE message_id=?", (m["id"],)))
    reads = c.execute("SELECT COUNT(*) FROM message_reads WHERE message_id=?", (m["id"],)).fetchone()[0]
    return {
        "id": m["id"], "channel_id": m["channel_id"], "sender_id": m["sender_id"], "sender_name": m["sender_name"],
        "body": "" if m["deleted_at"] else decrypt(m["body_enc"]), "priority": m["priority"],
        "created_at": m["created_at"], "edited_at": m["edited_at"], "deleted": bool(m["deleted_at"]),
        "attachments": atts, "reads": reads,
    }


async def publish_channel(ch, type_: str, data) -> None:
    if ch["kind"] == "dm":
        for member in dm_members(ch):
            await hub.publish(f"dm:{member}", type_, data)
    else:
        await hub.publish(channel_topic(ch), type_, data)


@router.get("/api/comms/channels")
def list_channels(p: Principal = Depends(require_user)):
    with db.ro() as c:
        chans = db.rows(c.execute(
            "SELECT ch.*, r.sort AS room_sort FROM channels ch LEFT JOIN rooms r ON r.id=ch.room_id "
            "ORDER BY ch.kind DESC, r.sort, ch.name"))
        out = []
        for ch in chans:
            if not can_see(p, ch):
                continue
            if ch["kind"] == "dm":
                other = [m for m in dm_members(ch) if m != p.id]
                a = c.execute("SELECT display_name FROM accounts WHERE id=?", (other[0] if other else p.id,)).fetchone()
                ch["name"] = a["display_name"] if a else "Direct message"
            out.append(ch)
        return out


@router.get("/api/comms/people")
def people(p: Principal = Depends(require_user)):
    with db.ro() as c:
        rows = db.rows(c.execute("SELECT id,display_name,role,site_id FROM accounts WHERE active=1 ORDER BY display_name"))
    return [r for r in rows if r["id"] != p.id and (p.site_id is None or r["site_id"] in (None, p.site_id))]


class DmIn(BaseModel):
    account_id: int


@router.post("/api/comms/dm")
def open_dm(body: DmIn, p: Principal = Depends(require_user)):
    if p.kind != "account":
        raise HTTPException(400, "Sign in as a person to send direct messages")
    if body.account_id == p.id:
        raise HTTPException(400, "That's you")
    key = ":".join(str(x) for x in sorted((p.id, body.account_id)))
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM accounts WHERE id=? AND active=1", (body.account_id,)).fetchone():
            raise HTTPException(404, "Person not found")
        ch = c.execute("SELECT id FROM channels WHERE dm_key=?", (key,)).fetchone()
        if ch:
            return {"id": ch["id"]}
        cur = c.execute("INSERT INTO channels(site_id,room_id,kind,name,dm_key) VALUES(NULL,NULL,'dm','',?)", (key,))
        return {"id": cur.lastrowid}


@router.get("/api/comms/channels/{channel_id}/messages")
def list_messages(channel_id: int, before: int | None = None, limit: int = 100, p: Principal = Depends(require_user)):
    with db.ro() as c:
        get_channel(c, channel_id, p)
        q = "SELECT * FROM messages WHERE channel_id=?"
        args: list = [channel_id]
        if before:
            q += " AND id<?"
            args.append(before)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(min(max(limit, 1), 500))
        msgs = c.execute(q, args).fetchall()
        return [message_out(c, m) for m in reversed(msgs)]


class MessageIn(BaseModel):
    body: str = Field(default="", max_length=8000)
    priority: str = "normal"


@router.post("/api/comms/channels/{channel_id}/messages")
async def post_message(channel_id: int, body: MessageIn, p: Principal = Depends(require_tech)):
    if body.priority not in PRIORITIES:
        raise HTTPException(400, "Unknown priority")
    with db.tx() as c:
        ch = get_channel(c, channel_id, p)
        cur = c.execute(
            "INSERT INTO messages(channel_id,sender_id,sender_name,body_enc,priority,created_at) VALUES(?,?,?,?,?,?)",
            (channel_id, p.id if p.kind == "account" else None, p.name, encrypt(body.body.strip()), body.priority, db.now_iso()),
        )
        m = message_out(c, c.execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone())
    await publish_channel(ch, "message.new", m)
    return m


@router.delete("/api/comms/messages/{message_id}")
async def delete_message(message_id: int, p: Principal = Depends(require_tech)):
    with db.tx() as c:
        m = c.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not m:
            raise HTTPException(404, "Message not found")
        ch = get_channel(c, m["channel_id"], p)
        if not (p.role == "admin" or (p.kind == "account" and m["sender_id"] == p.id)):
            raise HTTPException(403, "You can only delete your own messages")
        c.execute("UPDATE messages SET deleted_at=?, body_enc='' WHERE id=?", (db.now_iso(), message_id))
    await publish_channel(ch, "message.deleted", {"id": message_id, "channel_id": ch["id"]})
    return {"ok": True}


class ReadIn(BaseModel):
    up_to: int


@router.post("/api/comms/channels/{channel_id}/read")
def mark_read(channel_id: int, body: ReadIn, p: Principal = Depends(require_user)):
    if p.kind != "account":
        return {"ok": True}
    with db.tx() as c:
        get_channel(c, channel_id, p)
        c.execute(
            "INSERT OR IGNORE INTO message_reads(message_id,account_id,read_at) "
            "SELECT id,?,? FROM messages WHERE channel_id=? AND id<=?",
            (p.id, db.now_iso(), channel_id, body.up_to),
        )
    return {"ok": True}


@router.post("/api/comms/messages/{message_id}/attachments")
async def upload_attachment(message_id: int, file: UploadFile, p: Principal = Depends(require_tech)):
    data = await file.read(config.cfg.max_upload + 1)
    if len(data) > config.cfg.max_upload:
        raise HTTPException(413, "File is too large")
    with db.tx() as c:
        m = c.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not m:
            raise HTTPException(404, "Message not found")
        ch = get_channel(c, m["channel_id"], p)
        if p.kind == "account" and m["sender_id"] != p.id:
            raise HTTPException(403, "Attach files to your own messages")
        stored = uuid.uuid4().hex
        (config.cfg.uploads / stored).write_bytes(data)
        name = (file.filename or "file").replace("/", "_").replace("\\", "_")[:200]
        mime = file.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        c.execute("INSERT INTO attachments(message_id,original_name,stored_name,mime,size) VALUES(?,?,?,?,?)",
                  (message_id, name, stored, mime, len(data)))
        out = message_out(c, m)
    await publish_channel(ch, "message.updated", out)
    return out


@router.get("/api/comms/attachments/{attachment_id}")
def download_attachment(attachment_id: int, p: Principal = Depends(require_user)):
    with db.ro() as c:
        a = c.execute(
            "SELECT a.*, m.channel_id FROM attachments a JOIN messages m ON m.id=a.message_id WHERE a.id=?",
            (attachment_id,)).fetchone()
        if not a:
            raise HTTPException(404, "Attachment not found")
        get_channel(c, a["channel_id"], p)
    path = config.cfg.uploads / a["stored_name"]
    if not path.exists():
        raise HTTPException(404, "File is missing")
    return FileResponse(path, media_type=a["mime"], filename=a["original_name"])


# ------------------------------------------------------------------- help --
class HelpIn(BaseModel):
    room_id: int
    category: str = Field(default="general", max_length=40)
    description: str = Field(default="", max_length=2000)
    priority: str = "normal"


@router.post("/api/comms/help")
async def request_help(body: HelpIn, p: Principal = Depends(require_tech)):
    if body.priority not in PRIORITIES:
        raise HTTPException(400, "Unknown priority")
    with db.tx() as c:
        r = room_or_404(c, body.room_id, p)
        cur = c.execute(
            "INSERT INTO help_requests(site_id,room_id,room_name,requested_by,category,description,priority,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (r["site_id"], r["id"], r["name"], p.name, body.category, body.description, body.priority, db.now_iso()),
        )
        h = dict(c.execute("SELECT * FROM help_requests WHERE id=?", (cur.lastrowid,)).fetchone())
    await hub.publish(f"site:{r['site_id']}", "help.new", h)
    return h


@router.get("/api/comms/help")
def list_help(status: str | None = None, p: Principal = Depends(require_user)):
    with db.ro() as c:
        q, args = "SELECT * FROM help_requests", []
        if status:
            q += " WHERE status=?"
            args.append(status)
        rows = db.rows(c.execute(q + " ORDER BY id DESC LIMIT 200", args))
    return [h for h in rows if site_ok(p, h["site_id"])]


class HelpUpdate(BaseModel):
    status: str


@router.put("/api/comms/help/{help_id}")
async def update_help(help_id: int, body: HelpUpdate, p: Principal = Depends(require_tech)):
    if body.status not in HELP_STATUSES:
        raise HTTPException(400, "Unknown status")
    with db.tx() as c:
        h = c.execute("SELECT * FROM help_requests WHERE id=?", (help_id,)).fetchone()
        if not h or not site_ok(p, h["site_id"]):
            raise HTTPException(404, "Help request not found")
        col = {"acknowledged": "acknowledged_at", "resolved": "resolved_at"}.get(body.status)
        c.execute("UPDATE help_requests SET status=?, assigned_to=? WHERE id=?", (body.status, p.name, help_id))
        if col:
            c.execute(f"UPDATE help_requests SET {col}=? WHERE id=?", (db.now_iso(), help_id))
        out = dict(c.execute("SELECT * FROM help_requests WHERE id=?", (help_id,)).fetchone())
    await hub.publish(f"site:{out['site_id']}", "help.updated", out)
    return out
