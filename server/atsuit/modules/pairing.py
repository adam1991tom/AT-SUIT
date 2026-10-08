"""Pairing codes for screens: the simple way to add a Linux screen laptop.

An unpaired screen (the Linux screen agent, or any browser opened on /screen)
asks for a code and shows it full screen, big. The tech types that code into
"Add a screen" in their workspace and picks what it shows (a timer view, a
built view, captions in any layout, a test pattern or a web page). The server
adds the screen to the tech's room and hands the screen its token; the screen
then shows the layout straight away. No enrolment code, no typing on the
screen itself.

The enrolment-code path (/api/nodes/enrol) still works for scripted installs.

Pending codes live in memory: they last 15 minutes and a screen asks for a
fresh one when its code runs out (or the server restarts)."""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db
from ..hub import hub
from ..security import Principal, new_token, require_tech, token_hash
from .core import require_module, room_or_404, site_ok
from .fleet import _check_node_limit, caption_views, check_view, norm_host, work_day

router = APIRouter(dependencies=[Depends(require_module("fleet"))])

CODE_DIGITS = 6
CODE_TTL = 15 * 60      # an unclaimed code lasts this long
PICKUP_TTL = 10 * 60    # after pairing, the screen has this long to collect its token
MAX_PENDING = 500
MAX_PER_IP = 8


@dataclass
class Pending:
    code: str
    secret: str
    created: float
    expires: float
    ip: str = ""
    hint: str = ""
    info: dict = field(default_factory=dict)
    token: str | None = None
    node_id: int | None = None
    name: str = ""
    room_id: int | None = None
    view: str = ""
    by: str = ""


_lock = threading.Lock()
_by_secret: dict[str, Pending] = {}
_by_code: dict[str, str] = {}


def _sweep(now: float) -> None:
    for s, p in list(_by_secret.items()):
        if p.expires < now:
            _by_secret.pop(s, None)
            if _by_code.get(p.code) == s:
                _by_code.pop(p.code, None)


def normalise_code(code: str) -> str:
    return "".join(ch for ch in str(code or "") if ch.isdigit())


def pretty(code: str) -> str:
    return f"{code[:3]} {code[3:]}" if len(code) == 6 else code


def reset() -> None:
    with _lock:
        _by_secret.clear()
        _by_code.clear()


def new_pending(ip: str = "", hint: str = "", info: dict | None = None, now: float | None = None) -> Pending:
    now = time.time() if now is None else now
    with _lock:
        _sweep(now)
        if len(_by_secret) >= MAX_PENDING:
            raise HTTPException(429, "Too many screens waiting to be added; try again in a few minutes")
        mine = sorted((p for p in _by_secret.values() if p.ip == ip and p.token is None), key=lambda p: p.created)
        for old in mine[:max(0, len(mine) - MAX_PER_IP + 1)]:  # a screen that keeps asking drops its oldest code
            _by_secret.pop(old.secret, None)
            _by_code.pop(old.code, None)
        while True:
            code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
            if code not in _by_code and code[0] != "0":
                break
        p = Pending(code=code, secret=secrets.token_urlsafe(24), created=now, expires=now + CODE_TTL,
                    ip=ip, hint=norm_host(hint)[:40], info=dict(info or {}))
        _by_secret[p.secret] = p
        _by_code[code] = p.secret
        return p


def lookup_secret(secret: str, now: float | None = None) -> Pending | None:
    now = time.time() if now is None else now
    with _lock:
        _sweep(now)
        return _by_secret.get(secret or "")


def lookup_code(code: str, now: float | None = None) -> Pending | None:
    now = time.time() if now is None else now
    with _lock:
        _sweep(now)
        s = _by_code.get(normalise_code(code))
        p = _by_secret.get(s) if s else None
        return p if p and p.token is None else None


# ------------------------------------------------------------ screen-facing --
class PairRequest(BaseModel):
    name: str = Field("", max_length=64)  # the agent sends the hostname
    info: dict = {}


@router.post("/api/screens/pair/request")
def pair_request(body: PairRequest, request: Request):
    """Public: an unpaired screen asks for a code to show."""
    p = new_pending(request.client.host if request.client else "", body.name,
                    {k: str(v)[:200] for k, v in list(body.info.items())[:10]})
    return {"code": p.code, "display": pretty(p.code), "secret": p.secret,
            "expires_in": int(p.expires - time.time()), "poll_seconds": 2}


