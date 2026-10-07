"""Setup wizard, sign-in, sites, rooms, accounts, settings, licence, API keys."""
from __future__ import annotations

import io
import json
import re
import secrets
import sqlite3
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import VERSION, config, db, licence
from ..hub import hub
from ..security import (
    ROLES,
    SESSION_COOKIE,
    Principal,
    create_session,
    hash_password,
    new_token,
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
    "accent": "#4f7cff",
    "logo_url": "",
    "support_contact": "",
}
DEFAULT_MODULES = {m: True for m in licence.ALL_MODULES}


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
class SetupIn(BaseModel):
    organisation: str = Field(min_length=1, max_length=120)
    site_name: str = Field(min_length=1, max_length=120)
    timezone: str = "Europe/London"
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
        if body.licence_key.strip():
            db.set_setting(c, "licence_key", body.licence_key.strip())
        db.set_setting(c, "setup_complete", True)
        db.audit(c, body.admin_username, "setup.complete", body.site_name)
        token = create_session(c, cur.lastrowid)
    _set_cookie(response, token)
    return {"ok": True, "token": token}


# -------------------------------------------------------------------- auth --
class LoginIn(BaseModel):
    username: str
    password: str


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        max_age=config.cfg.session_hours * 3600)


@router.post("/api/auth/login")
def login(body: LoginIn, response: Response):
    with db.tx() as c:
        a = c.execute("SELECT * FROM accounts WHERE username=? AND active=1", (body.username.strip(),)).fetchone()
        if not a or not verify_password(body.password, a["password_hash"]):
            raise HTTPException(401, "Wrong username or password")
        token = create_session(c, a["id"])
        db.audit(c, a["username"], "auth.login")
    _set_cookie(response, token)
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
        return {**DEFAULT_BRANDING, **db.get_setting(c, "branding", {})}


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
            "branding": {**DEFAULT_BRANDING, **db.get_setting(c, "branding", {})},
            "modules": modules_enabled(c),
            "licence": {"licensee": lic.licensee, "edition": lic.edition, "valid": lic.valid, "reason": lic.reason},
            "sites": sites,
            "rooms": rooms,
        }


# ------------------------------------------------------------------- admin --
@router.get("/api/admin/settings")
def get_settings(p: Principal = Depends(require_admin)):
    with db.ro() as c:
        lic = licence.current(c)
        return {
            "branding": {**DEFAULT_BRANDING, **db.get_setting(c, "branding", {})},
            "modules": {**DEFAULT_MODULES, **db.get_setting(c, "modules", {})},
            "legacy_fleet_api": db.get_setting(c, "legacy_fleet_api", True),
            "message_retention_days": db.get_setting(c, "message_retention_days", 0),
            "licence": lic.public(),
        }


class SettingsIn(BaseModel):
    branding: dict | None = None
    modules: dict | None = None
    legacy_fleet_api: bool | None = None
    message_retention_days: int | None = None


@router.put("/api/admin/settings")
def put_settings(body: SettingsIn, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        if body.branding is not None:
            allowed = {k: str(v)[:300] for k, v in body.branding.items() if k in DEFAULT_BRANDING}
            db.set_setting(c, "branding", {**DEFAULT_BRANDING, **db.get_setting(c, "branding", {}), **allowed})
        if body.modules is not None:
            db.set_setting(c, "modules", {m: bool(body.modules.get(m, True)) for m in licence.ALL_MODULES})
        if body.legacy_fleet_api is not None:
            db.set_setting(c, "legacy_fleet_api", body.legacy_fleet_api)
        if body.message_retention_days is not None:
            db.set_setting(c, "message_retention_days", max(0, body.message_retention_days))
        db.audit(c, p.name, "settings.update", ",".join(k for k, v in body.model_dump().items() if v is not None))
    return {"ok": True}


class LicenceIn(BaseModel):
    key: str


@router.put("/api/admin/licence")
def put_licence(body: LicenceIn, p: Principal = Depends(require_admin)):
    lic = licence.parse(body.key)
    if body.key.strip() and not lic.valid:
        raise HTTPException(400, lic.reason)
    with db.tx() as c:
        db.set_setting(c, "licence_key", body.key.strip())
        db.audit(c, p.name, "licence.update", lic.licensee)
    return lic.public()


# sites
class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    timezone: str = "Europe/London"


@router.get("/api/admin/sites")
def list_sites(p: Principal = Depends(require_admin)):
    with db.ro() as c:
        return db.rows(c.execute("SELECT * FROM sites ORDER BY name"))


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
async def add_room(body: RoomIn, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM sites WHERE id=?", (body.site_id,)).fetchone():
            raise HTTPException(404, "Site not found")
        try:
            rid = create_room(c, body.site_id, body.name, body.short_name, body.sort)
        except sqlite3.IntegrityError:
            raise HTTPException(409, "A room with that name already exists")
        db.audit(c, p.name, "room.add", body.name)
    await hub.publish(f"site:{body.site_id}", "rooms.changed", {})
    return {"id": rid}


@router.put("/api/admin/rooms/{room_id}")
async def edit_room(room_id: int, body: RoomIn, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        room_or_404(c, room_id)
        c.execute(
            "UPDATE rooms SET name=?,short_name=?,sort=?,enabled=? WHERE id=?",
            (body.name.strip(), (body.short_name or body.name)[:12], body.sort, int(body.enabled), room_id),
        )
        c.execute("UPDATE channels SET name=? WHERE room_id=? AND kind='room'", (body.name.strip(), room_id))
    await hub.publish(f"site:{body.site_id}", "rooms.changed", {})
    return {"ok": True}


@router.delete("/api/admin/rooms/{room_id}")
async def delete_room(room_id: int, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        r = room_or_404(c, room_id)
        c.execute("DELETE FROM rooms WHERE id=?", (room_id,))
        db.audit(c, p.name, "room.delete", r["name"])
    await hub.publish(f"site:{r['site_id']}", "rooms.changed", {})
    return {"ok": True}


# accounts
class AccountIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    display_name: str = ""
    role: str = "tech"
    site_id: int | None = None
    password: str | None = None
    active: bool = True


@router.get("/api/admin/accounts")
def list_accounts(p: Principal = Depends(require_admin)):
    with db.ro() as c:
        return db.rows(c.execute("SELECT id,username,display_name,role,site_id,active,created_at FROM accounts ORDER BY username"))


@router.post("/api/admin/accounts")
def add_account(body: AccountIn, p: Principal = Depends(require_admin)):
    if body.role not in ROLES:
        raise HTTPException(400, "Unknown role")
    if not body.password or len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
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
def edit_account(account_id: int, body: AccountIn, p: Principal = Depends(require_admin)):
    if body.role not in ROLES:
        raise HTTPException(400, "Unknown role")
    with db.tx() as c:
        if account_id == p.id and (body.role != "admin" or not body.active):
            raise HTTPException(400, "You can't remove your own admin access")
        c.execute(
            "UPDATE accounts SET username=?,display_name=?,role=?,site_id=?,active=? WHERE id=?",
            (body.username.strip(), body.display_name.strip() or body.username.strip(), body.role, body.site_id,
             int(body.active), account_id),
        )
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
def delete_account(account_id: int, p: Principal = Depends(require_admin)):
    if account_id == p.id:
        raise HTTPException(400, "You can't delete your own account")
    with db.tx() as c:
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
def audit_log(limit: int = 200, p: Principal = Depends(require_admin)):
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
