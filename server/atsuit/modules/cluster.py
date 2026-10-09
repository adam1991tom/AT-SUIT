"""Helper servers. One AT-SUIT server is the main one: it holds the database,
the rooms and every screen. Any other machine running the same image with
ATSUIT_ROLE=helper can join it with a one-time code and take work off it.

The first job helpers take is the heavy one: live caption speech recognition.
When a laptop starts its microphone, the main server places that room's
recogniser on whichever machine has the most room (or on a helper first, in
"offload" mode). Audio after the room's gain/EQ goes to the helper over one
websocket the helper opens to the main server, so helpers need no open port
of their own. If a helper drops mid-sentence, the room moves to another
helper or back to the main server without the laptop noticing.

Wire format on /ws/helper (helper -> main connection):
  main -> helper  text   {"t": "config", "vocabulary": [...], "hotwords_score": x}
                         {"t": "open", "job": n, "options": {...}}
                         {"t": "close", "job": n}
                  binary 4-byte big-endian job id + 16 kHz mono int16 PCM
  helper -> main  text   {"t": "hello"|"stats", "name", "version", "capacity", "load", "asr"}
                         {"t": "events", "job": n, "events": [...]}
                         {"t": "error", "job": n, "message": str}
"""
from __future__ import annotations

import asyncio
import json
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .. import VERSION, asr, config, db
from ..hub import hub
from ..security import Principal, require_admin, token_hash

router = APIRouter()
ws_router = APIRouter()

MODES = ("share", "offload")
CODE_MINUTES = 15
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I to misread
JOIN_TRIES, JOIN_WINDOW = 10, 300
_join_fails: dict[str, list[float]] = {}


class HelperGone(Exception):
    """The helper running a room's recogniser went away."""


class HelperLink:
    def __init__(self, hid: int, name: str, ws: WebSocket) -> None:
        self.id, self.name, self.ws = hid, name, ws
        self.version = ""
        self.capacity = 0
        self.load = 0.0
        self.asr = "off"
        self.connected = time.time()
        self.seen = time.time()
        self.jobs: dict[int, asyncio.Queue] = {}
        self.alive = True
        self.send_lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        return self.alive and self.asr == "ready" and self.capacity > 0

    def out(self) -> dict:
        return {"id": self.id, "name": self.name, "online": self.alive, "version": self.version,
                "capacity": self.capacity, "rooms": len(self.jobs), "load": round(self.load, 2),
                "speech": self.asr, "since": self.connected}

    async def send_text(self, msg: dict) -> None:
        async with self.send_lock:
            await self.ws.send_text(json.dumps(msg))

    async def send_bytes(self, data: bytes) -> None:
        async with self.send_lock:
            await self.ws.send_bytes(data)


links: dict[int, HelperLink] = {}
_job_seq = 0


def reset() -> None:
    links.clear()
    _join_fails.clear()


def mode(c=None) -> str:
    if c is None:
        with db.ro() as c:
            return mode(c)
    m = db.get_setting(c, "cluster.mode", "share")
    return m if m in MODES else "share"


# --------------------------------------------------------------- placement --
def local_rooms() -> int:
    from .captions import rooms
    return sum(1 for r in rooms.values() if r.ws and getattr(r.session, "where", 0) == 0)


def local_ready() -> bool:
    return asr.engine.state == "ready"


def capacity() -> int:
    """Rooms the whole system can caption at once."""
    total = config.cfg.asr_max_rooms if local_ready() else 0
    return total + sum(h.capacity for h in links.values() if h.ready)


def _choose(exclude: set[int] = frozenset()) -> HelperLink | None:
    """The helper to use, or None for this server. Lowest share of its
    capacity in use wins; in offload mode any helper with room beats us."""
    helpers = [h for h in links.values() if h.ready and h.id not in exclude and len(h.jobs) < h.capacity]
    best = min(helpers, key=lambda h: (len(h.jobs) / h.capacity, h.load), default=None)
    local_ok = local_ready() and local_rooms() < config.cfg.asr_max_rooms
    if best is None:
        return None if local_ok else False  # type: ignore[return-value]
    if not local_ok or mode() == "offload":
        return best
    local_share = local_rooms() / max(1, config.cfg.asr_max_rooms)
    return best if len(best.jobs) / best.capacity < local_share else None


class LocalSession:
    where = 0
    where_name = "main"

    def __init__(self, options: dict) -> None:
        self.s = asr.engine.session(options)

    async def feed(self, samples: np.ndarray) -> list[dict]:
        return await asyncio.to_thread(self.s.feed_samples, samples)

    async def close(self) -> None:
        pass


