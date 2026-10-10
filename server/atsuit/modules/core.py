"""Setup wizard, sign-in, sites, rooms, accounts, settings, licence, API keys."""
from __future__ import annotations

import io
import json
import os
import platform
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import zipfile
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import AfterValidator, BaseModel, Field

from .. import VERSION, config, db, licence
from ..hub import hub
from ..security import (
    ROLES,
    SESSION_COOKIE,
    Principal,
    create_session,
    hash_password,
    new_token,
    require_manager,
    principal,
    require_admin,
    require_user,
    token_hash,
    verify_password,
)

router = APIRouter()

DEFAULT_BRANDING = {
    "product_name": "AT-SUIT",
    "organisation": "",
    "accent": "#FF7A1A",  # AT-SUIT orange
    "logo_url": "",
    "support_contact": "",
}
OLD_DEFAULT_ACCENT = "#4f7cff"  # before the AT-SUIT logo; servers set up then still store it

# How every staff page looks (the console, the tech workspace, setup and the presenter
# page). One look for the whole site, set by an admin; stage screens keep their own.
APPEARANCE_CHOICES = {
    "theme": ("dark", "light", "modern", "futuristic"),
    "density": ("compact", "normal", "roomy"),
    "corners": ("square", "rounded", "round"),
    "font": ("system", "brand", "rounded", "mono"),
    "motion": ("on", "off"),
}
DEFAULT_APPEARANCE = {"theme": "dark", "density": "normal", "corners": "rounded", "font": "system", "motion": "on"}


def get_appearance(c) -> dict:
    saved = db.get_setting(c, "appearance", {})
    return {k: saved.get(k) if saved.get(k) in choices else DEFAULT_APPEARANCE[k] for k, choices in APPEARANCE_CHOICES.items()}


def get_branding(c) -> dict:
    b = {**DEFAULT_BRANDING, **db.get_setting(c, "branding", {})}
    if b.get("accent", "").lower() == OLD_DEFAULT_ACCENT:
        b["accent"] = DEFAULT_BRANDING["accent"]
    b["appearance"] = get_appearance(c)
    return b


DEFAULT_MODULES = {m: True for m in licence.ALL_MODULES}
STARTED = time.time()


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "site"


def enrol_code() -> str:
    return "-".join(secrets.token_hex(2).upper() for _ in range(3))


def is_setup(c) -> bool:
    return bool(db.get_setting(c, "setup_complete", False))


def modules_enabled(c) -> dict:
    lic = licence.current(c)
    chosen = {**DEFAULT_MODULES, **db.get_setting(c, "modules", {})}
    return {m: bool(chosen.get(m)) and lic.allows(m) for m in licence.ALL_MODULES}


def require_module(name: str):
    def dep():
        with db.ro() as c:
            if not modules_enabled(c).get(name):
                raise HTTPException(404, f"The {name} module is turned off")

    return dep


def site_ok(p: Principal, site_id: int | None) -> bool:
    return p.site_id is None or site_id == p.site_id


def room_or_404(c, room_id: int, p: Principal | None = None) -> sqlite3.Row:
    r = c.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not r or (p is not None and not site_ok(p, r["site_id"])):
        raise HTTPException(404, "Room not found")
    return r


def create_room(c, site_id: int, name: str, short_name: str = "", sort: int = 0) -> int:
    cur = c.execute(
        "INSERT INTO rooms(site_id,name,short_name,sort) VALUES(?,?,?,?)",
        (site_id, name.strip(), (short_name or name).strip()[:12], sort),
    )
    rid = cur.lastrowid
    c.execute("INSERT INTO channels(site_id,room_id,kind,name) VALUES(?,?,?,?)", (site_id, rid, "room", name.strip()))
    return rid


def create_site(c, name: str, tz: str = "Europe/London") -> int:
    slug = slugify(name)
    base, n = slug, 2
    while c.execute("SELECT 1 FROM sites WHERE slug=?", (slug,)).fetchone():
        slug, n = f"{base}-{n}", n + 1
    cur = c.execute("INSERT INTO sites(name,slug,timezone,enrol_code) VALUES(?,?,?,?)", (name.strip(), slug, tz, enrol_code()))
    sid = cur.lastrowid
    c.execute("INSERT INTO channels(site_id,room_id,kind,name) VALUES(?,?,?,?)", (sid, None, "site", "All crew"))
    return sid


