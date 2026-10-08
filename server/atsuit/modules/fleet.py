"""Nodes: every tech laptop, kiosk and caption source. Replaces AT Device
Suite, the Fleet Dashboard and AT Ops' fleet half, and keeps the old kiosk
agent API (/heartbeat, /agent/poll, /agent/ack) so deployed agents keep
working when pointed at AT-SUIT."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .. import config, db, licence
from ..hub import hub
from ..security import NODE_COOKIE, Principal, new_token, require_admin, require_node, require_tech, token_hash
from .core import require_module, site_ok

router = APIRouter(dependencies=[Depends(require_module("fleet"))])
legacy = APIRouter()

ONLINE_SECONDS = 45
NODE_KINDS = ("tech", "kiosk", "caption")
COMMANDS = ("set_url", "reload", "message", "reboot", "shutdown", "update", "identify", "restart_browser")
SSH_COMMANDS = {
    "reboot": "sudo reboot",
    "shutdown": "sudo shutdown now",
    "update": "if command -v at-fleet-os-update >/dev/null 2>&1; then sudo at-fleet-os-update; "
              "else sudo apt update && sudo apt full-upgrade -y && sudo reboot; fi",
}


def norm_host(h) -> str:
    return re.sub(r"[^A-Z0-9_.-]", "", str(h or "").strip().upper())[:64]


def agent_file() -> Path | None:
    """The node agent laptops pull updates from: an uploaded one in the data
    volume wins, then the one bundled in the image, then the repo copy (dev)."""
    here = Path(__file__).resolve()
    for f in (config.cfg.data / "agent" / "atsuit_node.py", here.parents[1] / "agent" / "atsuit_node.py",
              here.parents[3] / "node-agent" / "atsuit_node.py"):
        if f.is_file():
            return f
    return None


def agent_release(f: Path | None = None) -> dict:
    import hashlib

    f = f or agent_file()
    if not f:
        return {"version": None}
    data = f.read_bytes()
    m = re.search(rb'^VERSION = "([^"]+)"', data, re.M)
    return {"version": m.group(1).decode() if m else None, "sha256": hashlib.sha256(data).hexdigest()}


def screen_file(name: str = "atsuit_screen.py") -> Path | None:
    """The Linux screen agent (or its install.sh): an uploaded one in the data
    volume wins, then the copy bundled in the image, then the repo (dev)."""
    here = Path(__file__).resolve()
    for f in (config.cfg.data / "screen-agent" / name, here.parents[1] / "agent" / "screen" / name,
              here.parents[3] / "screen-agent" / name):
        if f.is_file():
            return f
    return None


def screen_release() -> dict:
    f = screen_file()
    return agent_release(f) if f else {"version": None}


def client_dir() -> Path:
    d = config.cfg.data / "client-updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ssh_key() -> Path:
    return config.cfg.data / "ssh" / "fleet_key"


def work_day(c, site_id) -> str:
    """Today's date at the node's site. A tech laptop's room choice lasts until
    the day rolls over at node_room_reset_hour (default 05:00), so a late show
    keeps its room past midnight and the next morning starts with a fresh pick."""
    tz = None
    if site_id:
        row = c.execute("SELECT timezone FROM sites WHERE id=?", (site_id,)).fetchone()
        tz = row["timezone"] if row else None
    try:
        zone = ZoneInfo(tz or os.getenv("TZ") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    hour = int(db.get_setting(c, "node_room_reset_hour", 5) or 0)
    return (datetime.now(zone) - timedelta(hours=hour)).date().isoformat()


def current_room_id(c, n):
    """Kiosks and caption sources keep the room an admin gave them; a tech
    laptop's room only counts on the day the tech picked it."""
    if not n["room_id"]:
        return None
    if n["kind"] != "tech":
        return n["room_id"]
    return n["room_id"] if n["room_day"] == work_day(c, n["site_id"]) else None


def node_out(r, c=None) -> dict:
    d = dict(r)
    d.pop("token_hash", None)
    if c is not None and r["kind"] == "tech" and current_room_id(c, r) is None:
        d["room_id"] = None
        if "room_name" in d:
            d["room_name"] = None
    d["online"] = bool(r["last_seen"] and time.time() - r["last_seen"] < ONLINE_SECONDS)
    d["info"] = json.loads(r["info_json"] or "{}")
    d.pop("info_json", None)
    return d


def _check_node_limit(c) -> None:
    lic = licence.current(c)
    if lic.max_nodes:
        count = c.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        if count >= lic.max_nodes:
            raise HTTPException(402, f"Your licence allows {lic.max_nodes} nodes")


# ---------------------------------------------------------- node-facing --
class EnrolIn(BaseModel):
    code: str
    name: str = Field(min_length=1, max_length=64)
    kind: str = "tech"


