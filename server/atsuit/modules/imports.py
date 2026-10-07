"""Bring data across from the old apps. Every importer is idempotent and
returns a report of what it took and what it skipped."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, UploadFile

from .. import db
from ..security import Principal, encrypt, require_admin
from .core import create_room

router = APIRouter()
MAX_IMPORT = 200 * 1024 * 1024


def _room_id(c, site_id: int, name: str, created: list) -> int:
    name = name.strip()
    r = c.execute("SELECT id FROM rooms WHERE site_id=? AND (name=? COLLATE NOCASE OR short_name=? COLLATE NOCASE)",
                  (site_id, name, name)).fetchone()
    if r:
        return r["id"]
    created.append(name)
    return create_room(c, site_id, name)


def _site(c, site_id: int | None) -> int:
    r = c.execute("SELECT id FROM sites WHERE id=?", (site_id,)).fetchone() if site_id else \
        c.execute("SELECT id FROM sites ORDER BY id LIMIT 1").fetchone()
    if not r:
        raise HTTPException(404, "Site not found")
    return r["id"]


def import_rooms_txt(text: str, site_id: int | None = None) -> dict:
    """Device Suite rooms.txt: `ROOM|URL|LABEL;` rows. Anything else is skipped."""
    report = {"links": 0, "rooms_created": [], "skipped": [], "duplicates": 0}
    with db.tx() as c:
        sid = _site(c, site_id)
        for raw in text.replace("\r", "").split(";"):
            row = raw.strip()
            if not row:
                continue
            parts = row.split("|")
            if len(parts) != 3 or not parts[1].strip().lower().startswith(("http://", "https://")) or "\n" in row:
                report["skipped"].append(row[:80])
                continue
            room, url, label = (p.strip() for p in parts)
            rid = _room_id(c, sid, room, report["rooms_created"])
            kind = "buttons" if "/emulator/" in url else "timer" if ":40" in url else "kiosk"
            if c.execute("SELECT 1 FROM links WHERE room_id=? AND url=?", (rid, url)).fetchone():
                report["duplicates"] += 1
                continue
            c.execute("INSERT INTO links(site_id,room_id,board,label,url,kind,sort) VALUES(?,?,?,?,?,?,?)",
                      (sid, rid, "public", label, url, kind, report["links"]))
            report["links"] += 1
        db.audit(c, "import", "import.rooms_txt", json.dumps({k: v if isinstance(v, int) else len(v) for k, v in report.items()}))
    report["skipped_count"] = len(report["skipped"])
    report["skipped"] = report["skipped"][:20]
    return report


def import_homarr_tsv(text: str, site_id: int | None = None) -> dict:
    """Homarr link export (BOARD/ITEM, NAME, HREF, PING_URL). Links go on the
    public board; IP-scanner, CasaOS and remote-management links go on admin."""
    report = {"links": 0, "duplicates": 0, "skipped": 0}
    admin_words = ("scanner", "casa", "rdm", "beszel", "companion", "admin")
    with db.tx() as c:
        sid = _site(c, site_id)
        for row in csv.reader(io.StringIO(text), delimiter="\t"):
            if len(row) < 3 or row[0].strip().lower() != "app":
                report["skipped"] += 1
                continue
            label, url = row[1].strip(), row[2].strip()
            if not url.lower().startswith(("http://", "https://")):
                report["skipped"] += 1
                continue
            if c.execute("SELECT 1 FROM links WHERE site_id=? AND url=? COLLATE NOCASE", (sid, url)).fetchone():
                report["duplicates"] += 1
                continue
            lower = label.lower()
            board = "admin" if any(w in lower for w in admin_words) else "public"
            kind = ("buttons" if "/emulator/" in url else "timer" if "/editor" in url
                    else "device" if label.upper().startswith(("ATLAP", "ATLAV", "ATTLAV", "ATCC", "ATPI")) or "projector" in lower
                    else "tool")
            c.execute("INSERT INTO links(site_id,room_id,board,label,url,kind,sort) VALUES(?,?,?,?,?,?,?)",
                      (sid, None, board, label, url, kind, report["links"]))
            report["links"] += 1
        db.audit(c, "import", "import.homarr", json.dumps(report))
    return report


def import_device_state(data: dict, site_id: int | None = None) -> dict:
    """Device Suite state.json: known kiosk hosts with IP, MAC and current URL."""
    report = {"nodes_created": 0, "nodes_updated": 0}
    devices = data.get("devices", data) if isinstance(data, dict) else {}
    with db.tx() as c:
        sid = _site(c, site_id)
        for host, d in devices.items():
            if not isinstance(d, dict):
                continue
            name = "".join(ch for ch in str(host).upper() if ch.isalnum() or ch in "-_.")[:64]
            if not name:
                continue
            exists = c.execute("SELECT id FROM nodes WHERE name=?", (name,)).fetchone()
            if exists:
                c.execute("UPDATE nodes SET ip=COALESCE(NULLIF(ip,''),?), mac=COALESCE(NULLIF(mac,''),?) WHERE id=?",
                          (d.get("ip") or "", d.get("mac") or "", exists["id"]))
                report["nodes_updated"] += 1
            else:
                c.execute("INSERT INTO nodes(name,site_id,kind,legacy,ip,mac,version,current_url,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                          (name, sid, "kiosk", 1, d.get("ip") or "", d.get("mac") or "", str(d.get("version") or ""),
                           d.get("current_url") or "", db.now_iso()))
                report["nodes_created"] += 1
        db.audit(c, "import", "import.device_state", json.dumps(report))
    return report


def _cols(c, table: str) -> set[str]:
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def import_roomcomms(db_path: Path, key: bytes | None, site_id: int | None = None) -> dict:
    """AT-RoomComms (0.3.x to 1.0.0) or AT-Presenter database. Passwords keep
    working because the hash format is the same. Messages are decrypted with
    the old key and re-encrypted with this server's key."""
    report = {"accounts": 0, "accounts_existing": 0, "rooms_created": [], "messages": 0,
              "messages_unreadable": 0, "messages_skipped_dm": 0, "help_requests": 0, "operators_not_imported": 0}
    old_f = Fernet(key.strip()) if key else None
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "accounts" not in tables or "messages" not in tables:
            raise HTTPException(400, "That isn't a RoomComms or Presenter database")
        with db.tx() as c:
            if db.get_setting(c, "import.roomcomms.done", False):
                raise HTTPException(409, "A RoomComms database was already imported. Restore a backup to import again.")
            sid = _site(c, site_id)
            site_channel = c.execute("SELECT id FROM channels WHERE site_id=? AND kind='site'", (sid,)).fetchone()["id"]
            for a in src.execute("SELECT * FROM accounts"):
                if c.execute("SELECT 1 FROM accounts WHERE username=?", (a["username"],)).fetchone():
                    report["accounts_existing"] += 1
                    continue
                role = "admin" if a["role"] == "admin" else "tech" if a["role"] in ("technician", "tech", "operator", "manager") else "viewer"
                c.execute("INSERT INTO accounts(username,password_hash,display_name,role,site_id,active,created_at) VALUES(?,?,?,?,?,?,?)",
                          (a["username"], a["password_hash"], a["display_name"] or a["username"], role, None,
                           a["active"] if "active" in a.keys() else 1, db.now_iso()))
                report["accounts"] += 1
            room_map: dict[int, int] = {}
            if "rooms" in tables:
                for r in src.execute("SELECT * FROM rooms"):
                    room_map[r["id"]] = _room_id(c, sid, r["name"], report["rooms_created"])
            if "operators" in tables:
                report["operators_not_imported"] = src.execute("SELECT COUNT(*) FROM operators").fetchone()[0]
            mcols = _cols(src, "messages")
            for m in src.execute("SELECT * FROM messages ORDER BY id"):
                if m["scope"] == "dm":
                    report["messages_skipped_dm"] += 1
                    continue
                if "deleted_at" in mcols and m["deleted_at"]:
                    continue
                body = m["body"] or ""
                if old_f and body:
                    try:
                        body = old_f.decrypt(body.encode()).decode()
                    except (InvalidToken, ValueError):
                        report["messages_unreadable"] += 1
                        continue
                if m["scope"] == "room" and m["scope_id"] in room_map:
                    ch = c.execute("SELECT id FROM channels WHERE room_id=? AND kind='room'", (room_map[m["scope_id"]],)).fetchone()["id"]
                else:
                    ch = site_channel
                c.execute("INSERT INTO messages(channel_id,sender_id,sender_name,body_enc,priority,created_at) VALUES(?,?,?,?,?,?)",
                          (ch, None, m["sender"] or "Unknown", encrypt(body), m["priority"] or "normal", m["created_at"] or db.now_iso()))
                report["messages"] += 1
            if "help_requests" in tables:
                for h in src.execute("SELECT * FROM help_requests"):
                    rid = room_map.get(h["room_id"])
                    c.execute("INSERT INTO help_requests(site_id,room_id,room_name,requested_by,category,description,priority,status,created_at) "
                              "VALUES(?,?,?,?,?,?,?,?,?)",
                              (sid, rid, h["room_name"] or "", h["requested_by"] or "", h["category"] or "general",
                               h["description"] or "", h["priority"] or "normal",
                               h["status"] if h["status"] in ("open", "acknowledged", "resolved") else "resolved",
                               h["created_at"] or db.now_iso()))
                    report["help_requests"] += 1
            db.set_setting(c, "import.roomcomms.done", True)
            db.audit(c, "import", "import.roomcomms", json.dumps({k: v if isinstance(v, int) else len(v) for k, v in report.items()}))
    finally:
        src.close()
    return report


