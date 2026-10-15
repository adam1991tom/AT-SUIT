"""Managed hosting: the shop's only link to the hosting server is the `atcloud` tool (cloud/atcloud).

Every change is a job in `hosting_jobs`. In HOSTING_MODE=manual (the default) jobs wait in
Admin → Hosting with the exact command to run, and you mark them done. In HOSTING_MODE=ssh the
shop runs the command itself over SSH, as a user on the hosting server that may run atcloud.

The commands used, all of which atcloud already has:
    atcloud new <venue> --name "<name>" --licence /dev/stdin     (key on stdin)
    atcloud licence <venue> /dev/stdin                            (key on stdin)
    atcloud suspend <venue> / atcloud resume <venue>
    atcloud link <venue>    atcloud info <venue>
Nothing here removes a venue or its data; that stays a manual `atcloud remove`.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import time

import httpx

from .db import audit, now

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")
# Same list as cloud/atcloud, plus the shop's own names.
RESERVED = {"www", "admin", "api", "app", "mail", "smtp", "status", "help", "kb", "docs", "cloud", "edge", "portal",
            "billing", "shop", "support", "dashboard", "login", "account", "accounts", "static", "cdn", "ns1", "ns2",
            "atsuite", "atsuit", "test", "demo"}


def check_slug(slug: str) -> str:
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug) or "--" in slug:
        raise ValueError("Use 1 to 32 lowercase letters, numbers and dashes, starting and ending with a letter or number.")
    if slug in RESERVED:
        raise ValueError("That address is reserved. Please pick another.")
    return slug


def address(settings, slug: str) -> str:
    return f"https://{slug}.{settings.hosting_domain}" if settings.hosting_domain else ""


def queue(c, hosting_id: int, action: str, licence_serial: str | None = None) -> int:
    cur = c.execute("INSERT INTO hosting_jobs (hosting_id, action, licence_serial, created_at) VALUES (?,?,?,?)",
                    (hosting_id, action, licence_serial, now()))
    return cur.lastrowid


def command(settings, job, hosting) -> tuple[list[str], bool]:
    """The atcloud command for a job, and whether the licence key goes on stdin."""
    a, slug = settings.hosting_atcloud, hosting["slug"]
    if job["action"] == "new":
        return [a, "new", slug, "--name", hosting["name"], "--licence", "/dev/stdin"], True
    if job["action"] == "licence":
        return [a, "licence", slug, "/dev/stdin"], True
    if job["action"] in ("suspend", "resume", "link", "info"):
        return [a, job["action"], slug], False
    raise ValueError(job["action"])


def manual_text(settings, job, hosting) -> str:
    """What to paste on the hosting server for a manual job (the key is saved to a file first)."""
    cmd, stdin = command(settings, job, hosting)
    if stdin:
        cmd = cmd[:-1] + [f"{hosting['slug']}-key.txt"]
    return shlex.join(cmd)


class Runner:
    """Runs atcloud over SSH. Swapped for a fake in tests."""

    def __init__(self, settings):
        self.s = settings

    def __call__(self, argv: list[str], stdin: str | None) -> tuple[int, str]:
        ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
        if self.s.hosting_ssh_key:
            ssh += ["-i", self.s.hosting_ssh_key]
        r = subprocess.run(ssh + [self.s.hosting_ssh, shlex.join(argv)], input=stdin, capture_output=True,
                           text=True, timeout=600)
        return r.returncode, (r.stdout + r.stderr)[-4000:]


def run_pending(db, settings, runner=None) -> int:
    """In ssh mode, run the jobs that are waiting. Returns how many ran."""
    if settings.hosting_mode != "ssh":
        return 0
    runner = runner or Runner(settings)
    done = 0
    for job in db.all("SELECT * FROM hosting_jobs WHERE status='todo' ORDER BY id LIMIT 10"):
        h = db.one("SELECT * FROM hosting WHERE id=?", job["hosting_id"])
        key = None
        if job["licence_serial"]:
            lic = db.one("SELECT key FROM licences WHERE serial=?", job["licence_serial"])
            key = lic["key"] if lic else None
        argv, wants_key = command(settings, job, h)
        if wants_key and not key:
            db.run("UPDATE hosting_jobs SET status='failed', result=?, done_at=? WHERE id=?",
                   "No licence key for this job", now(), job["id"])
            continue
        try:
            code, out = runner(argv, key if wants_key else None)
        except (OSError, subprocess.SubprocessError) as e:
            code, out = 1, str(e)
        with db.tx() as c:
            c.execute("UPDATE hosting_jobs SET status=?, result=?, done_at=? WHERE id=?",
                      ("done" if code == 0 else "failed", out, now(), job["id"]))
            if code == 0:
                apply_done(c, settings, job, h, out)
            audit(c, "system", f"hosting.{job['action']}", h["slug"], {"ok": code == 0})
        done += 1
    return done


def apply_done(c, settings, job, h, output: str = "") -> None:
    """What a finished job means for the venue's record."""
    if job["action"] == "new":
        link = next((ln.strip() for ln in output.splitlines() if "/setup?code=" in ln), "")
        c.execute("UPDATE hosting SET status='active', setup_link=COALESCE(NULLIF(?, ''), setup_link) WHERE id=?",
                  (link, h["id"]))
    elif job["action"] == "suspend":
        c.execute("UPDATE hosting SET status='suspended' WHERE id=?", (h["id"],))
    elif job["action"] == "resume":
        c.execute("UPDATE hosting SET status='active' WHERE id=?", (h["id"],))


_health_cache: dict[str, tuple[float, dict]] = {}


def health(settings, slug: str, transport=None) -> dict:
    """The venue's own /api/health, cached for a minute: {"up": bool, "version": …}."""
    hit = _health_cache.get(slug)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    url = address(settings, slug)
    out = {"up": False, "version": "", "setup_complete": None, "checked": int(time.time())}
    if url:
        try:
            with httpx.Client(timeout=5, transport=transport) as cl:
                r = cl.get(f"{url}/api/health")
            if r.status_code == 200:
                d = r.json()
                out.update(up=bool(d.get("ok")), version=str(d.get("version", "")), setup_complete=d.get("setup_complete"))
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            pass
    _health_cache[slug] = (time.time(), out)
    return out