@router.post("/api/nodes/enrol")
async def enrol(body: EnrolIn):
    if body.kind not in NODE_KINDS:
        raise HTTPException(400, "Unknown node kind")
    name = norm_host(body.name)
    token = new_token("atn_")
    with db.tx() as c:
        site = c.execute("SELECT * FROM sites WHERE enrol_code=?", (body.code.strip().upper(),)).fetchone()
        if not site:
            raise HTTPException(403, "Enrolment code is wrong")
        existing = c.execute("SELECT id FROM nodes WHERE name=?", (name,)).fetchone()
        if existing:
            c.execute("UPDATE nodes SET token_hash=?, site_id=?, kind=?, legacy=0 WHERE id=?",
                      (token_hash(token), site["id"], body.kind, existing["id"]))
            node_id = existing["id"]
        else:
            _check_node_limit(c)
            node_id = c.execute(
                "INSERT INTO nodes(name,site_id,kind,token_hash,created_at) VALUES(?,?,?,?,?)",
                (name, site["id"], body.kind, token_hash(token), db.now_iso())).lastrowid
        db.audit(c, f"node:{name}", "node.enrol", site["name"])
    await hub.publish("fleet", "node.changed", {"id": node_id})
    return {"node_id": node_id, "name": name, "token": token, "site_id": site["id"]}


class HeartbeatIn(BaseModel):
    ip: str = ""
    mac: str = ""
    version: str = ""
    current_url: str = ""
    info: dict = {}


@router.post("/api/nodes/heartbeat")
async def heartbeat(body: HeartbeatIn, request: Request, p: Principal = Depends(require_node)):
    ip = body.ip or (request.client.host if request.client else "")
    with db.tx() as c:
        old = c.execute("SELECT version, mac, info_json FROM nodes WHERE id=?", (p.id,)).fetchone()
        # A Linux screen reports twice: its agent (displays, MAC, agent version)
        # and the /screen page (what is showing). Merge, so neither wipes the other.
        try:
            info = json.loads(old["info_json"] or "{}")
        except ValueError:
            info = {}
        info.update(body.info)
        version = body.version or old["version"] or str(body.info.get("page") or "")
        c.execute(
            "UPDATE nodes SET ip=?, mac=?, version=?, current_url=COALESCE(NULLIF(?,''),current_url), info_json=?, last_seen=? "
            "WHERE id=?",
            (ip[:64], (body.mac or old["mac"] or "")[:64], version[:32], body.current_url[:1000], json.dumps(info)[:8000],
             time.time(), p.id))
        pending = c.execute("SELECT COUNT(*) FROM node_commands WHERE node_id=? AND status='queued'", (p.id,)).fetchone()[0]
        n = c.execute("SELECT * FROM nodes WHERE id=?", (p.id,)).fetchone()
        room_id = current_room_id(c, n)
    await hub.publish("fleet", "node.heartbeat", {"id": p.id})
    out = {"ok": True, "pending_commands": pending, "room_id": room_id, "screen_view": n["screen_view"],
           "agent": agent_release()}
    if n["kind"] == "kiosk":
        out["screen_agent"] = screen_release()
    return out


@router.get("/api/nodes/agent")
def agent_info(p: Principal = Depends(require_node)):
    return agent_release()


@router.get("/api/nodes/agent/file")
def agent_download(p: Principal = Depends(require_node)):
    f = agent_file()
    if not f:
        raise HTTPException(404, "No node agent on this server")
    return FileResponse(f, media_type="text/x-python", filename="atsuit_node.py")


@router.get("/api/nodes/screen-agent/file")
def screen_agent_download(p: Principal = Depends(require_node)):
    f = screen_file()
    if not f:
        raise HTTPException(404, "No screen agent on this server")
    return FileResponse(f, media_type="text/x-python", filename="atsuit_screen.py")


@router.get("/api/fleet/screen-agent")
def screen_agent_info(p: Principal = Depends(require_tech)):
    return screen_release()


@router.get("/screen-agent/{filename}")
def screen_agent_public(filename: str):
    """Public: a new Linux screen fetches install.sh and the agent from here
    before it is enrolled (curl -fsSL http://server/screen-agent/install.sh)."""
    if filename not in ("install.sh", "atsuit_screen.py"):
        raise HTTPException(404)
    f = screen_file(filename)
    if not f:
        raise HTTPException(404, "Not on this server")
    return FileResponse(f, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-cache"})


@router.post("/api/fleet/agent")
async def upload_agent(file: UploadFile, p: Principal = Depends(require_admin)):
    """Publish a newer node agent; laptops pick it up on their next heartbeat."""
    data = await file.read(2 * 1024 * 1024)
    if not re.search(rb'^VERSION = "[^"]+"', data, re.M):
        raise HTTPException(400, "That isn't an AT-SUIT node agent")
    d = config.cfg.data / "agent"
    d.mkdir(parents=True, exist_ok=True)
    (d / "atsuit_node.py").write_bytes(data)
    with db.tx() as c:
        db.audit(c, p.name, "fleet.agent_release", agent_release().get("version") or "")
    return agent_release()


# ------------------------------------------------------- Windows app --
APP_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,120}\.(exe|blockmap|yml)$")


