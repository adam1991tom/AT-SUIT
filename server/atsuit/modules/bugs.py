"""Report a bug: anyone signed in (the console, a tech laptop, the laptop app) writes what went
wrong, with a screenshot if they like. It lands in the console under Bug reports, where admins
read it and tick it off. Nothing leaves the server unless an admin adds a GitHub repository and
token: then a report can open a GitHub issue (by itself on arrival, or when an admin presses the
button). The issue gets what went wrong and the version and system, never names, the
organisation, addresses or the screenshot."""
from __future__ import annotations

import asyncio
import json
import platform
import re
import time
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile as StarletteUpload

from .. import VERSION, config, db
from ..hub import hub
from ..security import Principal, decrypt, encrypt, require, require_admin

router = APIRouter()

require_reporter = require("viewer", ("account", "node"))

MAX_SHOT = 8 * 1024 * 1024
# What a screenshot may be, told by its first bytes (not by what the browser says it is).
IMAGE_TYPES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"))
PER_HOUR = 10  # reports one person or laptop can send in an hour
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_]{1,255}$")
API = "https://api.github.com"
STATUSES = ("open", "resolved")

_recent: dict[str, list[float]] = {}
_sending: set[asyncio.Task] = set()  # issues being opened in the background


def image_type(data: bytes) -> str | None:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return next((mime for magic, mime in IMAGE_TYPES if data.startswith(magic)), None)


# --------------------------------------------------------------- settings --
def conf(c) -> dict:
    s = db.get_setting(c, "bug_reports", {}) or {}
    repo = s.get("github_repo") if REPO_RE.match(str(s.get("github_repo") or "")) else ""
    return {"github_repo": repo, "token": decrypt(s.get("token_enc", "")), "auto_github": bool(s.get("auto_github"))}


def conf_out(cf: dict) -> dict:
    return {"github_repo": cf["github_repo"], "has_token": bool(cf["token"]), "auto_github": cf["auto_github"],
            "github_ready": bool(cf["github_repo"] and cf["token"])}


class SettingsIn(BaseModel):
    github_repo: str = Field(default="", max_length=140)
    token: str | None = Field(default=None, max_length=255)  # None keeps the saved one, "" removes it
    auto_github: bool = False


@router.get("/api/admin/bugs/settings")
def get_settings(p: Principal = Depends(require_admin)):
    with db.ro() as c:
        return conf_out(conf(c))


@router.put("/api/admin/bugs/settings")
def put_settings(body: SettingsIn, p: Principal = Depends(require_admin)):
    repo = body.github_repo.strip().removeprefix("https://github.com/").strip("/")
    if repo and not REPO_RE.match(repo):
        raise HTTPException(400, "The repository is owner/name, e.g. my-company/at-suite-bugs")
    with db.tx() as c:
        s = db.get_setting(c, "bug_reports", {}) or {}
        s["github_repo"] = repo
        s["auto_github"] = bool(body.auto_github)
        if body.token is not None:
            token = body.token.strip()
            if token and not TOKEN_RE.match(token):
                raise HTTPException(400, "That doesn't look like a GitHub token")
            s["token_enc"] = encrypt(token) if token else ""
        db.set_setting(c, "bug_reports", s)
        db.audit(c, p.name, "bugs.settings", repo or "GitHub off")
        return conf_out(conf(c))


# ---------------------------------------------------------------- reports --
def system_info(c) -> dict:
    """What the admins (and a GitHub issue) need to reproduce it. No addresses, keys or names."""
    from .core import build_info

    schema = c.execute("SELECT version FROM schema_version").fetchone()[0]
    b = build_info()
    return {"version": VERSION, "build": b["number"], "commit": b["commit"], "schema": schema,
            "os": platform.platform(), "python": platform.python_version(),
            "running_in": "Docker" if Path("/.dockerenv").exists() else "Python", "hosted": config.cfg.hosted}


def report_out(r, full: bool = False) -> dict:
    out = {
        "id": r["id"], "title": decrypt(r["title_enc"]), "reporter": r["reporter"], "reporter_kind": r["reporter_kind"],
        "room_name": r["room_name"], "status": r["status"], "created_at": r["created_at"],
        "resolved_at": r["resolved_at"], "resolved_by": r["resolved_by"], "has_screenshot": bool(r["screenshot"]),
        "github_url": r["github_url"], "github_error": r["github_error"],
    }
    if full:
        out.update(description=decrypt(r["description_enc"]), page=r["page"], user_agent=r["user_agent"],
                   app_version=r["app_version"], system=json.loads(r["system"] or "{}"))
    else:
        out["description"] = decrypt(r["description_enc"])[:300]
    return out