# ---------------------------------------------------------------- routes --
async def _read(file: UploadFile) -> bytes:
    data = await file.read(MAX_IMPORT + 1)
    if len(data) > MAX_IMPORT:
        raise HTTPException(413, "File is too large")
    return data


@router.post("/api/admin/import/rooms-txt")
async def route_rooms_txt(file: UploadFile, site_id: int | None = None, p: Principal = Depends(require_admin)):
    return import_rooms_txt((await _read(file)).decode("utf-8", "replace"), site_id)


@router.post("/api/admin/import/homarr")
async def route_homarr(file: UploadFile, site_id: int | None = None, p: Principal = Depends(require_admin)):
    return import_homarr_tsv((await _read(file)).decode("utf-8", "replace"), site_id)


@router.post("/api/admin/import/device-state")
async def route_state(file: UploadFile, site_id: int | None = None, p: Principal = Depends(require_admin)):
    try:
        data = json.loads(await _read(file))
    except ValueError:
        raise HTTPException(400, "That isn't a JSON file")
    return import_device_state(data, site_id)


@router.post("/api/admin/import/roomcomms")
async def route_roomcomms(file: UploadFile, site_id: int | None = None, p: Principal = Depends(require_admin)):
    """Upload a zip of the RoomComms /data folder (roomcomms.db + .encryption_key),
    or the bare database if messages weren't encrypted."""
    data = await _read(file)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        key = None
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                dbname = next((n for n in names if n.endswith((".db", ".sqlite"))), None)
                if not dbname:
                    raise HTTPException(400, "No database in that zip")
                (tmpdir / "src.db").write_bytes(z.read(dbname))
                keyname = next((n for n in names if n.endswith(".encryption_key")), None)
                if keyname:
                    key = z.read(keyname)
        else:
            (tmpdir / "src.db").write_bytes(data)
        return import_roomcomms(tmpdir / "src.db", key, site_id)