def app_dir() -> Path:
    d = config.cfg.data / "app"
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_release() -> dict:
    """What the laptops' AT-SUIT Node app updates to: electron-builder's
    latest.yml plus the installer it names, uploaded by an admin."""
    f = app_dir() / "latest.yml"
    if not f.is_file():
        return {"version": None}
    text = f.read_text(errors="replace")
    version = re.search(r"^version:\s*['\"]?([^'\"\s]+)", text, re.M)
    path = re.search(r"^path:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", text, re.M)
    name = path.group(1) if path else None
    return {"version": version.group(1) if version else None, "file": name,
            "ready": bool(name and (app_dir() / name).is_file())}


@router.get("/api/nodes/app")
def app_info():
    return app_release()


@router.get("/api/nodes/app/{filename}")
def app_download(filename: str):
    """Public: a new laptop downloads the installer here before it is enrolled,
    and installed apps pull updates from here. Licences are enforced at enrolment."""
    if not APP_FILE.match(filename):
        raise HTTPException(404)
    f = app_dir() / filename
    if not f.is_file():
        raise HTTPException(404, "Not published yet")
    return FileResponse(f, headers={"Cache-Control": "no-cache"})


@router.post("/api/fleet/app")
async def upload_app(files: list[UploadFile], p: Principal = Depends(require_admin)):
    """Publish a Windows app release: the Setup .exe, its .blockmap and latest.yml
    from the GitHub release. Installed apps update next time they close."""
    names = []
    for file in files:
        name = os.path.basename(file.filename or "")
        if not APP_FILE.match(name):
            raise HTTPException(400, f"{name or 'That file'} isn't part of an app release")
        tmp = app_dir() / f".{name}.part"
        with tmp.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
        tmp.replace(app_dir() / name)
        names.append(name)
    rel = app_release()
    keep = {"latest.yml", rel.get("file"), f"{rel.get('file')}.blockmap"}
    for old in app_dir().iterdir():
        if old.is_file() and old.name not in keep and not old.name.startswith("."):
            old.unlink()
    with db.tx() as c:
        db.audit(c, p.name, "fleet.app_release", rel.get("version") or ",".join(names))
    return rel


@router.get("/api/nodes/me")
def node_me(p: Principal = Depends(require_node)):
    with db.ro() as c:
        n = c.execute("SELECT * FROM nodes WHERE id=?", (p.id,)).fetchone()
        rid = current_room_id(c, n)
        room = c.execute("SELECT id,name,short_name FROM rooms WHERE id=?", (rid,)).fetchone() if rid else None
        rooms = db.rows(c.execute("SELECT id,name,short_name FROM rooms WHERE enabled=1 AND (site_id=? OR ? IS NULL) "
                                  "ORDER BY sort,name", (n["site_id"], n["site_id"])))
        from .timers import list_views

        return {"node": node_out(n, c), "room": dict(room) if room else None, "rooms": rooms,
                "day": work_day(c, n["site_id"]), "views": list_views(c, tests=True) + [{"id": "captions", "name": "Captions", "builtin": True}]}


# ------------------------------------------------------- remote screens --
def check_view(c, view: str) -> str:
    """What a screen shows: a built-in timer view, captions, a view built in
    the console (built:<slug>), an uploaded view (view:<slug>) or any web page (url:https://...). Empty means 'not chosen'."""
    from .timers import view_known

    view = (view or "").strip()
    if view in ("", "captions") or view_known(c, view):
        return view
    if re.match(r"^url:https?://\S{1,1000}$", view):
        return view
    raise HTTPException(400, "Unknown screen view")


class ScreenPick(BaseModel):
    room_id: int | None = None
    view: str = ""


def _set_screen(c, n, body: ScreenPick) -> None:
    if body.room_id is not None:
        room = c.execute("SELECT * FROM rooms WHERE id=?", (body.room_id,)).fetchone()
        if not room or (n["site_id"] and room["site_id"] != n["site_id"]):
            raise HTTPException(404, "Room not found")
    c.execute("UPDATE nodes SET room_id=?, room_day=?, screen_view=? WHERE id=?",
              (body.room_id, work_day(c, n["site_id"]), check_view(c, body.view), n["id"]))