@router.get("/api/screens/pair/status")
def pair_status(secret: str):
    """Public, by the screen's secret: still waiting, or paired (with the token)."""
    p = lookup_secret(secret)
    if not p:
        raise HTTPException(404, "That code has run out; ask for a new one")
    if not p.token:
        return {"state": "waiting", "code": p.code, "display": pretty(p.code), "expires_in": int(p.expires - time.time())}
    return {"state": "paired", "token": p.token, "node_id": p.node_id, "name": p.name,
            "room_id": p.room_id, "view": p.view}


# -------------------------------------------------------------- tech-facing --
class PairIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    room_id: int
    view: str = Field("", max_length=1100)
    name: str = Field("", max_length=64)


def can_pair(p: Principal) -> None:
    if p.kind == "node":
        with db.ro() as c:
            n = c.execute("SELECT kind FROM nodes WHERE id=?", (p.id,)).fetchone()
        if not n or n["kind"] != "tech":
            raise HTTPException(403, "Only a tech can add a screen")


@router.post("/api/screens/pair")
async def pair(body: PairIn, p: Principal = Depends(require_tech)):
    """A tech types the code a screen is showing and picks its layout. The
    screen joins the tech's room and shows that layout."""
    can_pair(p)
    pend = lookup_code(body.code)
    if not pend:
        raise HTTPException(404, "No screen is showing that code. Check the numbers, or wait for the screen to show a new one")
    with _lock:  # claim the code, so two techs typing it at once can't both add the screen
        if pend.token is not None or _by_secret.get(pend.secret) is not pend:
            raise HTTPException(409, "That screen was just added by someone else")
        pend.token = ""
        _by_code.pop(pend.code, None)
    try:
        node_id, name, room, view, token = _add_screen(body, pend, p)
    except Exception:
        with _lock:
            pend.token = None
            _by_code[pend.code] = pend.secret
        raise
    with _lock:
        pend.token, pend.node_id, pend.name, pend.room_id, pend.view, pend.by = token, node_id, name, room["id"], view, p.name
        pend.expires = time.time() + PICKUP_TTL
    await hub.publish("fleet", "node.changed", {"id": node_id})
    await hub.publish(f"room:{room['id']}", "screen.paired", {"id": node_id, "name": name, "view": view})
    return {"ok": True, "node_id": node_id, "name": name, "room_id": room["id"], "view": view}


def _add_screen(body: PairIn, pend: Pending, p: Principal):
    token = new_token("atn_")
    with db.tx() as c:
        room = room_or_404(c, body.room_id, p)
        view = check_view(c, body.view)
        name = norm_host("-".join(body.name.split())) or pend.hint or f"SCREEN-{pend.code}"
        existing = c.execute("SELECT * FROM nodes WHERE name=?", (name,)).fetchone()
        if existing and existing["kind"] != "kiosk":
            base, n = name[:58], 2
            while c.execute("SELECT 1 FROM nodes WHERE name=?", (f"{base}-{n}",)).fetchone():
                n += 1
            name, existing = f"{base}-{n}", None
        day = work_day(c, room["site_id"])
        if existing:  # the same screen paired again (re-imaged, or its token lost)
            if not site_ok(p, existing["site_id"]):
                raise HTTPException(409, "A screen with that name belongs to another site; give this one a different name")
            node_id = existing["id"]
            c.execute("UPDATE nodes SET token_hash=?, site_id=?, kind='kiosk', legacy=0, room_id=?, room_day=?, screen_view=? "
                      "WHERE id=?", (token_hash(token), room["site_id"], room["id"], day, view, node_id))
        else:
            _check_node_limit(c)
            node_id = c.execute(
                "INSERT INTO nodes(name,site_id,kind,token_hash,room_id,room_day,screen_view,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (name, room["site_id"], "kiosk", token_hash(token), room["id"], day, view, db.now_iso())).lastrowid
        db.audit(c, p.name, "node.pair", f"{name}: {room['name']} {view}")
    return node_id, name, dict(room), view, token


@router.get("/api/screens/layouts")
def layouts(p: Principal = Depends(require_tech)):
    """Everything a screen can show, grouped for the Add a screen picker."""
    from .timers import list_views

    with db.ro() as c:
        views = list_views(c, tests=True)
    timer = [v for v in views if not str(v["id"]).startswith("screentest:")]
    tests = [v for v in views if str(v["id"]).startswith("screentest:")]
    return {"groups": [{"name": "Timer", "views": timer}, {"name": "Captions", "views": caption_views()},
                       {"name": "Test patterns", "views": tests}],
            "web_page": True}
