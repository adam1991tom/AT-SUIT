"""Passwords, sessions, node tokens, API keys and encryption at rest."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request, WebSocket

from . import config, db

SESSION_COOKIE = "atsuit_session"
# A tech laptop signed in by its own node token (the tech only types their name).
NODE_COOKIE = "atsuit_node"
ROLES = ("admin", "tech", "viewer")
_ROLE_RANK = {"viewer": 0, "tech": 1, "admin": 2}


# Same format as AT-RoomComms and AT-Presenter, so migrated accounts keep their passwords.
def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


# ------------------------------------------------------------- encryption --
_fernet: Fernet | None = None


def fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key_file = config.cfg.data / "secret.key"
        if not key_file.exists():
            key_file.write_bytes(Fernet.generate_key())
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
        _fernet = Fernet(key_file.read_bytes().strip())
    return _fernet


def reset_keys() -> None:
    global _fernet
    _fernet = None


def encrypt(value: str) -> str:
    return fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


# ------------------------------------------------------------- principals --
@dataclass
class Principal:
    kind: str  # account | node | apikey
    id: int
    name: str
    role: str = "viewer"
    site_id: int | None = None
    room_id: int | None = None

    def at_least(self, role: str) -> bool:
        return _ROLE_RANK.get(self.role, -1) >= _ROLE_RANK[role]

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.name}"


def create_session(c, account_id: int) -> str:
    token = new_token()
    now = datetime.now(timezone.utc)
    c.execute(
        "INSERT INTO sessions(token_hash,account_id,created_at,expires_at) VALUES(?,?,?,?)",
        (token_hash(token), account_id, now.isoformat(), (now + timedelta(hours=config.cfg.session_hours)).isoformat()),
    )
    return token


def _from_session(c, token: str) -> Principal | None:
    r = c.execute(
        "SELECT a.id,a.username,a.display_name,a.role,a.site_id,s.expires_at FROM sessions s "
        "JOIN accounts a ON a.id=s.account_id WHERE s.token_hash=? AND a.active=1",
        (token_hash(token),),
    ).fetchone()
    if not r or r["expires_at"] < datetime.now(timezone.utc).isoformat():
        return None
    return Principal("account", r["id"], r["display_name"] or r["username"], r["role"], r["site_id"])


def _from_node(c, token: str) -> Principal | None:
    r = c.execute("SELECT id,name,kind,operator,site_id,room_id FROM nodes WHERE token_hash=?", (token_hash(token),)).fetchone()
    if not r:
        return None
    # On a tech laptop the person at it is who chat and help requests come from.
    name = r["operator"] if r["kind"] == "tech" and r["operator"] else r["name"]
    return Principal("node", r["id"], name, "tech", r["site_id"], r["room_id"])


def _from_apikey(c, key: str) -> Principal | None:
    r = c.execute("SELECT id,name FROM api_keys WHERE key_hash=?", (token_hash(key),)).fetchone()
    return Principal("apikey", r["id"], r["name"], "tech") if r else None


def resolve(headers, cookies, query) -> Principal | None:
    auth = headers.get("authorization", "")
    with db.ro() as c:
        if auth.lower().startswith("node "):
            return _from_node(c, auth[5:].strip())
        if headers.get("x-api-key"):
            return _from_apikey(c, headers["x-api-key"])
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else cookies.get(SESSION_COOKIE)
        if token:
            p = _from_session(c, token)
            if p:
                return p
        if cookies.get(NODE_COOKIE):
            return _from_node(c, cookies[NODE_COOKIE])
        if query.get("node_token"):
            return _from_node(c, query["node_token"])
        if query.get("token"):
            return _from_session(c, query["token"])
    return None


def principal(request: Request) -> Principal | None:
    return resolve(request.headers, request.cookies, request.query_params)


def ws_principal(ws: WebSocket) -> Principal | None:
    return resolve(ws.headers, ws.cookies, ws.query_params)


def require(role: str = "viewer", kinds: tuple[str, ...] = ("account", "node", "apikey")):
    def dep(request: Request) -> Principal:
        p = principal(request)
        if not p:
            raise HTTPException(401, "Sign in required")
        if p.kind not in kinds or not p.at_least(role):
            raise HTTPException(403, "Not allowed")
        return p

    return dep


require_admin = require("admin", ("account",))
require_tech = require("tech")
require_user = require("viewer")
require_node = require("tech", ("node",))