@router.put("/api/nodes/me/screen")
async def node_pick_screen(body: ScreenPick, p: Principal = Depends(require_node)):
    """A remote screen chooses its room and view from the screen itself."""
    with db.tx() as c:
        _set_screen(c, c.execute("SELECT * FROM nodes WHERE id=?", (p.id,)).fetchone(), body)
    await hub.publish("fleet", "node.changed", {"id": p.id})
    return {"ok": True}


@router.put("/api/fleet/nodes/{node_id}/screen")
async def route_screen(node_id: int, body: ScreenPick, p: Principal = Depends(require_tech)):
    """Route a screen from the dashboard: which room and which view it shows."""
    with db.tx() as c:
        n = c.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not n or not site_ok(p, n["site_id"]):
            raise HTTPException(404, "Node not found")
        _set_screen(c, n, body)
        db.audit(c, p.name, "node.screen", f"{n['name']}: room {body.room_id} {body.view}")
    await hub.publish("fleet", "node.changed", {"id": node_id})
    return {"ok": True}


class RoomPick(BaseModel):
    room_id: int | None = None


@router.put("/api/nodes/me/room")
async def node_pick_room(body: RoomPick, p: Principal = Depends(require_node)):
    """The tech says which room this laptop is in today."""
    with db.tx() as c:
        n = c.execute("SELECT * FROM nodes WHERE id=?", (p.id,)).fetchone()
        if body.room_id is not None:
            room = c.execute("SELECT * FROM rooms WHERE id=?", (body.room_id,)).fetchone()
            if not room or (n["site_id"] and room["site_id"] != n["site_id"]):
                raise HTTPException(404, "Room not found")
        c.execute("UPDATE nodes SET room_id=?, room_day=? WHERE id=?", (body.room_id, work_day(c, n["site_id"]), p.id))
    await hub.publish("fleet", "node.changed", {"id": p.id})
    return {"ok": True, "room_id": body.room_id}


class StartIn(BaseModel):
    operator: str = Field(min_length=1, max_length=60)
    room_id: int
    mode: str = Field(pattern="^(main|backup)$")


class ModeIn(BaseModel):
    mode: str = Field(pattern="^(main|backup)$")


def _node_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    return auth[5:].strip() if auth.lower().startswith("node ") else request.cookies.get(NODE_COOKIE, "")


@router.post("/api/nodes/me/start")
async def node_start(body: StartIn, request: Request, response: Response, p: Principal = Depends(require_node)):
    """A tech starts the day on a tech laptop: their name, the room, and
    whether this is the main PC (no notifications) or the backup (silent
    pop-ups). The laptop's own enrolment signs them in; no password."""
    operator = " ".join(body.operator.split())
    if not operator:
        raise HTTPException(422, "Enter your name")
    with db.tx() as c:
        n = c.execute("SELECT * FROM nodes WHERE id=?", (p.id,)).fetchone()
        if n["kind"] != "tech":
            raise HTTPException(400, "Only tech laptops start a day")
        room = c.execute("SELECT * FROM rooms WHERE id=? AND enabled=1", (body.room_id,)).fetchone()
        if not room or (n["site_id"] and room["site_id"] != n["site_id"]):
            raise HTTPException(404, "Room not found")
        c.execute("UPDATE nodes SET operator=?, mode=?, room_id=?, room_day=? WHERE id=?",
                  (operator, body.mode, body.room_id, work_day(c, n["site_id"]), p.id))
        db.audit(c, operator, "node.start", f"{n['name']}: {room['name']}, {body.mode}")
    response.set_cookie(NODE_COOKIE, _node_token(request), httponly=True, samesite="lax", max_age=400 * 86400)
    await hub.publish("fleet", "node.changed", {"id": p.id})
    return {"ok": True}


@router.put("/api/nodes/me/mode")
async def node_mode(body: ModeIn, p: Principal = Depends(require_node)):
    with db.tx() as c:
        c.execute("UPDATE nodes SET mode=? WHERE id=?", (body.mode, p.id))
    await hub.publish("fleet", "node.changed", {"id": p.id})
    return {"ok": True}


@router.post("/api/nodes/me/finish")
async def node_finish(response: Response, p: Principal = Depends(require_node)):
    """End of the day: the next tech enters their own name and room."""
    with db.tx() as c:
        c.execute("UPDATE nodes SET operator='', room_day='' WHERE id=? AND kind='tech'", (p.id,))
    response.delete_cookie(NODE_COOKIE)
    await hub.publish("fleet", "node.changed", {"id": p.id})
    return {"ok": True}


