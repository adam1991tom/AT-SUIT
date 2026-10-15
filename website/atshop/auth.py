"""Customer sign-in by emailed link (no passwords to leak, reuse or reset), sessions, CSRF and
the admins' second factor (a 6-digit code from an authenticator app, RFC 6238).

- Sign-in links are 32 random bytes, stored only as a SHA-256 hash, single use, 20 minutes.
- Sessions are random tokens in an HttpOnly, SameSite=Lax cookie (Secure on https), stored hashed,
  30 days for customers and 12 hours once a session is used for admin.
- Every form that changes something carries the session's CSRF token.
- Admins are the addresses in ADMIN_EMAILS, and must also pass a TOTP code each session.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import struct
import time
from urllib.parse import quote

from .db import audit, now

COOKIE = "atshop_session"
LINK_TTL = 20 * 60
SESSION_TTL = 30 * 86400
ADMIN_TTL = 12 * 3600
EMAIL_RE = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def clean_email(v: str) -> str:
    v = (v or "").strip().lower()
    if not EMAIL_RE.match(v):
        raise ValueError("Please enter a valid email address.")
    return v


# -------------------------------------------------------------- rate limits --
def limited(db, key: str, limit: int, window_s: int) -> bool:
    """True when `key` has been used `limit` times in this window."""
    w = int(time.time()) // window_s
    with db.tx() as c:
        row = c.execute("SELECT window, count FROM rate WHERE key=?", (key,)).fetchone()
        if not row or row["window"] != w:
            c.execute("INSERT OR REPLACE INTO rate (key, window, count) VALUES (?,?,1)", (key, w))
            return False
        if row["count"] >= limit:
            return True
        c.execute("UPDATE rate SET count=count+1 WHERE key=?", (key,))
        return False


# ------------------------------------------------------------- login links --
def make_link(c, settings, email: str, data: dict, ip: str, next_url: str = "") -> str:
    token = secrets.token_urlsafe(32)
    c.execute("INSERT INTO login_tokens (token_hash, email, data, created_at, expires_at, ip) VALUES (?,?,?,?,?,?)",
              (sha(token), email, json.dumps(data), now(), now() + LINK_TTL, ip))
    nxt = f"&next={quote(next_url)}" if next_url else ""
    return f"{settings.site_url}/login/confirm?token={token}{nxt}"


def use_link(db, token: str):
    """The account a sign-in link belongs to (made on first use), or None if it's bad, used or old."""
    if not token or len(token) > 100:
        return None
    with db.tx() as c:
        row = c.execute("SELECT * FROM login_tokens WHERE token_hash=?", (sha(token),)).fetchone()
        if not row or row["used_at"] or row["expires_at"] < now():
            return None
        c.execute("UPDATE login_tokens SET used_at=? WHERE token_hash=?", (now(), row["token_hash"]))
        acc = c.execute("SELECT * FROM accounts WHERE email=?", (row["email"],)).fetchone()
        if acc is None:
            d = json.loads(row["data"] or "{}")
            c.execute("INSERT INTO accounts (email, name, organisation, phone, marketing, created_at) VALUES (?,?,?,?,?,?)",
                      (row["email"], d.get("name", "")[:120], d.get("organisation", "")[:120], d.get("phone", "")[:40],
                       int(bool(d.get("marketing"))), now()))
            acc = c.execute("SELECT * FROM accounts WHERE email=?", (row["email"],)).fetchone()
            audit(c, row["email"], "account.create", str(acc["id"]), ip=row["ip"] or "")
        if acc["disabled"]:
            return None
        c.execute("UPDATE accounts SET last_login_at=? WHERE id=?", (now(), acc["id"]))
        return acc


# ----------------------------------------------------------------- sessions --
def start_session(db, account_id: int, ip: str, ua: str) -> str:
    token = secrets.token_urlsafe(32)
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (now(),))
        c.execute("INSERT INTO sessions (token_hash, account_id, csrf, created_at, expires_at, ip, user_agent)"
                  " VALUES (?,?,?,?,?,?,?)", (sha(token), account_id, secrets.token_urlsafe(24), now(),
                                               now() + SESSION_TTL, ip, (ua or "")[:200]))
    return token


def session(db, token: str | None):
    if not token or len(token) > 100:
        return None
    row = db.one("SELECT s.*, a.email, a.name, a.organisation, a.disabled FROM sessions s JOIN accounts a "
                 "ON a.id = s.account_id WHERE s.token_hash=?", sha(token))
    if not row or row["expires_at"] < now() or row["disabled"]:
        return None
    return row


def end_session(db, token: str | None) -> None:
    if token:
        db.run("DELETE FROM sessions WHERE token_hash=?", sha(token))


def csrf_ok(sess, value: str | None) -> bool:
    return bool(sess and value and hmac.compare_digest(sess["csrf"], value))


# ------------------------------------------------------------------ TOTP --
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp(secret: str, at: float | None = None, step: int = 30) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = int((at or time.time()) // step)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_ok(secret: str, code: str, at: float | None = None) -> bool:
    code = (code or "").replace(" ", "")
    if not secret or not code.isdigit() or len(code) != 6:
        return False
    t = at or time.time()
    return any(hmac.compare_digest(totp(secret, t + d * 30), code) for d in (-1, 0, 1))


def otpauth_uri(secret: str, email: str) -> str:
    return f"otpauth://totp/AT%20SUITE%20admin:{quote(email)}?secret={secret}&issuer=AT%20SUITE"
