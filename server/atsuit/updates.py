"""Updates from GitHub releases.

The server checks the newest release on GitHub every few hours. What happens then is set in
Settings → Updates (where they come from is fixed here, not a setting):
  auto    install it by itself, but never while a show is live (a timer running or paused);
  notify  only say a new version is out; an admin presses Install now;
  off     don't check.

The server can't replace itself from inside its container, so the install is done on the host
by update.sh (backup, new version, health check, roll back if it isn't healthy). install.sh
sets up a systemd timer that runs `update.sh --auto` every few minutes; that asks this module
what to do:

  python -m atsuit.updates plan                 ACTION=install|none, and what to install
  python -m atsuit.updates report ok|failed V   how it went

After the server is on a new version it also fetches the Windows app from the same release
(the .exe, .blockmap and latest.yml), so tech laptops keep updating from the server and need
no internet of their own.
"""
from __future__ import annotations

import asyncio
import re
import sys
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import VERSION, config, db
from .security import Principal, decrypt, encrypt, require_admin

# Releases come from a public repository that holds only the releases (no source code), so
# servers need no token. Until it has a release, the source repository is asked instead.
REPO_DEFAULT = "adam1991tom/AT-SUITE-releases"
REPO_SOURCE = "adam1991tom/AT-SUITE"
# The repositories were called AT-SUIT before the product became AT SUITE. GitHub redirects the old
# names, but each new name is asked first and its old one only when the new one isn't there (yet).
RENAMED = {REPO_DEFAULT: "adam1991tom/AT-SUIT-releases", REPO_SOURCE: "adam1991tom/AT-SUIT"}
MODES = ("auto", "notify", "off")
CHECK_EVERY_S = 30 * 60  # a new release shows within half an hour; GitHub allows 60 calls an hour without a token
FLAG = "update-now"  # in the data folder: the host's atsuit-update.path starts update.sh as soon as it appears
HOST_SEEN_S = 30 * 60  # the host updater runs every few minutes; older than this, it isn't set up
TAG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_]{1,255}$")
API = "https://api.github.com"


