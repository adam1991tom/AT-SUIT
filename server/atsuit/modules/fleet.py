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
from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .. import config, db, licence
from ..hub import hub
from ..security import Principal, new_token, require_admin, require_node, require_tech, token_hash
from .core import require_module, site_ok

router = APIRouter(dependencies=[Depends(require_module("fleet"))])
legacy = APIRouter()

ONLINE_SECONDS = 45
NODE_KINDS = ("tech", "kiosk", "caption")
COMMANDS = ("set_url", "reload", "message", "reboot", "shutdown", "update", "identify")
SSH_COMMANDS = {
    "reboot": "sudo reboot",
    "shutdown": "sudo shutdown now",
    "update": "if command -v at-fleet-os-update >/dev/null 2>&1; then sudo at-fleet-os-update; "
              "else sudo apt update && sudo apt full-upgrade -y && sudo reboot; fi",
}


def norm_host(h) -> str:
    return re.sub(r"[^A-Z0-9_.-]", "", str(h or "").strip().upper())[:64]


def client_dir() -> Path:
    d = config.cfg.data / "client-updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ssh_key() -> Path:
    return config.cfg.data / "ssh" / "fleet_key"


def node_out(r) -> dict:
    d = dict(r)
    d.pop("token_hash", None)
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
        c.execute(
            "UPDATE nodes SET ip=?, mac=?, version=?, current_url=?, info_json=?, last_seen=? WHERE id=?",
            (ip[:64], body.mac[:64], body.version[:32], body.current_url[:1000], json.dumps(body.info)[:8000],
             time.time(), p.id))
        pending = c.execute("SELECT COUNT(*) FROM node_commands WHERE node_id=? AND status='queued'", (p.id,)).fetchone()[0]
    await hub.publish("fleet", "node.heartbeat", {"id": p.id})
    return {"ok": True, "pending_commands": pending}


@router.get("/api/nodes/me")
def node_me(p: Principal = Depends(require_node)):
    with db.ro() as c:
        n = c.execute("SELECT * FROM nodes WHERE id=?", (p.id,)).fetchone()
        room = c.execute("SELECT id,name,short_name FROM rooms WHERE id=?", (n["room_id"],)).fetchone() if n["room_id"] else None
        return {"node": node_out(n), "room": dict(room) if room else None}


@router.get("/api/nodes/commands")
def poll_commands(p: Principal = Depends(require_node)):
    with db.ro() as c:
        cmds = db.rows(c.execute("SELECT id,kind,payload_json,created_at FROM node_commands WHERE node_id=? AND status='queued' ORDER BY id", (p.id,)))
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
        return [node_out(r) for r in rows if site_ok(p, r["site_id"])]


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
            c.execute("UPDATE nodes SET name=?, room_id=?, site_id=?, kind=? WHERE id=?",
                      (norm_host(body.name) if body.name else n["name"], body.room_id,
                       body.site_id or n["site_id"], body.kind or n["kind"], node_id))
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