# ------------------------------------------------------------------ health --
@router.get("/api/health")
def health():
    with db.ro() as c:
        setup = is_setup(c)
    return {"ok": True, "version": VERSION, "setup_complete": setup}


# ------------------------------------------------------------------- setup --
def _time_zone(v: str) -> str:
    """A real IANA time zone (Europe/London), or every venue clock would quietly run on UTC."""
    v = v.strip()
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Unknown time zone '{v}'. Use a name like Europe/London.")
    return v


TimeZone = Annotated[str, AfterValidator(_time_zone)]


class SetupIn(BaseModel):
    organisation: str = Field(min_length=1, max_length=120)
    site_name: str = Field(min_length=1, max_length=120)
    timezone: TimeZone = "Europe/London"
    admin_username: str = Field(min_length=2, max_length=64)
    admin_password: str = Field(min_length=8, max_length=256)
    admin_display_name: str = ""
    rooms: list[str] = []
    licence_key: str = ""


@router.get("/api/setup/status")
def setup_status():
    with db.ro() as c:
        return {"setup_complete": is_setup(c), "version": VERSION}


@router.post("/api/setup")
def setup(body: SetupIn, response: Response):
    lic = licence.parse(body.licence_key) if body.licence_key.strip() else None
    if lic and not lic.valid:
        raise HTTPException(400, lic.reason)
    with db.tx() as c:
        if is_setup(c):
            raise HTTPException(409, "Setup is already complete")
        site_id = create_site(c, body.site_name, body.timezone)
        for i, name in enumerate(n for n in body.rooms if n.strip()):
            create_room(c, site_id, name, sort=i)
        cur = c.execute(
            "INSERT INTO accounts(username,password_hash,display_name,role,site_id,created_at) VALUES(?,?,?,?,?,?)",
            (body.admin_username.strip(), hash_password(body.admin_password),
             body.admin_display_name.strip() or body.admin_username.strip(), "admin", None, db.now_iso()),
        )
        db.set_setting(c, "branding", {**DEFAULT_BRANDING, "organisation": body.organisation.strip()})
        db.set_setting(c, "modules", DEFAULT_MODULES)
        db.set_setting(c, "setup_complete", True)
        if lic:
            db.set_setting(c, "licence_key", body.licence_key.strip())
            licence.installed(c, lic, body.admin_username.strip())
        db.audit(c, body.admin_username, "setup.complete", body.site_name)
        token = create_session(c, cur.lastrowid)
    _set_cookie(response, token)
    return {"ok": True, "token": token}


# -------------------------------------------------------------------- auth --
class LoginIn(BaseModel):
    username: str
    password: str
    shared: bool = False  # signing in on a tech laptop: a short session that ends with the browser


SHARED_MINUTES = 30


def _set_cookie(response: Response, token: str, shared: bool = False) -> None:
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        max_age=None if shared else config.cfg.session_hours * 3600)


# Wrong passwords: after LOGIN_TRIES in LOGIN_WINDOW seconds from one address for one
# username, that address waits until the window clears. Every failure is in the audit log.
LOGIN_TRIES, LOGIN_WINDOW = 8, 300
_login_fails: dict[tuple[str, str], list[float]] = {}


@router.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    user = body.username.strip()
    key, now = (request.client.host if request.client else "", user.lower()), time.monotonic()
    fails = [t for t in _login_fails.get(key, []) if now - t < LOGIN_WINDOW]
    if len(fails) >= LOGIN_TRIES:
        wait = int(LOGIN_WINDOW - (now - fails[0])) + 1
        raise HTTPException(429, f"Too many wrong passwords. Try again in {max(1, wait // 60)} min.")
    with db.tx() as c:
        a = c.execute("SELECT * FROM accounts WHERE username=? AND active=1", (user,)).fetchone()
        ok = bool(a) and verify_password(body.password, a["password_hash"])
        if not ok:
            db.audit(c, user[:64] or "?", "auth.fail", key[0])
    if not ok:
        _login_fails[key] = fails + [now]
        if len(_login_fails) > 10000:  # don't let made-up usernames grow this forever
            _login_fails.clear()
        raise HTTPException(401, "Wrong username or password")
    _login_fails.pop(key, None)
    with db.tx() as c:
        token = create_session(c, a["id"], minutes=SHARED_MINUTES if body.shared else None)
        db.audit(c, a["username"], "auth.login", "shared computer" if body.shared else "")
    _set_cookie(response, token, body.shared)
    return {"ok": True, "token": token}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with db.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/api/auth/me")