def vtuple(v: str | None) -> tuple:
    """'v1.2.10' → (1, 2, 10); anything that isn't a version sorts first."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", str(v or ""))
    return tuple(int(x) for x in m.groups()) if m else (0,)


def newer(a: str | None, b: str | None) -> bool:
    return vtuple(a) > vtuple(b)


# --------------------------------------------------------------- settings --
def conf(c) -> dict:
    s = db.get_setting(c, "updates", {}) or {}
    mode = s.get("mode") if s.get("mode") in MODES else "auto"
    # Always AT SUITE's own releases: a repository saved by an older version is ignored.
    repo = REPO_DEFAULT
    token = ""
    if s.get("token_enc"):
        try:
            token = decrypt(s["token_enc"])
        except Exception:  # a key change: ask for the token again
            token = ""
    return {"mode": mode, "repo": repo, "token": token}


def save_conf(c, mode: str | None = None, token: str | None = None) -> None:
    s = db.get_setting(c, "updates", {}) or {}
    if mode is not None:
        if mode not in MODES:
            raise ValueError("Pick auto, notify or off")
        s["mode"] = mode
    s.pop("repo", None)  # older versions let admins set it
    if token is not None:
        token = token.strip()
        if token and not TOKEN_RE.match(token):
            raise ValueError("That doesn't look like a GitHub token")
        s["token_enc"] = encrypt(token) if token else ""
    db.set_setting(c, "updates", s)


def state(c) -> dict:
    return db.get_setting(c, "updates_state", {}) or {}


def save_state(c, **changes) -> dict:
    st = {**state(c), **changes}
    db.set_setting(c, "updates_state", st)
    return st


def live_rooms(c) -> list[str]:
    """Rooms with a show on: a timer running, or started and paused."""
    rows = c.execute("SELECT r.name FROM timers t JOIN rooms r ON r.id=t.room_id "
                     "WHERE t.running=1 OR t.first_started_at IS NOT NULL ORDER BY r.name").fetchall()
    return [r["name"] for r in rows]


def status(c) -> dict:
    """Everything Settings → Updates shows."""
    from .modules.fleet import app_release

    cf, st = conf(c), state(c)
    latest = st.get("latest") or {}
    seen = st.get("host_seen_at") or 0
    return {
        "current": VERSION, "mode": cf["mode"], "has_token": bool(cf["token"]),
        "latest": latest or None, "available": bool(latest) and newer(latest.get("version"), VERSION),
        "ahead": bool(latest) and newer(VERSION, latest.get("version")),
        "checked_at": st.get("checked_at"), "error": st.get("error") or "",
        "requested": st.get("requested") or None, "failed": st.get("failed") or [],
        "last_result": st.get("last_result") or None,
        "host_updater": bool(seen) and time.time() - seen < HOST_SEEN_S, "host_seen_at": seen or None,
        "live": live_rooms(c), "app": app_release().get("version"), "app_error": st.get("app_error") or "",
        "helper": config.cfg.role != "main",
    }


# ----------------------------------------------------------------- GitHub --
def _headers(token: str, accept: str = "application/vnd.github+json") -> dict:
    h = {"Accept": accept, "User-Agent": f"AT-SUITE/{VERSION}", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


class NoRelease(RuntimeError):
    pass


def fetch_latest(repo: str, token: str) -> dict:
    """The newest release of repo, or of its old name while only that one exists."""
    old = next((o for n, o in RENAMED.items() if n.lower() == repo.lower()), None)
    try:
        return _fetch_latest(repo, token)
    except NoRelease:
        if not old:
            raise
        return _fetch_latest(old, token)


def _fetch_latest(repo: str, token: str) -> dict:
    # follow_redirects: a renamed repository answers from its old name with a redirect.
    r = httpx.get(f"{API}/repos/{repo}/releases/latest", headers=_headers(token), timeout=20, follow_redirects=True)
    if r.status_code == 404:
        raise NoRelease("No release found. If the repository is private, add a GitHub token.")
    if r.status_code in (401, 403):
        raise RuntimeError(f"GitHub refused ({r.status_code}). Check the token, or try again later.")
    r.raise_for_status()
    j = r.json()
    tag = str(j.get("tag_name") or "")
    return {
        "repo": repo, "version": tag.lstrip("v"), "tag": tag, "name": j.get("name") or tag, "url": j.get("html_url") or "",
        "published_at": j.get("published_at"), "notes": (j.get("body") or "")[:4000],
        "assets": [{"name": a["name"], "url": a["url"], "size": a.get("size", 0)} for a in j.get("assets") or []],
    }


def poke(version: str) -> None:
    """Start the host updater now instead of at its next 10-minute run."""
    try:
        (config.cfg.data / FLAG).write_text(version + "\n")
    except OSError as exc:
        print("updates: can't start the host updater now:", exc)


def check(manual: bool = False) -> dict:
    """Ask GitHub for the newest release and remember it; then make sure the laptops' app matches.
    manual: an admin pressed Check now, which checks even when updates are off."""
    with db.ro() as c:
        cf = conf(c)
    if config.cfg.role != "main" or (cf["mode"] == "off" and not manual):
        with db.ro() as c:
            return status(c)
    try:
        try:
            latest = fetch_latest(cf["repo"], cf["token"])
        except RuntimeError:
            if cf["repo"] != REPO_DEFAULT:
                raise
            latest = fetch_latest(REPO_SOURCE, cf["token"])
        with db.tx() as c:
            save_state(c, latest=latest, checked_at=db.now_iso(), error="")
            st = status(c)
        # Install by itself: don't wait for the host's next run either (it still won't install mid-show).
        if cf["mode"] == "auto" and st["available"] and not st["live"] and latest["version"] not in st["failed"]:
            poke(latest["version"])
    except Exception as exc:
        with db.tx() as c:
            save_state(c, checked_at=db.now_iso(), error=str(exc)[:300] or exc.__class__.__name__)
    mirror_app()
    with db.ro() as c:
        return status(c)


def mirror_app() -> None:
    """Once this server runs the newest release, put that release's Windows app where laptops
    update from (the same files an admin would upload in Laptops & screens)."""
    from .modules.fleet import app_dir, app_release, publish_app_files

    with db.ro() as c:
        cf, latest = conf(c), state(c).get("latest") or {}
    if cf["mode"] == "off" or not latest or latest.get("version") != VERSION:
        return
    if app_release().get("version") == VERSION:
        return
    assets = {a["name"]: a for a in latest.get("assets") or []}
    if "latest.yml" not in assets:
        return
    try:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            def get(name: str) -> bytes:
                r = client.get(assets[name]["url"], headers=_headers(cf["token"], "application/octet-stream"))
                r.raise_for_status()
                return r.content
            yml = get("latest.yml")
            m = re.search(rb"^path:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", yml, re.M)
            exe = m.group(1).decode() if m else ""
            if exe not in assets:
                raise RuntimeError(f"The release has latest.yml but not {exe or 'the installer it names'}")
            files = {exe: None, f"{exe}.blockmap": None}
            for name in list(files):
                if name in assets:
                    tmp = app_dir() / f".{name}.part"
                    with client.stream("GET", assets[name]["url"], headers=_headers(cf["token"], "application/octet-stream")) as r:
                        r.raise_for_status()
                        with tmp.open("wb") as out:
                            for chunk in r.iter_bytes(1024 * 1024):
                                out.write(chunk)
                    files[name] = tmp
            ytmp = app_dir() / ".latest.yml.part"
            ytmp.write_bytes(yml)
            files["latest.yml"] = ytmp  # last, so laptops never see a latest.yml before its installer
        publish_app_files({n: p for n, p in files.items() if p}, "AT-SUITE updates")
        with db.tx() as c:
            save_state(c, app_error="")
    except Exception as exc:
        with db.tx() as c:
            save_state(c, app_error=f"Couldn't fetch the Windows app: {str(exc)[:200]}")


async def loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await asyncio.to_thread(check)
        except Exception as exc:  # never let the checker kill the server
            print("updates:", exc)
        await asyncio.sleep(CHECK_EVERY_S)


# -------------------------------------------------------------------- API --
router = APIRouter()


class UpdatesIn(BaseModel):
    mode: str | None = None
    token: str | None = None  # "" removes it


@router.get("/api/admin/updates")
def get_updates(p: Principal = Depends(require_admin)):
    with db.ro() as c:
        return status(c)


@router.put("/api/admin/updates")
def put_updates(body: UpdatesIn, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        try:
            save_conf(c, body.mode, body.token)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        changed = (["mode"] if body.mode is not None else []) + (["token"] if body.token is not None else [])
        db.audit(c, p.name, "updates.settings", ", ".join(changed))
        return status(c)


@router.post("/api/admin/updates/check")
async def check_updates(p: Principal = Depends(require_admin)):
    return await asyncio.to_thread(check, True)


@router.post("/api/admin/updates/install")
def install_now(p: Principal = Depends(require_admin)):
    """Install the newest release now, whatever the mode, but still not while a show is on.
    It asks GitHub first, so a release published a minute ago counts."""
    check(True)
    with db.tx() as c:
        st = status(c)
        if not st["available"]:
            raise HTTPException(400, f"There is no newer version to install. This server is on {VERSION}"
                                + (f" and the newest release is {st['latest']['version']}." if st["latest"] else "."))
        save_state(c, requested=st["latest"]["version"])
        db.audit(c, p.name, "updates.install_now", st["latest"]["version"])
        out = status(c)
    poke(out["requested"])
    return out


@router.delete("/api/admin/updates/install")
def cancel_install(p: Principal = Depends(require_admin)):
    with db.tx() as c:
        save_state(c, requested=None)
        return status(c)


# ------------------------------------------------------- the host updater --
def plan() -> dict:
    """What update.sh --auto should do now."""
    with db.tx() as c:
        st = save_state(c, host_seen_at=time.time())
        cf = conf(c)
        latest = st.get("latest") or {}
        version, requested = latest.get("version"), st.get("requested")
        asked = bool(requested) and requested == version
        none = lambda why: {"ACTION": "none", "REASON": why}  # noqa: E731
        if config.cfg.role != "main":
            return none("a helper server updates with its main server")
        if not latest or not newer(version, VERSION):
            return none(f"up to date ({VERSION})")
        if cf["mode"] == "off" and not asked:
            return none("updates are off")
        if cf["mode"] == "notify" and not asked:
            return none(f"{version} is out; waiting for an admin to press Install now")
        if version in (st.get("failed") or []) and not asked:
            return none(f"{version} failed to install before; an admin can try again with Install now")
        live = live_rooms(c)
        if live:
            return none(f"a show is on in {', '.join(live)}")
        tag = latest.get("tag") or ""
        if not TAG_RE.match(tag):
            return none("the release has no usable tag")
        save_state(c, installing={"version": version, "at": db.now_iso()})
        db.audit(c, "updates", "updates.install", f"{VERSION} → {version}")
        return {"ACTION": "install", "VERSION": version, "TAG": tag, "REPO": latest.get("repo") or cf["repo"], "TOKEN": cf["token"]}


def report(result: str, version: str, detail: str = "") -> None:
    with db.tx() as c:
        st = state(c)
        failed = [v for v in st.get("failed") or [] if v != version]
        if result != "ok":
            failed.append(version)
        save_state(c, failed=failed[-10:], requested=None, installing=None,
                   last_result={"result": result, "version": version, "detail": detail[:300], "at": db.now_iso(), "from": VERSION})
        db.audit(c, "updates", f"updates.{'installed' if result == 'ok' else 'failed'}", f"{version} {detail[:200]}".strip())


def main(argv: list[str]) -> int:
    db.migrate()
    if argv[:1] == ["plan"]:
        for k, v in plan().items():
            print(f"{k}={v}")
        return 0
    if argv[:1] == ["report"] and len(argv) >= 3 and argv[1] in ("ok", "failed"):
        report(argv[1], argv[2], " ".join(argv[3:]))
        return 0
    if argv[:1] == ["check"]:
        st = check()
        print(f"current={st['current']} latest={(st['latest'] or {}).get('version')} error={st['error']}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