def report_or_404(c, report_id: int):
    r = c.execute("SELECT * FROM bug_reports WHERE id=?", (report_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Bug report not found")
    return r


def _too_many(who: str) -> bool:
    now = time.time()
    seen = [t for t in _recent.get(who, []) if now - t < 3600]
    _recent[who] = seen
    if len(seen) >= PER_HOUR:
        return True
    seen.append(now)
    return False


@router.post("/api/bugs")
async def report_bug(request: Request, title: str = Form(..., min_length=1, max_length=120), description: str = Form("", max_length=4000),
                     page: str = Form("", max_length=300), user_agent: str = Form("", max_length=400),
                     app_version: str = Form("", max_length=40),
                     p: Principal = Depends(require_reporter)):
    title, description = title.strip(), description.strip()
    if not title:
        raise HTTPException(400, "Say what went wrong first")
    shot = mime = None
    screenshot = (await request.form()).get("screenshot")
    if isinstance(screenshot, StarletteUpload):  # no file chosen arrives as ""
        data = await screenshot.read(MAX_SHOT + 1)
        if len(data) > MAX_SHOT:
            raise HTTPException(413, "The screenshot is too big (8 MB at most)")
        if data:
            mime = image_type(data)
            if not mime:
                raise HTTPException(400, "The screenshot must be a picture (PNG, JPEG, GIF or WebP)")
            shot = f"bug-{uuid.uuid4().hex}"
    if _too_many(p.label):
        raise HTTPException(429, f"That's {PER_HOUR} reports in the last hour. Please try again later.")
    if shot:
        (config.cfg.uploads / shot).write_bytes(data)
    with db.tx() as c:
        room = ""
        if p.kind == "node" and p.room_id:
            r = c.execute("SELECT name FROM rooms WHERE id=?", (p.room_id,)).fetchone()
            room = r["name"] if r else ""
        cur = c.execute(
            "INSERT INTO bug_reports(title_enc,description_enc,reporter,reporter_kind,room_name,page,user_agent,app_version,"
            "system,screenshot,screenshot_mime,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (encrypt(title), encrypt(description), p.name, p.kind, room, page.strip(), user_agent.strip(), app_version.strip(),
             json.dumps(system_info(c)), shot, mime, db.now_iso()))
        rid = cur.lastrowid
        db.audit(c, p.label, "bug.report", f"#{rid}")
        auto = conf(c)
        out = report_out(report_or_404(c, rid))
    await hub.publish("admins", "bug.new", {"id": rid, "title": title, "reporter": p.name, "room_name": room})
    if auto["auto_github"] and auto["github_repo"] and auto["token"]:
        task = asyncio.get_running_loop().create_task(_forward_quietly(rid))
        _sending.add(task)
        task.add_done_callback(_sending.discard)
    return {"id": rid, "status": out["status"]}


@router.get("/api/admin/bugs")
def list_bugs(status: str = "", p: Principal = Depends(require_admin)):
    with db.ro() as c:
        where, args = ("WHERE status=?", (status,)) if status in STATUSES else ("", ())
        rows = c.execute(f"SELECT * FROM bug_reports {where} ORDER BY status='resolved', id DESC LIMIT 500", args).fetchall()
        open_ = c.execute("SELECT COUNT(*) FROM bug_reports WHERE status='open'").fetchone()[0]
        return {"reports": [report_out(r) for r in rows], "open": open_, "settings": conf_out(conf(c))}


@router.get("/api/admin/bugs/{report_id}")
def get_bug(report_id: int, p: Principal = Depends(require_admin)):
    with db.ro() as c:
        return report_out(report_or_404(c, report_id), full=True)


@router.get("/api/admin/bugs/{report_id}/screenshot")
def get_screenshot(report_id: int, p: Principal = Depends(require_admin)):
    with db.ro() as c:
        r = report_or_404(c, report_id)
    path = config.cfg.uploads / (r["screenshot"] or "-")
    if not r["screenshot"] or not path.is_file():
        raise HTTPException(404, "No screenshot")
    return FileResponse(path, media_type=r["screenshot_mime"] or "application/octet-stream",
                        headers={"Cache-Control": "private, no-store", "Content-Security-Policy": "default-src 'none'"})


class StatusIn(BaseModel):
    status: str = Field(pattern="^(open|resolved)$")


@router.put("/api/admin/bugs/{report_id}")
async def set_status(report_id: int, body: StatusIn, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        report_or_404(c, report_id)
        if body.status == "resolved":
            c.execute("UPDATE bug_reports SET status='resolved', resolved_at=?, resolved_by=? WHERE id=?", (db.now_iso(), p.name, report_id))
        else:
            c.execute("UPDATE bug_reports SET status='open', resolved_at=NULL, resolved_by=NULL WHERE id=?", (report_id,))
        db.audit(c, p.name, f"bug.{body.status}", f"#{report_id}")
        out = report_out(report_or_404(c, report_id))
    await hub.publish("admins", "bug.updated", {"id": report_id, "status": out["status"]})
    return out


@router.delete("/api/admin/bugs/{report_id}")
async def delete_bug(report_id: int, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        r = report_or_404(c, report_id)
        c.execute("DELETE FROM bug_reports WHERE id=?", (report_id,))
        db.audit(c, p.name, "bug.delete", f"#{report_id}")
    if r["screenshot"]:
        (config.cfg.uploads / r["screenshot"]).unlink(missing_ok=True)
    await hub.publish("admins", "bug.updated", {"id": report_id, "status": "deleted"})
    return {"ok": True}


# ----------------------------------------------------------------- GitHub --
def issue_body(r) -> str:
    """The issue: what went wrong and the system. GitHub repositories can be public, so no
    names, organisation, rooms, addresses or screenshot go there."""
    s = json.loads(r["system"] or "{}")
    lines = [decrypt(r["description_enc"]) or "_No details given._", "", "---", "**Reported from AT SUITE**", "",
             f"- Version: {s.get('version', '?')} (build {s.get('build', '?')}, {s.get('commit', '?')}), schema {s.get('schema', '?')}",
             f"- Server: {s.get('running_in', '?')} on {s.get('os', '?')}, Python {s.get('python', '?')}" + (", hosted" if s.get("hosted") else ""),
             f"- Reported from: {'a tech laptop' if r['reporter_kind'] == 'node' else 'the console'}"
             + (f", page `{r['page']}`" if r["page"] else ""),
             ]
    if r["app_version"]:
        lines.append(f"- Laptop app: {r['app_version']}")
    if r["user_agent"]:
        lines.append(f"- Browser: {r['user_agent']}")
    lines += [f"- Report #{r['id']}, {r['created_at']}" + (" (has a screenshot on the server)" if r["screenshot"] else "")]
    return "\n".join(lines)


def post_issue(repo: str, token: str, title: str, body: str) -> str:
    """Opens the issue and returns its address."""
    r = httpx.post(f"{API}/repos/{repo}/issues", json={"title": title, "body": body, "labels": ["bug"]}, timeout=20,
                   headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": f"AT-SUITE/{VERSION}"})
    if r.status_code in (401, 403):
        raise RuntimeError("GitHub refused the token. It needs Issues: read and write on that repository.")
    if r.status_code == 404:
        raise RuntimeError("GitHub can't find that repository, or the token can't see it.")
    if r.status_code == 410:
        raise RuntimeError("Issues are turned off on that repository.")
    if r.status_code >= 300:
        raise RuntimeError(f"GitHub answered {r.status_code}. Try again later.")
    return r.json().get("html_url", "")


def forward(report_id: int) -> dict:
    with db.ro() as c:
        r = report_or_404(c, report_id)
        cf = conf(c)
    if r["github_url"]:
        return report_out(r)
    if not (cf["github_repo"] and cf["token"]):
        raise HTTPException(400, "Add a GitHub repository and token under Bug reports first")
    url, err = "", ""
    try:
        url = post_issue(cf["github_repo"], cf["token"], f"Bug: {decrypt(r['title_enc'])}", issue_body(r))
    except (RuntimeError, httpx.HTTPError) as exc:
        err = str(exc) if isinstance(exc, RuntimeError) else "Couldn't reach GitHub. Check the server's internet connection."
    with db.tx() as c:
        c.execute("UPDATE bug_reports SET github_url=?, github_error=? WHERE id=?", (url or None, err or None, report_id))
        if url:
            db.audit(c, "system", "bug.github", f"#{report_id} {url}")
        return report_out(report_or_404(c, report_id))


async def _forward_quietly(report_id: int) -> None:
    try:
        out = await asyncio.to_thread(forward, report_id)
        await hub.publish("admins", "bug.updated", {"id": report_id, "status": out["status"]})
    except Exception as exc:  # the report is saved either way; the admin can press Send to GitHub
        print("bug report to GitHub:", exc)


@router.post("/api/admin/bugs/{report_id}/github")
async def send_to_github(report_id: int, p: Principal = Depends(require_admin)):
    out = await asyncio.to_thread(forward, report_id)
    if out["github_error"] and not out["github_url"]:
        raise HTTPException(502, out["github_error"])
    return out