def me(request: Request):
    p = principal(request)
    if not p:
        raise HTTPException(401, "Sign in required")
    return {"kind": p.kind, "id": p.id, "name": p.name, "role": p.role, "site_id": p.site_id, "room_id": p.room_id}


class PasswordIn(BaseModel):
    current: str
    new: str = Field(min_length=8, max_length=256)


@router.post("/api/auth/password")
def change_password(body: PasswordIn, p: Principal = Depends(require_user)):
    if p.kind != "account":
        raise HTTPException(400, "Only accounts have passwords")
    with db.tx() as c:
        a = c.execute("SELECT password_hash FROM accounts WHERE id=?", (p.id,)).fetchone()
        if not verify_password(body.current, a["password_hash"]):
            raise HTTPException(400, "Current password is wrong")
        c.execute("UPDATE accounts SET password_hash=? WHERE id=?", (hash_password(body.new), p.id))
    return {"ok": True}


# --------------------------------------------------------------- bootstrap --
@router.get("/api/public/branding")
def public_branding():
    with db.ro() as c:
        return get_branding(c)


@router.get("/api/bootstrap")
def bootstrap(p: Principal = Depends(require_user)):
    with db.ro() as c:
        sites = db.rows(c.execute("SELECT id,name,slug,timezone FROM sites ORDER BY name"))
        rooms = db.rows(c.execute("SELECT * FROM rooms ORDER BY site_id,sort,name"))
        if p.site_id is not None:
            sites = [s for s in sites if s["id"] == p.site_id]
            rooms = [r for r in rooms if r["site_id"] == p.site_id]
        lic = licence.current(c)
        return {
            "version": VERSION,
            "me": {"kind": p.kind, "id": p.id, "name": p.name, "role": p.role, "site_id": p.site_id, "room_id": p.room_id},
            "branding": get_branding(c),
            "modules": modules_enabled(c),
            # Licence details are for admins; everyone else only needs the modules,
            # whether it's locked, and a warning while the subscription runs out.
            "licence": {"licensee": lic.licensee, "edition": lic.edition, "valid": lic.valid, "reason": lic.reason,
                        "state": lic.state, "expires": lic.expires, "grace_until": lic.grace_until}
            if p.role == "admin" else {},
            "licence_locked": licence.locked(c),
            "licence_notice": licence.notice(c, p.role),
            "sites": sites,
            "rooms": rooms,
        }


# ------------------------------------------------------- workspace layout --
# The layout every tech laptop starts with (the windows of the workspace). An
# admin sets it from their own workspace; techs can still change theirs.
class WorkspaceLayoutIn(BaseModel):
    layout: dict | None = None


@router.get("/api/workspace/layout")
def workspace_layout(p: Principal = Depends(require_user)):
    with db.ro() as c:
        raw = db.get_setting(c, "workspace.layout", "")
    try:
        return {"layout": json.loads(raw) if raw else None}
    except ValueError:
        return {"layout": None}


@router.put("/api/workspace/layout")
def set_workspace_layout(body: WorkspaceLayoutIn, p: Principal = Depends(require_admin)):
    raw = json.dumps(body.layout) if body.layout else ""
    if body.layout is not None and (body.layout.get("v") != 1 or len(raw) > 65536):
        raise HTTPException(400, "That isn't a workspace layout")
    with db.tx() as c:
        db.set_setting(c, "workspace.layout", raw)
        db.audit(c, p.name, "workspace.layout", "cleared" if not raw else "set")
    return {"ok": True}


# ------------------------------------------------------------------- admin --
@router.get("/api/admin/settings")
def get_settings(p: Principal = Depends(require_admin)):
    with db.ro() as c:
        lic = licence.current(c)
        return {
            "branding": get_branding(c),
            "modules": {**DEFAULT_MODULES, **db.get_setting(c, "modules", {})},
            "legacy_fleet_api": db.get_setting(c, "legacy_fleet_api", True),
            "message_retention_days": db.get_setting(c, "message_retention_days", 0),
            "help_escalate_minutes": db.get_setting(c, "help_escalate_minutes", 2),
            "licence": lic.public(),
        }


