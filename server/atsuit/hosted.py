"""Hosted mode: AT-SUITE run for a venue by its provider on a shared cloud server (see cloud/).

Each venue has its own container, data and licence; the provider's `atcloud` tool looks after them.
With ATSUIT_HOSTED=1:
  - setting up a new server needs the one-time code from the setup link the provider sends
    (ATSUIT_SETUP_CODE; that also works on a venue's own server), so a stranger can't claim it;
  - the provider updates every venue, so Settings → Updates says so, nothing asks GitHub,
    and nothing installs by itself;
  - the server makes no captions itself (ATSUIT_ASR=0): the laptops do, with the speech model
    they fetch from it (ATSUIT_LAPTOP_MODELS=1), and their captions stay private by default.

For atcloud, run inside a venue's container:
  python -m atsuit.hosted info               one line of JSON: version, set up, licence, rooms, laptops
  python -m atsuit.hosted snapshot           a consistent copy of the database for the nightly backup
  python -m atsuit.hosted licence FILE       install the licence key in FILE (before or after setup)
  python -m atsuit.hosted app DIR            publish the Windows tech app in DIR for laptops to update from
"""
from __future__ import annotations

import hmac
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from . import VERSION, config, db, licence
from .security import Principal, require_admin

SNAPSHOT = ".snapshot"
MANAGED = "Updates are looked after by your AT-SUITE provider. Every venue is kept on the newest version, never while a show is on."


def setup_allowed(code: str | None) -> bool:
    """A new server can be set up by anyone on a venue's own network, unless a setup code is set.
    A hosted one always needs the code: it is on the internet."""
    want = config.cfg.setup_code
    if not want:
        return not config.cfg.hosted
    return bool(code) and hmac.compare_digest(code.strip().encode(), want.encode())


class SetupGate:
    """Refuses the setup wizard's POST without the right code (header X-Setup-Code)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "POST" and scope["path"].rstrip("/") == "/api/setup":
            code = dict(scope.get("headers") or []).get(b"x-setup-code", b"").decode("latin-1")
            if not setup_allowed(code):
                body = json.dumps({"detail": "This AT-SUITE needs the setup link from your provider. "
                                             "Open the link exactly as you were sent it."}).encode()
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


# -------------------------------------------------------------------- API --
public = APIRouter()


@public.get("/api/public/hosting", include_in_schema=False)
def hosting():
    """For the setup page: whether it needs the code from the setup link."""
    with db.ro() as c:
        licensed = licence.current(c).valid
    return {"hosted": config.cfg.hosted, "setup_code_needed": bool(config.cfg.setup_code) or config.cfg.hosted,
            "licensed": licensed}


# Registered ahead of the updates module's own routes, only in hosted mode.
updates_router = APIRouter()


def _updates_status() -> dict:
    from . import updates
    from .modules.fleet import app_release

    with db.ro() as c:
        return {"current": VERSION, "hosted": True, "detail": MANAGED, "mode": "managed", "latest": None,
                "available": False, "ahead": False, "host_updater": True, "live": updates.live_rooms(c),
                "app": app_release().get("version"), "failed": [], "requested": None, "error": "", "app_error": ""}


@updates_router.get("/api/admin/updates")
def get_updates(p: Principal = Depends(require_admin)):
    return _updates_status()


@updates_router.post("/api/admin/updates/check")
def check_updates(p: Principal = Depends(require_admin)):
    return _updates_status()


@updates_router.put("/api/admin/updates")
@updates_router.post("/api/admin/updates/install")
@updates_router.delete("/api/admin/updates/install")
def managed(p: Principal = Depends(require_admin)):
    raise HTTPException(400, MANAGED)


# -------------------------------------------------------------- atcloud --
def info() -> dict:
    from . import updates

    with db.ro() as c:
        from .modules.core import is_setup

        lic = licence.current(c)
        one = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
        return {"version": VERSION, "setup_complete": is_setup(c),
                "licence": {"valid": lic.valid, "state": lic.state, "licensee": lic.licensee, "edition": lic.edition,
                            "laptops": lic.max_nodes, "expires": lic.expires, "locked": licence.locked(c)},
                "rooms": one("SELECT COUNT(*) FROM rooms"), "laptops": one("SELECT COUNT(*) FROM nodes"),
                "accounts": one("SELECT COUNT(*) FROM accounts"),
                "last_sign_in": one("SELECT MAX(created_at) FROM sessions"), "live": updates.live_rooms(c)}


def snapshot() -> Path:
    """A consistent copy of the live database, safe to back up while the server runs.
    Caption transcripts and speech models are left out of backups (cloud/atcloud excludes them)."""
    out = config.cfg.data / SNAPSHOT
    out.mkdir(exist_ok=True)
    tmp, dest = out / "atsuit.db.part", out / "atsuit.db"
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(config.cfg.db_path, timeout=30)
    try:
        dst = sqlite3.connect(tmp)
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
    tmp.replace(dest)
    return dest


# Settings a hosted venue starts with. A venue admin can still change them.
DEFAULTS = {
    "captions_private": True,  # captions stay on the laptops and never reach the cloud server
}


def apply_defaults(c: sqlite3.Connection) -> None:
    """Give a hosted venue its starting settings, leaving any the venue has already set."""
    for key, value in DEFAULTS.items():
        if c.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone() is None:
            db.set_setting(c, key, value)


def install_licence(key: str) -> dict:
    with db.tx() as c:
        lic = licence.check_new(c, key)
        if not lic.valid:
            raise ValueError(lic.reason)
        db.set_setting(c, "licence_key", key.strip())
        licence.installed(c, lic, "atcloud")
    return lic.public()


def publish_app(src: Path) -> dict:
    """The Windows tech app from a release folder (latest.yml, the installer and its blockmap)."""
    from .modules.fleet import app_dir, publish_app_files

    yml = src / "latest.yml"
    if not yml.is_file():
        raise ValueError(f"No latest.yml in {src}")
    parts = {}
    for f in src.iterdir():
        if f.is_file() and (f.name == "latest.yml" or f.suffix in (".exe", ".blockmap")):
            tmp = app_dir() / f".{f.name}.part"
            shutil.copyfile(f, tmp)
            parts[f.name] = tmp
    return publish_app_files(parts, "atcloud")


def main(argv: list[str]) -> int:
    db.migrate()
    cmd, args = (argv[0] if argv else ""), argv[1:]
    try:
        if cmd == "info":
            print(json.dumps(info()))
        elif cmd == "snapshot":
            print(snapshot())
        elif cmd == "licence" and args:
            print(json.dumps(install_licence(Path(args[0]).read_text().strip())))
        elif cmd == "app" and args:
            print(json.dumps(publish_app(Path(args[0]))))
        else:
            print(__doc__)
            return 2
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