@router.get("/api/nodes/commands")
def poll_commands(p: Principal = Depends(require_node), kinds: str = ""):
    """Queued commands; `kinds` limits them (a screen's page and its agent
    each take their own)."""
    with db.ro() as c:
        cmds = db.rows(c.execute("SELECT id,kind,payload_json,created_at FROM node_commands WHERE node_id=? AND status='queued' ORDER BY id", (p.id,)))
    if kinds:
        wanted = set(kinds.split(","))
        cmds = [x for x in cmds if x["kind"] in wanted]
    for cmd in cmds:
        cmd["payload"] = json.loads(cmd.pop("payload_json"))
    return cmds


class AckIn(BaseModel):
    ok: bool = True
    detail: str = ""


@router.post("/api/nodes/commands/{command_id}/ack")
async def ack_command(command_id: int, body: AckIn, p: Principal = Depends(require_node)):
    with db.tx() as c:
        cmd = c.execute("SELECT * FROM node_commands WHERE id=? AND node_id=?", (command_id, p.id)).fetchone()
        if not cmd:
            raise HTTPException(404, "Command not found")
        c.execute("UPDATE node_commands SET status=?, acked_at=? WHERE id=?", ("done" if body.ok else "failed", db.now_iso(), command_id))
        if body.ok and cmd["kind"] == "set_url":
            c.execute("UPDATE nodes SET current_url=? WHERE id=?", (json.loads(cmd["payload_json"]).get("url", ""), p.id))
    await hub.publish("fleet", "node.changed", {"id": p.id})
    return {"ok": True}


# --------------------------------------------------------------- admin --
@router.get("/api/fleet/nodes")
def list_nodes(p: Principal = Depends(require_tech)):
    with db.ro() as c:
        rows = c.execute("SELECT n.*, r.name AS room_name FROM nodes n LEFT JOIN rooms r ON r.id=n.room_id ORDER BY n.name").fetchall()
        return [node_out(r, c) for r in rows if site_ok(p, r["site_id"])]


@router.get("/api/fleet/enrolment")
def enrolment_info(p: Principal = Depends(require_admin)):
    with db.ro() as c:
        return db.rows(c.execute("SELECT id,name,enrol_code FROM sites ORDER BY name"))


class NodeEdit(BaseModel):
    name: str | None = None
    room_id: int | None = None
    site_id: int | None = None
    kind: str | None = None