class SettingsIn(BaseModel):
    branding: dict | None = None
    appearance: dict | None = None
    modules: dict | None = None
    legacy_fleet_api: bool | None = None
    message_retention_days: int | None = None
    help_escalate_minutes: float | None = Field(default=None, ge=0, le=60)


@router.put("/api/admin/settings")
def put_settings(body: SettingsIn, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        if body.branding is not None:
            allowed = {k: str(v)[:300] for k, v in body.branding.items() if k in DEFAULT_BRANDING}
            db.set_setting(c, "branding", {**DEFAULT_BRANDING, **db.get_setting(c, "branding", {}), **allowed})
        if body.appearance is not None:
            bad = [k for k, v in body.appearance.items() if k not in APPEARANCE_CHOICES or v not in APPEARANCE_CHOICES[k]]
            if bad:
                raise HTTPException(400, f"Unknown appearance setting: {', '.join(bad)}")
            db.set_setting(c, "appearance", {**get_appearance(c), **body.appearance})
            db.audit(c, p.name, "appearance.edit", ", ".join(f"{k}={v}" for k, v in body.appearance.items()))
        if body.modules is not None:
            db.set_setting(c, "modules", {m: bool(body.modules.get(m, True)) for m in licence.ALL_MODULES})
        if body.legacy_fleet_api is not None:
            db.set_setting(c, "legacy_fleet_api", body.legacy_fleet_api)
        if body.message_retention_days is not None:
            db.set_setting(c, "message_retention_days", max(0, body.message_retention_days))
        if body.help_escalate_minutes is not None:
            db.set_setting(c, "help_escalate_minutes", body.help_escalate_minutes)
        db.audit(c, p.name, "settings.update", ",".join(k for k, v in body.model_dump().items() if v is not None))
    return {"ok": True}


def usage(c) -> dict:
    return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("sites", "rooms", "nodes")}