class RemoteSession:
    def __init__(self, link: HelperLink, options: dict) -> None:
        global _job_seq
        _job_seq = (_job_seq + 1) % 0x7FFFFFFF
        self.link, self.job, self.options = link, _job_seq, options
        self.where, self.where_name = link.id, link.name
        self.q: asyncio.Queue = asyncio.Queue()
        link.jobs[self.job] = self.q

    async def open(self) -> "RemoteSession":
        try:
            await self.link.send_text({"t": "open", "job": self.job, "options": self.options})
        except Exception as exc:
            self.link.jobs.pop(self.job, None)
            raise HelperGone() from exc
        return self

    async def feed(self, samples: np.ndarray) -> list[dict]:
        if not self.link.alive:
            raise HelperGone()
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        try:
            await self.link.send_bytes(struct.pack(">I", self.job) + pcm)
        except Exception as exc:
            raise HelperGone() from exc
        out: list[dict] = []
        while not self.q.empty():
            item = self.q.get_nowait()
            if item is None:
                raise HelperGone()
            if isinstance(item, Exception):
                raise item
            out += item
        return out

    async def close(self) -> None:
        self.link.jobs.pop(self.job, None)
        if self.link.alive:
            try:
                await self.link.send_text({"t": "close", "job": self.job})
            except Exception:
                pass


async def _place(options: dict, exclude: set[int] = frozenset()):
    tried = set(exclude)
    while True:
        choice = _choose(tried)
        if choice is False:
            raise RuntimeError("No server has room to caption another room right now"
                               if local_ready() or links else asr.engine.detail or "Speech recognition isn't ready")
        if choice is None:
            return LocalSession(options)
        try:
            return await RemoteSession(choice, options).open()
        except HelperGone:
            tried.add(choice.id)


class PlacedSession:
    """What a room's microphone talks to. Moves the recogniser to another
    machine if the one running it goes away."""

    def __init__(self, room_id: int, options: dict) -> None:
        self.room_id, self.options, self.inner = room_id, options, None

    @property
    def where(self) -> int:
        return self.inner.where if self.inner else -1

    @property
    def where_name(self) -> str:
        return self.inner.where_name if self.inner else ""

    async def start(self) -> "PlacedSession":
        self.inner = await _place(self.options)
        return self

    async def feed(self, samples: np.ndarray) -> list[dict]:
        try:
            return await self.inner.feed(samples)
        except HelperGone:
            gone = self.inner.where
            await self.inner.close()
            self.inner = await _place(self.options, {gone})
            await hub.publish(f"captions:{self.room_id}", "moved", {"server": self.where_name})
            return await self.inner.feed(samples)

    async def close(self) -> None:
        if self.inner:
            await self.inner.close()


async def start_session(room_id: int, options: dict) -> PlacedSession:
    return await PlacedSession(room_id, options).start()


async def push_config(vocabulary: list[str], score: float) -> None:
    for h in list(links.values()):
        try:
            await h.send_text({"t": "config", "vocabulary": vocabulary, "hotwords_score": score})
        except Exception:
            pass


def _config_msg() -> dict:
    from .captions import all_vocabulary, hotwords_score
    with db.ro() as c:
        score = hotwords_score(c)
    return {"t": "config", "vocabulary": all_vocabulary(), "hotwords_score": score}


# ---------------------------------------------------------------- the link --
@ws_router.websocket("/ws/helper")
async def helper_ws(ws: WebSocket):
    auth = ws.headers.get("authorization", "")
    secret = auth[7:] if auth.lower().startswith("bearer ") else ""
    with db.ro() as c:
        row = c.execute("SELECT * FROM helpers WHERE secret_hash=?", (token_hash(secret),)).fetchone() if secret else None
    if not row:
        await ws.close(code=4403)
        return
    await ws.accept()
    old = links.get(row["id"])
    if old:  # the helper reconnected before we noticed it had gone
        await _drop(old)
    link = links[row["id"]] = HelperLink(row["id"], row["name"], ws)
    with db.tx() as c:
        c.execute("UPDATE helpers SET last_seen=? WHERE id=?", (db.now_iso(), row["id"]))
    try:
        await link.send_text(_config_msg())
        await hub.publish("fleet", "helpers", {})
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            text = msg.get("text")
            if not text:
                continue
            m = json.loads(text)
            link.seen = time.time()
            t = m.get("t")
            if t in ("hello", "stats"):
                link.version = str(m.get("version", ""))[:40]
                link.capacity = max(0, min(64, int(m.get("capacity", 0) or 0)))
                link.load = float(m.get("load", 0) or 0)
                link.asr = str(m.get("asr", "off"))[:20]
                if t == "hello":
                    await hub.publish("fleet", "helpers", {})
            elif t == "events" and m.get("job") in link.jobs:
                await link.jobs[m["job"]].put([e for e in m.get("events", []) if isinstance(e, dict)])
            elif t == "error" and m.get("job") in link.jobs:
                await link.jobs[m["job"]].put(HelperGone())
    except (WebSocketDisconnect, ValueError, RuntimeError, TypeError):
        pass
    finally:
        if links.get(link.id) is link:
            await _drop(link)
            await hub.publish("fleet", "helpers", {})