@router.put("/api/fleet/nodes/{node_id}")
async def edit_node(node_id: int, body: NodeEdit, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        n = c.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not n:
            raise HTTPException(404, "Node not found")
        if body.kind and body.kind not in NODE_KINDS:
            raise HTTPException(400, "Unknown node kind")
        try:
            site_id = body.site_id or n["site_id"]
            c.execute("UPDATE nodes SET name=?, room_id=?, room_day=?, site_id=?, kind=? WHERE id=?",
                      (norm_host(body.name) if body.name else n["name"], body.room_id, work_day(c, site_id),
                       site_id, body.kind or n["kind"], node_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Another node has that name")
        db.audit(c, p.name, "node.edit", n["name"])
    await hub.publish("fleet", "node.changed", {"id": node_id})
    return {"ok": True}


@router.delete("/api/fleet/nodes/{node_id}")
async def delete_node(node_id: int, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        c.execute("DELETE FROM nodes WHERE id=?", (node_id,))
        db.audit(c, p.name, "node.delete", str(node_id))
    await hub.publish("fleet", "node.changed", {"id": node_id})
    return {"ok": True}


class CommandIn(BaseModel):
    kind: str
    payload: dict = {}


async def _run_ssh(ip: str, command: str) -> None:
    key = ssh_key()
    if not key.exists():
        return
    user = os.getenv("ATSUIT_FLEET_SSH_USER", "adam")
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-i", str(key), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={key.parent / 'known_hosts'}",
        f"{user}@{ip}", command,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(proc.wait(), 60)
    except asyncio.TimeoutError:
        proc.kill()


def queue_command(c, node_id: int, kind: str, payload: dict) -> int:
    return c.execute("INSERT INTO node_commands(node_id,kind,payload_json,created_at) VALUES(?,?,?,?)",
                     (node_id, kind, json.dumps(payload), db.now_iso())).lastrowid


@router.post("/api/fleet/nodes/{node_id}/command")
async def send_command(node_id: int, body: CommandIn, p: Principal = Depends(require_tech)):
    if body.kind not in COMMANDS:
        raise HTTPException(400, "Unknown command")
    if body.kind in SSH_COMMANDS and not p.at_least("admin"):
        raise HTTPException(403, "Only admins can reboot, shut down or update nodes")
    if body.kind == "set_url" and not str(body.payload.get("url", "")).startswith(("http://", "https://")):
        raise HTTPException(400, "URL must start with http:// or https://")
    with db.tx() as c:
        n = c.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not n or not site_ok(p, n["site_id"]):
            raise HTTPException(404, "Node not found")
        cid = None
        if n["legacy"] and body.kind in SSH_COMMANDS:
            if not ssh_key().exists():
                raise HTTPException(400, "Upload the fleet SSH key in Admin → Fleet to control older kiosk agents")
            asyncio.get_running_loop().create_task(_run_ssh(n["ip"], SSH_COMMANDS[body.kind]))
        else:
            cid = queue_command(c, node_id, body.kind, body.payload)
        db.audit(c, p.name, f"node.{body.kind}", n["name"])
    await hub.publish("fleet", "node.changed", {"id": node_id})
    return {"ok": True, "command_id": cid}


@router.post("/api/fleet/ssh-key")
async def upload_ssh_key(file: UploadFile, p: Principal = Depends(require_admin)):
    data = await file.read(20000)
    if b"PRIVATE KEY" not in data:
        raise HTTPException(400, "That doesn't look like an SSH private key")
    key = ssh_key()
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(data)
    os.chmod(key, 0o600)
    with db.tx() as c:
        db.audit(c, p.name, "fleet.ssh_key")
    return {"ok": True}


@router.post("/api/fleet/client-release")
async def upload_client_release(file: UploadFile, version: str, p: Principal = Depends(require_admin)):
    """Publish a kiosk agent script for the old agents' self-update."""
    import hashlib

    data = await file.read(5 * 1024 * 1024)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or "client.sh")
    (client_dir() / name).write_bytes(data)
    release = {"version": version, "enabled": True, "filename": name, "sha256": hashlib.sha256(data).hexdigest()}
    (client_dir() / "release.json").write_text(json.dumps(release, indent=2))
    with db.tx() as c:
        db.audit(c, p.name, "fleet.client_release", version)
    return release


# -------------------------------------------------------- legacy agents --
def _legacy_on() -> bool:
    from .core import modules_enabled

    with db.ro() as c:
        return bool(db.get_setting(c, "legacy_fleet_api", True)) and modules_enabled(c).get("fleet", False)


def _legacy_guard():
    if not _legacy_on():
        raise HTTPException(404, "Not found")


@legacy.post("/heartbeat", dependencies=[Depends(_legacy_guard)])
async def legacy_heartbeat(request: Request):
    data = await request.json()
    host = norm_host(data.get("host"))
    if not host:
        return PlainTextResponse("NO HOST", status_code=400)
    with db.tx() as c:
        n = c.execute("SELECT * FROM nodes WHERE name=?", (host,)).fetchone()
        info = {"last_os_update": data.get("last_os_update", "")}
        if not n:
            site = c.execute("SELECT id FROM sites ORDER BY id LIMIT 1").fetchone()
            _check_node_limit(c)
            c.execute("INSERT INTO nodes(name,site_id,kind,legacy,created_at) VALUES(?,?,?,?,?)",
                      (host, site["id"] if site else None, "kiosk", 1, db.now_iso()))
        c.execute("UPDATE nodes SET ip=?, mac=?, version=?, current_url=COALESCE(NULLIF(?,''),current_url), info_json=?, last_seen=? WHERE name=?",
                  (str(data.get("ip") or "")[:64], str(data.get("mac") or "")[:64], str(data.get("version") or "")[:32],
                   str(data.get("current_url") or "")[:1000], json.dumps(info), time.time(), host))
    await hub.publish("fleet", "node.heartbeat", {"name": host})
    return PlainTextResponse("OK")


@legacy.get("/agent/poll/{host}", dependencies=[Depends(_legacy_guard)])
def legacy_poll(host: str):
    with db.ro() as c:
        cmd = c.execute(
            "SELECT nc.id, nc.payload_json FROM node_commands nc JOIN nodes n ON n.id=nc.node_id "
            "WHERE n.name=? AND nc.kind='set_url' AND nc.status='queued' ORDER BY nc.id DESC LIMIT 1",
            (norm_host(host),)).fetchone()
    if not cmd:
        return {"id": None, "url": None}
    return {"id": str(cmd["id"]), "url": json.loads(cmd["payload_json"]).get("url")}


@legacy.post("/agent/ack", dependencies=[Depends(_legacy_guard)])
async def legacy_ack(request: Request):
    data = await request.json()
    host = norm_host(data.get("host"))
    with db.tx() as c:
        n = c.execute("SELECT id FROM nodes WHERE name=?", (host,)).fetchone()
        cmd = c.execute("SELECT * FROM node_commands WHERE id=? AND node_id=? AND status='queued'",
                        (str(data.get("id") or "0"), n["id"] if n else -1)).fetchone()
        if not cmd:
            return PlainTextResponse("NO MATCH", status_code=404)
        c.execute("UPDATE node_commands SET status='done', acked_at=? WHERE node_id=? AND kind='set_url' AND status='queued' AND id<=?",
                  (db.now_iso(), n["id"], cmd["id"]))
        c.execute("UPDATE nodes SET current_url=? WHERE id=?", (json.loads(cmd["payload_json"]).get("url", ""), n["id"]))
    await hub.publish("fleet", "node.changed", {"name": host})
    return PlainTextResponse("ACK OK")


@legacy.get("/kiosk/{host}", response_class=HTMLResponse, dependencies=[Depends(_legacy_guard)])
def legacy_kiosk(host: str):
    """Room link picker the old kiosk agents open."""
    with db.ro() as c:
        links = c.execute(
            "SELECT l.label, l.url, COALESCE(r.name,'Site') AS room FROM links l LEFT JOIN rooms r ON r.id=l.room_id "
            "WHERE l.kind IN ('kiosk','timer','buttons','link') ORDER BY r.sort, r.name, l.sort").fetchall()
    groups: dict[str, list] = {}
    for l in links:
        groups.setdefault(l["room"], []).append(l)
    body = "".join(
        f"<h2>{escape(room)}</h2>" + "".join(f'<a href="{escape(l["url"])}">{escape(l["label"])}</a>' for l in ls)
        for room, ls in groups.items())
    return HTMLResponse(
        "<!doctype html><meta name=viewport content='width=device-width'><title>Kiosk</title>"
        "<style>body{font-family:system-ui;background:#0e1116;color:#e8eaf0;padding:20px}"
        "a{display:inline-block;margin:6px;padding:14px 18px;background:#1c2230;color:#fff;border-radius:8px;text-decoration:none}</style>"
        f"<h1>{escape(norm_host(host))}</h1>{body or '<p>No kiosk links yet. Add them in Admin → Dashboard.</p>'}")


@legacy.get("/client-update", dependencies=[Depends(_legacy_guard)])
def legacy_client_update():
    f = client_dir() / "release.json"
    if not f.exists():
        return JSONResponse({"enabled": False, "error": "No release published"}, status_code=404)
    release = json.loads(f.read_text())
    base = config.cfg.public_url
    if base and release.get("filename"):
        release["url"] = f"{base}/client-update/file/{release['filename']}"
    return release


@legacy.get("/client-update/file/{filename}", dependencies=[Depends(_legacy_guard)])
def legacy_client_file(filename: str):
    if "/" in filename or filename.startswith("."):
        raise HTTPException(404)
    f = client_dir() / filename
    if not f.is_file():
        raise HTTPException(404)
    return FileResponse(f)


@legacy.get("/client-bootstrap", dependencies=[Depends(_legacy_guard)])
def legacy_bootstrap():
    f = client_dir() / "client-bootstrap.sh"
    if not f.is_file():
        raise HTTPException(404, "No bootstrap script uploaded")
    return FileResponse(f, media_type="text/x-shellscript")


# ------------------------------------------------ AT-SUIT Node overlay --
# The Windows app can float a click-through overlay (the room timer, by
# default) over the slides. Any tech, a laptop in the room or Companion can
# turn a laptop's overlay on or off and move it. What was asked for is kept in
# the node's info JSON as "overlay_want"; what the app actually shows comes
# back as "overlay" (PUT /api/nodes/me/overlay). No schema change needed.
OVERLAY_POSITIONS = ("top-center", "bottom-right", "bottom-left", "top-right", "top-left", "bottom-bar", "top-bar")
OVERLAY_SIZES = ("small", "medium", "large")
OVERLAY_DEFAULT = {"on": False, "url": "", "position": "top-center", "size": "medium", "display": 0, "opacity": 0.85}


class OverlayIn(BaseModel):
    """Every field is optional: anything left out keeps its last value."""
    on: bool | None = None
    url: str | None = Field(None, max_length=1000)  # "" = this room's timer overlay
    position: str | None = Field(None, pattern="^(" + "|".join(OVERLAY_POSITIONS) + ")$")
    size: str | None = Field(None, pattern="^(small|medium|large)$")
    display: int | None = Field(None, ge=0, le=8)
    opacity: float | None = Field(None, ge=0.2, le=1.0)


class OverlayReport(BaseModel):
    on: bool = False
    url: str = Field("", max_length=1000)
    target: str = Field("", max_length=1100)
    position: str = Field("", max_length=20)
    size: str = Field("", max_length=10)
    display: int = 0
    opacity: float = 1.0
    room_id: int | None = None
    error: str = Field("", max_length=300)


def _info(n) -> dict:
    try:
        return json.loads(n["info_json"] or "{}")
    except ValueError:
        return {}


def _save_info(c, node_id: int, info: dict) -> None:
    c.execute("UPDATE nodes SET info_json=? WHERE id=?", (json.dumps(info), node_id))


def overlay_out(c, n) -> dict:
    info = _info(n)
    return {"id": n["id"], "name": n["name"], "operator": n["operator"] or "", "mode": n["mode"] or "",
            "online": bool(n["last_seen"] and time.time() - n["last_seen"] < ONLINE_SECONDS),
            "app": str(n["version"] or "").startswith("app-"), "room_id": current_room_id(c, n),
            "want": {**OVERLAY_DEFAULT, **(info.get("overlay_want") or {})}, "state": info.get("overlay")}


async def _overlay_changed(node_id: int, room_id) -> None:
    data = {"id": node_id, "room_id": room_id}
    if room_id:
        await hub.publish(f"room:{room_id}", "overlay.changed", data)
    await hub.publish("fleet", "overlay.changed", data)
    await hub.publish("fleet", "node.changed", {"id": node_id})


@router.get("/api/rooms/{room_id}/overlays")
def room_overlays(room_id: int, p: Principal = Depends(require_tech)):
    """The tech laptops in this room today, and each one's overlay."""
    from .core import room_or_404

    with db.ro() as c:
        room_or_404(c, room_id, p)
        rows = c.execute("SELECT * FROM nodes WHERE kind='tech' ORDER BY mode='backup', name").fetchall()
        return [overlay_out(c, n) for n in rows if site_ok(p, n["site_id"]) and current_room_id(c, n) == room_id]


@router.get("/api/fleet/nodes/{node_id}/overlay")
def get_overlay(node_id: int, p: Principal = Depends(require_tech)):
    with db.ro() as c:
        n = c.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not n or n["kind"] != "tech" or not site_ok(p, n["site_id"]):
            raise HTTPException(404, "Tech laptop not found")
        return overlay_out(c, n)


@router.put("/api/fleet/nodes/{node_id}/overlay")
async def set_overlay(node_id: int, body: OverlayIn, p: Principal = Depends(require_tech)):
    """Turn a tech laptop's overlay on or off, or move it. The laptop's app
    picks up a node command of kind "overlay" with the full settings."""
    url = (body.url or "").strip() if body.url is not None else None
    if url and not re.match(r"^https?://\S+$", url, re.I):
        raise HTTPException(400, "URL must start with http:// or https://")
    with db.tx() as c:
        n = c.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not n or not site_ok(p, n["site_id"]):
            raise HTTPException(404, "Node not found")
        if n["kind"] != "tech":
            raise HTTPException(400, "Only tech laptops running AT-SUIT Node have an overlay")
        info = _info(n)
        want = {**OVERLAY_DEFAULT, **(info.get("overlay_want") or {})}
        changes = body.model_dump(exclude_none=True)
        if url is not None:
            changes["url"] = url
        want.update(changes)
        room_id = current_room_id(c, n)
        want["room_id"] = room_id
        want["by"] = p.name
        want["at"] = db.now_iso()
        info["overlay_want"] = want
        _save_info(c, node_id, info)
        # Only the latest overlay command matters: older queued ones are dropped.
        c.execute("UPDATE node_commands SET status='superseded', acked_at=? WHERE node_id=? AND kind='overlay' AND status='queued'",
                  (db.now_iso(), node_id))
        cid = queue_command(c, node_id, "overlay", {k: want[k] for k in (*OVERLAY_DEFAULT, "room_id", "by")})
        db.audit(c, p.name, "node.overlay", f"{n['name']}: {'on' if want['on'] else 'off'} {want['position']} {want['url'] or 'room timer'}")
    await _overlay_changed(node_id, room_id)
    return {"ok": True, "command_id": cid, "want": want}


@router.get("/api/nodes/me/overlay")
def my_overlay(p: Principal = Depends(require_node)):
    with db.ro() as c:
        return overlay_out(c, c.execute("SELECT * FROM nodes WHERE id=?", (p.id,)).fetchone())


@router.put("/api/nodes/me/overlay")
async def report_overlay(body: OverlayReport, p: Principal = Depends(require_node)):
    """The app says what its overlay is actually doing."""
    with db.tx() as c:
        n = c.execute("SELECT * FROM nodes WHERE id=?", (p.id,)).fetchone()
        info = _info(n)
        info["overlay"] = {**body.model_dump(), "at": db.now_iso()}
        _save_info(c, p.id, info)
        room_id = current_room_id(c, n)
    await _overlay_changed(p.id, room_id)
    return {"ok": True}