@router.get("/api/admin/licence")
def get_licence(p: Principal = Depends(require_admin)):
    with db.ro() as c:
        key = db.get_setting(c, "licence_key", "")
        lic = licence.current(c)
        now, is_locked, note = licence.clock(c), licence.locked(c), licence.notice(c, "admin")
        used = usage(c)
    d = licence.details(key)
    pl = d["payload"] if isinstance(d["payload"], dict) else {}
    expires = lic.expires or int(pl.get("expires", 0) or 0)
    return {
        **lic.public(),
        "expires": expires,
        "issued": lic.issued or int(pl.get("issued", 0) or 0),
        "licensee": lic.licensee if lic.valid or not pl else str(pl.get("licensee", "")),
        "days_left": max(0, -int((now - expires) // 86400)) if expires else None,  # rounded up, as the warnings say
        "locked": is_locked,
        "notice": note,
        "usage": used,
        "limits": {"sites": lic.max_sites, "nodes": lic.max_nodes, "rooms": 0},
        "installed": d["installed"],
        "signature_valid": d["signature_valid"],
        "vendor_key_id": d["vendor_key_id"],
        "vendor_key_source": d["vendor_key_source"],
        "stored_in": f"Settings table (licence_key) in {config.cfg.db_path}",
        "payload": pl,
        "raw": d["raw"],
    }


class LicenceIn(BaseModel):
    key: str
    remove: bool = False  # an empty key only removes the licence when that's what was asked


@router.put("/api/admin/licence")
def put_licence(body: LicenceIn, p: Principal = Depends(require_admin)):
    if not body.key.strip() and not body.remove:
        raise HTTPException(400, "Paste the licence key first")
    with db.tx() as c:
        lic = licence.check_new(c, body.key)
        if body.key.strip() and not lic.valid:
            raise HTTPException(400, lic.reason)
        db.set_setting(c, "licence_key", body.key.strip())
        if body.key.strip():
            licence.installed(c, lic, p.name)
        else:
            db.audit(c, p.name, "licence.remove", "")
            licence.forget()
    return lic.public()


# sites
class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    timezone: TimeZone = "Europe/London"


@router.get("/api/admin/sites")
def list_sites(p: Principal = Depends(require_manager)):
    with db.ro() as c:
        rows = [r for r in db.rows(c.execute("SELECT * FROM sites ORDER BY name")) if site_ok(p, r["id"])]
    # The enrolment code adds laptops, which is for admins only.
    if p.role != "admin":
        for r in rows:
            r.pop("enrol_code", None)
    return rows


@router.post("/api/admin/sites")
def add_site(body: SiteIn, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        lic = licence.current(c)
        count = c.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
        if lic.max_sites and count >= lic.max_sites:
            raise HTTPException(402, f"Your licence allows {lic.max_sites} site(s)")
        sid = create_site(c, body.name, body.timezone)
        db.audit(c, p.name, "site.add", body.name)
    return {"id": sid}


@router.put("/api/admin/sites/{site_id}")
def edit_site(site_id: int, body: SiteIn, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        c.execute("UPDATE sites SET name=?,timezone=? WHERE id=?", (body.name.strip(), body.timezone, site_id))
        db.audit(c, p.name, "site.edit", f"{body.name.strip()} ({body.timezone})")
    return {"ok": True}


@router.post("/api/admin/sites/{site_id}/enrol-code")
def new_enrol_code(site_id: int, p: Principal = Depends(require_admin)):
    code = enrol_code()
    with db.tx() as c:
        c.execute("UPDATE sites SET enrol_code=? WHERE id=?", (code, site_id))
        db.audit(c, p.name, "site.enrol_code", str(site_id))
    return {"enrol_code": code}


@router.delete("/api/admin/sites/{site_id}")
def delete_site(site_id: int, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        if c.execute("SELECT COUNT(*) FROM sites").fetchone()[0] <= 1:
            raise HTTPException(400, "You can't delete the last site")
        c.execute("DELETE FROM sites WHERE id=?", (site_id,))
        db.audit(c, p.name, "site.delete", str(site_id))
    return {"ok": True}


# rooms
class RoomIn(BaseModel):
    site_id: int
    name: str = Field(min_length=1, max_length=80)
    short_name: str = ""
    sort: int = 0
    enabled: bool = True


@router.post("/api/admin/rooms")
async def add_room(body: RoomIn, p: Principal = Depends(require_manager)):
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM sites WHERE id=?", (body.site_id,)).fetchone() or not site_ok(p, body.site_id):
            raise HTTPException(404, "Site not found")
        try:
            rid = create_room(c, body.site_id, body.name, body.short_name, body.sort)
        except sqlite3.IntegrityError:
            raise HTTPException(409, "A room with that name already exists")
        db.audit(c, p.name, "room.add", body.name)
    await hub.publish(f"site:{body.site_id}", "rooms.changed", {})
    return {"id": rid}


@router.put("/api/admin/rooms/{room_id}")
async def edit_room(room_id: int, body: RoomIn, p: Principal = Depends(require_manager)):
    with db.tx() as c:
        room_or_404(c, room_id, p)
        try:
            c.execute(
                "UPDATE rooms SET name=?,short_name=?,sort=?,enabled=? WHERE id=?",
                (body.name.strip(), (body.short_name or body.name)[:12], body.sort, int(body.enabled), room_id),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "A room with that name already exists")
        c.execute("UPDATE channels SET name=? WHERE room_id=? AND kind='room'", (body.name.strip(), room_id))
        db.audit(c, p.name, "room.edit", body.name.strip())
    await hub.publish(f"site:{body.site_id}", "rooms.changed", {})
    return {"ok": True}


@router.delete("/api/admin/rooms/{room_id}")
async def delete_room(room_id: int, p: Principal = Depends(require_manager)):
    with db.tx() as c:
        r = room_or_404(c, room_id, p)
        c.execute("DELETE FROM rooms WHERE id=?", (room_id,))
        db.audit(c, p.name, "room.delete", r["name"])
    await hub.publish(f"site:{r['site_id']}", "rooms.changed", {})
    return {"ok": True}


# accounts
# Managers look after techs and viewers; only an admin creates or changes admins and managers.
MANAGED_ROLES = ("tech", "viewer")


def _check_manages(p: Principal, *roles: str) -> None:
    if p.role != "admin" and any(r not in MANAGED_ROLES for r in roles):
        raise HTTPException(403, "Only an admin can add or change admins and managers")


class AccountIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    display_name: str = ""
    role: str = "tech"
    site_id: int | None = None
    password: str | None = None
    active: bool = True


@router.get("/api/admin/accounts")
def list_accounts(p: Principal = Depends(require_manager)):
    with db.ro() as c:
        rows = db.rows(c.execute("SELECT id,username,display_name,role,site_id,active,created_at FROM accounts ORDER BY username"))
    # A user kept to one site only sees that site's people (and themselves).
    return [r for r in rows if p.site_id is None or r["site_id"] == p.site_id or r["id"] == p.id]


@router.post("/api/admin/accounts")
def add_account(body: AccountIn, p: Principal = Depends(require_manager)):
    if body.role not in ROLES:
        raise HTTPException(400, "Unknown role")
    if not body.password or len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    _check_manages(p, body.role)
    if p.site_id is not None and body.site_id != p.site_id:
        raise HTTPException(403, "You can only add people to your own site")
    with db.tx() as c:
        try:
            cur = c.execute(
                "INSERT INTO accounts(username,password_hash,display_name,role,site_id,active,created_at) VALUES(?,?,?,?,?,?,?)",
                (body.username.strip(), hash_password(body.password), body.display_name.strip() or body.username.strip(),
                 body.role, body.site_id, int(body.active), db.now_iso()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "That username is taken")
        db.audit(c, p.name, "account.add", body.username)
    return {"id": cur.lastrowid}


@router.put("/api/admin/accounts/{account_id}")
def edit_account(account_id: int, body: AccountIn, p: Principal = Depends(require_manager)):
    if body.role not in ROLES:
        raise HTTPException(400, "Unknown role")
    with db.tx() as c:
        cur = c.execute("SELECT role,site_id FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not cur or (account_id != p.id and p.site_id is not None and cur["site_id"] != p.site_id):
            raise HTTPException(404, "Account not found")
        if p.site_id is not None and body.site_id != p.site_id:
            raise HTTPException(403, "You can only keep people on your own site")
        if account_id == p.id and (body.role != p.role or not body.active):
            raise HTTPException(400, "You can't change your own access")
        if account_id != p.id:
            _check_manages(p, cur["role"], body.role)
        try:
            c.execute(
                "UPDATE accounts SET username=?,display_name=?,role=?,site_id=?,active=? WHERE id=?",
                (body.username.strip(), body.display_name.strip() or body.username.strip(), body.role, body.site_id,
                 int(body.active), account_id),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "That username is taken")
        if body.password:
            if len(body.password) < 8:
                raise HTTPException(400, "Password must be at least 8 characters")
            c.execute("UPDATE accounts SET password_hash=? WHERE id=?", (hash_password(body.password), account_id))
            c.execute("DELETE FROM sessions WHERE account_id=?", (account_id,))
        if not body.active:
            c.execute("DELETE FROM sessions WHERE account_id=?", (account_id,))
        db.audit(c, p.name, "account.edit", body.username)
    return {"ok": True}


@router.delete("/api/admin/accounts/{account_id}")
def delete_account(account_id: int, p: Principal = Depends(require_manager)):
    if account_id == p.id:
        raise HTTPException(400, "You can't delete your own account")
    with db.tx() as c:
        cur = c.execute("SELECT role,site_id FROM accounts WHERE id=?", (account_id,)).fetchone()
        if cur and p.site_id is not None and cur["site_id"] != p.site_id:
            raise HTTPException(404, "Account not found")
        if cur:
            _check_manages(p, cur["role"])
        c.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        db.audit(c, p.name, "account.delete", str(account_id))
    return {"ok": True}


# API keys (Companion, automation)
class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.get("/api/admin/api-keys")
def list_keys(p: Principal = Depends(require_admin)):
    with db.ro() as c:
        return db.rows(c.execute("SELECT id,name,prefix,created_at FROM api_keys ORDER BY name"))


@router.post("/api/admin/api-keys")
def add_key(body: ApiKeyIn, p: Principal = Depends(require_admin)):
    key = new_token("ats_")
    with db.tx() as c:
        c.execute("INSERT INTO api_keys(name,key_hash,prefix,created_at) VALUES(?,?,?,?)",
                  (body.name.strip(), token_hash(key), key[:8], db.now_iso()))
        db.audit(c, p.name, "apikey.add", body.name)
    return {"key": key}


@router.delete("/api/admin/api-keys/{key_id}")
def delete_key(key_id: int, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        c.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
        db.audit(c, p.name, "apikey.delete", str(key_id))
    return {"ok": True}


@router.get("/api/admin/audit")
def audit_log(limit: int = 200, p: Principal = Depends(require_manager)):
    with db.ro() as c:
        return db.rows(c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (min(limit, 1000),)))


@router.get("/api/admin/backup")
def backup(p: Principal = Depends(require_admin)):
    """Consistent snapshot of the database, uploads and key, as a zip."""
    buf = io.BytesIO()
    snap = config.cfg.data / ".backup.db"
    src = db.connect()
    dst = sqlite3.connect(snap)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(snap, "atsuit.db")
        key = config.cfg.data / "secret.key"
        if key.exists():
            z.write(key, "secret.key")
        for f in config.cfg.uploads.rglob("*"):
            if f.is_file():
                z.write(f, f"uploads/{f.relative_to(config.cfg.uploads)}")
        z.writestr("VERSION", VERSION)
    snap.unlink(missing_ok=True)
    with db.tx() as c:
        db.audit(c, p.name, "backup.download")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": "attachment; filename=atsuit-backup.zip"})


@router.get("/api/admin/diagnostics")
def diagnostics(p: Principal = Depends(require_admin)):
    with db.ro() as c:
        counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("sites", "rooms", "accounts", "nodes", "messages", "links", "help_requests")}
        schema = c.execute("SELECT version FROM schema_version").fetchone()[0]
        lic = licence.current(c)
    from . import captions

    return {
        "version": VERSION,
        "schema": schema,
        "counts": counts,
        "websockets": hub.count(),
        "licence": json.loads(json.dumps(lic.public())),
        "captions": captions.engine_status(),
    }


# -------------------------------------------------------------- admin info --
def build_info() -> dict:
    """Set at image build time (Dockerfile BUILD_* args); a dev checkout reads git."""
    commit = os.getenv("ATSUIT_COMMIT", "")
    if not commit:
        try:
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).parent,
                                    capture_output=True, text=True, timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            commit = ""
    return {"number": os.getenv("ATSUIT_BUILD", "") or "dev", "commit": commit or "dev",
            "date": os.getenv("ATSUIT_BUILD_DATE", "")}


def _ips() -> list[str]:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # the address used for outbound traffic; nothing is sent
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith(("127.", "::1")))


def _dir_size(path: Path) -> int | None:
    if not path.is_dir():
        return None
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _version(pkg: str) -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(pkg)
    except PackageNotFoundError:
        return ""


def server_info() -> dict:
    """Everything about this install for Settings → About and support tickets.
    Never includes tokens, keys, password hashes or the licence key."""
    from . import captions, fleet

    with db.ro() as c:
        now = time.time()
        nodes = c.execute("SELECT kind, legacy, mode, last_seen FROM nodes").fetchall()
        node_counts: dict = {}
        for n in nodes:
            kind = "screen" if n["kind"] == "kiosk" and not n["legacy"] else n["kind"]
            b = node_counts.setdefault(kind, {"total": 0, "online": 0})
            b["total"] += 1
            b["online"] += int(bool(n["last_seen"] and now - n["last_seen"] < fleet.ONLINE_SECONDS))
        modes = {m: sum(1 for n in nodes if n["kind"] == "tech" and n["mode"] == m) for m in ("main", "backup")}
        counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("sites", "rooms", "accounts", "nodes", "messages", "links", "help_requests", "api_keys")}
        roles = dict(c.execute("SELECT role, COUNT(*) FROM accounts GROUP BY role").fetchall())
        open_help = c.execute("SELECT COUNT(*) FROM help_requests WHERE status!='resolved'").fetchone()[0]
        last_backup = c.execute("SELECT at, actor FROM audit_log WHERE action='backup.download' ORDER BY id DESC LIMIT 1").fetchone()
        schema = c.execute("SELECT version FROM schema_version").fetchone()[0]
        sites = db.rows(c.execute("SELECT name, timezone FROM sites ORDER BY name"))
        lic = licence.current(c)
        mods = modules_enabled(c)
        branding = get_branding(c)
        legacy = bool(db.get_setting(c, "legacy_fleet_api", True))
        retention = db.get_setting(c, "message_retention_days", 0)
    data = config.cfg.data
    dbf = config.cfg.db_path
    db_size = sum(f.stat().st_size for f in (dbf, Path(f"{dbf}-wal")) if f.exists())
    try:
        disk = shutil.disk_usage(data)
        disk_out = {"total": disk.total, "free": disk.free}
    except OSError:
        disk_out = {}
    presenter_dir = Path(os.getenv("ATSUIT_PRESENTER_FILES") or data / "presenter-files")
    client = fleet.client_dir() / "release.json"
    try:
        sys_uptime = int(float(Path("/proc/uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        sys_uptime = None
    local = datetime.now().astimezone()
    return {
        "product": branding["product_name"] or "AT-SUIT",
        "organisation": branding["organisation"],
        "version": VERSION,
        "build": build_info(),
        "schema": schema,
        "server": {
            "role": "This server (AT-SUIT runs as one server; there are no others to manage)",
            "count": 1,
            "hostname": socket.gethostname(),
            "ips": _ips(),
            "public_url": config.cfg.public_url,
            "port": config.cfg.port,
            "os": platform.platform(),
            "python": sys.version.split()[0],
            "fastapi": _version("fastapi"),
            "uvicorn": _version("uvicorn"),
            "sqlite": sqlite3.sqlite_version,
            "cpus": os.cpu_count(),
            "in_docker": Path("/.dockerenv").exists(),
            "started": datetime.fromtimestamp(STARTED, timezone.utc).isoformat(),
            "uptime_seconds": int(time.time() - STARTED),
            "system_uptime_seconds": sys_uptime,
            "time": local.isoformat(),
            "timezone": os.getenv("TZ") or local.tzname(),
            "site_timezones": {s["name"]: s["timezone"] for s in sites},
        },
        "storage": {
            "data_dir": str(data),
            "db_bytes": db_size,
            "uploads_bytes": _dir_size(config.cfg.uploads),
            "transcripts_bytes": _dir_size(config.cfg.transcripts),
            "presenter_dir": str(presenter_dir),
            "presenter_bytes": _dir_size(presenter_dir),
            "disk": disk_out,
        },
        "apps": {
            "windows_app": fleet.app_release().get("version"),
            "node_agent": fleet.agent_release().get("version"),
            "screen_agent": fleet.screen_release().get("version"),
            "kiosk_agent": json.loads(client.read_text()).get("version") if client.is_file() else None,
        },
        "nodes": {"total": len(nodes), "online": sum(b["online"] for b in node_counts.values()),
                  "by_kind": node_counts, "tech_main": modes["main"], "tech_backup": modes["backup"]},
        "counts": {**counts, "open_help_requests": open_help, "accounts_by_role": roles},
        "modules": mods,
        "licence": {"licensee": lic.licensee, "edition": lic.edition, "valid": lic.valid, "reason": lic.reason,
                    "expires": lic.expires, "max_nodes": lic.max_nodes, "max_sites": lic.max_sites, "serial": lic.serial},
        "captions": captions.engine_status(),
        "websockets": hub.count(),
        "settings": {"legacy_fleet_api": legacy, "message_retention_days": retention, "asr_enabled": config.cfg.asr_enabled,
                     "asr_max_rooms": config.cfg.asr_max_rooms, "session_hours": config.cfg.session_hours},
        "last_backup": dict(last_backup) if last_backup else None,
    }


@router.get("/api/admin/info")
def admin_info(download: bool = False, p: Principal = Depends(require_admin)):
    out = server_info()
    if not download:
        return out
    out["generated"] = datetime.now(timezone.utc).isoformat()
    name = f"atsuit-diagnostics-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    return JSONResponse(out, headers={"Content-Disposition": f"attachment; filename={name}"})


@router.get("/api/admin/downloads/{name}")
def admin_download(name: str, p: Principal = Depends(require_admin)):
    """The agents an admin copies onto machines by hand."""
    from . import fleet

    if name == "atsuit_node.py":
        f = fleet.agent_file()
    elif name in ("atsuit_screen.py", "install.sh"):
        f = fleet.screen_file(name)
    elif name == "kiosk-agent":
        rel = fleet.client_dir() / "release.json"
        fname = json.loads(rel.read_text()).get("filename") if rel.is_file() else None
        f = fleet.client_dir() / fname if fname and "/" not in fname else None
        name = fname or name
    else:
        raise HTTPException(404)
    if not f or not f.is_file():
        raise HTTPException(404, "Not on this server")
    return FileResponse(f, media_type="application/octet-stream", filename=name)