async def _drop(link: HelperLink) -> None:
    link.alive = False
    links.pop(link.id, None)
    for q in link.jobs.values():
        q.put_nowait(None)
    try:
        await link.ws.close(code=4010)
    except Exception:
        pass


# --------------------------------------------------------------------- api --
class JoinIn(BaseModel):
    code: str = Field(min_length=4, max_length=20)
    name: str = Field(min_length=1, max_length=60)


@router.post("/api/helpers/join")
async def join(body: JoinIn, request: Request):
    """A helper trades the one-time code an admin made for its own secret."""
    ip = request.client.host if request.client else ""
    now = time.time()
    fails = [t for t in _join_fails.get(ip, []) if now - t < JOIN_WINDOW]
    if len(fails) >= JOIN_TRIES:
        raise HTTPException(429, "Too many wrong codes. Wait a few minutes and try again.")
    code = body.code.strip().upper().replace("-", "").replace(" ", "")
    with db.tx() as c:
        want = db.get_setting(c, "cluster.join_hash", "")
        until = db.get_setting(c, "cluster.join_until", "")
        if not want or not secrets.compare_digest(want, token_hash(code)) or until < db.now_iso():
            _join_fails[ip] = fails + [now]
            raise HTTPException(403, "That code is wrong or has run out. Make a new one in Servers.")
        c.execute("DELETE FROM settings WHERE key IN ('cluster.join_hash','cluster.join_until')")
        name = body.name.strip()
        if c.execute("SELECT 1 FROM helpers WHERE name=?", (name,)).fetchone():
            name = f"{name} ({secrets.token_hex(2)})"
        secret = secrets.token_urlsafe(32)
        cur = c.execute("INSERT INTO helpers(name, secret_hash, added_at) VALUES(?,?,?)",
                        (name, token_hash(secret), db.now_iso()))
        db.audit(c, f"helper:{name}", "helper.join", ip)
    await hub.publish("fleet", "helpers", {})
    return {"id": cur.lastrowid, "name": name, "secret": secret}


def _main_out() -> dict:
    return {"id": 0, "name": "Main server", "online": True, "version": VERSION,
            "capacity": config.cfg.asr_max_rooms, "rooms": local_rooms(), "load": round(_loadavg(), 2),
            "speech": asr.engine.state, "main": True}


def _loadavg() -> float:
    try:
        import os
        return os.getloadavg()[0] / (os.cpu_count() or 1)
    except (OSError, AttributeError):
        return 0.0


@router.get("/api/admin/helpers")
def list_helpers(p: Principal = Depends(require_admin)):
    with db.ro() as c:
        rows = db.rows(c.execute("SELECT id,name,added_at,last_seen FROM helpers ORDER BY name"))
        until = db.get_setting(c, "cluster.join_until", "")
        m = mode(c)
    for r in rows:
        live = links.get(r["id"])
        r.update(live.out() if live else {"online": False, "rooms": 0, "capacity": 0, "speech": "", "load": 0, "version": ""})
    from .captions import rooms
    placed = [{"room_id": rid, "server": getattr(st.session, "where_name", "")}
              for rid, st in rooms.items() if st.ws and st.session]
    return {"mode": m, "main": _main_out(), "helpers": rows, "capacity": capacity(), "placed": placed,
            "code_open": bool(until and until > db.now_iso())}


@router.post("/api/admin/helpers/code")
def make_code(p: Principal = Depends(require_admin)):
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    until = (datetime.now(timezone.utc) + timedelta(minutes=CODE_MINUTES)).isoformat()
    with db.tx() as c:
        db.set_setting(c, "cluster.join_hash", token_hash(code))
        db.set_setting(c, "cluster.join_until", until)
        db.audit(c, p.name, "helper.code")
    return {"code": f"{code[:4]}-{code[4:]}", "until": until, "minutes": CODE_MINUTES}


class ModeIn(BaseModel):
    mode: str


@router.put("/api/admin/helpers/mode")
def set_mode(body: ModeIn, p: Principal = Depends(require_admin)):
    if body.mode not in MODES:
        raise HTTPException(400, "Pick share or offload")
    with db.tx() as c:
        db.set_setting(c, "cluster.mode", body.mode)
        db.audit(c, p.name, "helper.mode", body.mode)
    return {"mode": body.mode}


@router.delete("/api/admin/helpers/{hid}")
async def remove_helper(hid: int, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        row = c.execute("SELECT name FROM helpers WHERE id=?", (hid,)).fetchone()
        if not row:
            raise HTTPException(404, "Helper not found")
        c.execute("DELETE FROM helpers WHERE id=?", (hid,))
        db.audit(c, p.name, "helper.remove", row["name"])
    if hid in links:  # its rooms move to another machine on their next audio
        await _drop(links[hid])
    await hub.publish("fleet", "helpers", {})
    return {"ok": True}
