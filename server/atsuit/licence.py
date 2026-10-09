"""Offline licence check, and what happens as a subscription runs out.

A licence key is `<base64url(json)>.<base64url(ed25519 signature)>`, signed with
the vendor's private key (see tools/licence.py). The matching public key ships
in `vendor_pubkey.txt` inside the image and is the only key trusted. Keys are
checked offline, so a venue server needs no internet.

AT-SUIT doesn't work until a valid key is installed. A subscription key carries
its end date:
  - WARN_ADMIN_DAYS before it ends admins see a warning; WARN_ALL_DAYS before, everyone does;
  - when it ends there are GRACE_DAYS of grace, with everything still working;
  - after that it locks (only the Licence page works), but never during a show:
    the lock waits for a restart or the small hours, with no timer running and
    no captions live. Nothing is deleted, and a new key unlocks it at once.
The server remembers the latest time it has seen, so winding the clock back
doesn't bring an expired key back to life.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import db

ALL_MODULES = ["comms", "timers", "fleet", "captions", "overlays", "dashboard", "presenter"]
# The modules of the first release. A licence issued then that lists all of
# them was a full-suite licence, so it covers modules added since.
FIRST_MODULES = {"comms", "timers", "fleet", "captions", "overlays", "dashboard"}

DAY = 86400
WARN_ADMIN_DAYS = 30
WARN_ALL_DAYS = 7
GRACE_DAYS = 14
# Installs set up before keys were required get this long to add one.
UPGRADE_GRACE_DAYS = 14
# A due lock waits for these hours (site time), or a restart, and for no show to be on.
LOCK_HOURS = range(2, 5)
CHECK_EVERY_S = 300


@dataclass
class Licence:
    licensee: str = "Not licensed"
    edition: str = "none"
    expires: int = 0  # unix time, 0 = never
    max_nodes: int = 5
    max_sites: int = 1
    modules: list[str] = field(default_factory=lambda: list(ALL_MODULES))
    valid: bool = False
    reason: str = "No licence installed"
    serial: str = ""
    issued: int = 0  # unix time, 0 = not recorded
    # none: no key; invalid: a bad key; active; expiring: ends within WARN_ADMIN_DAYS;
    # grace: ended (or never had a key, on an upgraded install) but still working; lapsed: grace is over
    state: str = "none"
    grace_until: int = 0  # unix time the grace ends, when in grace or lapsed

    def allows(self, module: str) -> bool:
        return module in self.modules

    def public(self) -> dict:
        return asdict(self)


NONE = Licence()


def _now() -> float:
    return time.time()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _vendor_raw() -> tuple[str, str]:
    """The vendor public key and where it came from. Only the file in the image counts:
    there's no setting or variable to swap it, so nobody can sign keys of their own."""
    f = Path(__file__).with_name("vendor_pubkey.txt")
    return (f.read_text().strip() if f.exists() else ""), "vendor_pubkey.txt"


def vendor_key_id() -> str:
    """A short fingerprint of the vendor public key, to tell builds apart."""
    raw = _vendor_raw()[0]
    try:
        return hashlib.sha256(_b64d(raw)).hexdigest()[:16] if raw else ""
    except ValueError:
        return ""


def vendor_public_key() -> Ed25519PublicKey | None:
    raw = _vendor_raw()[0]
    if not raw:
        return None
    try:
        return Ed25519PublicKey.from_public_bytes(_b64d(raw))
    except ValueError:
        return None


def _modules(listed) -> list[str]:
    listed = set(listed) if isinstance(listed, list) else {listed}
    if "*" in listed or FIRST_MODULES <= listed:
        return list(ALL_MODULES)
    return [m for m in ALL_MODULES if m in listed]


def _day(ts: float, tz: str = "UTC") -> str:
    d = datetime.fromtimestamp(ts, ZoneInfo(tz))
    return f"{d.day} {d:%b %Y}"


def parse(key: str, now: float | None = None) -> Licence:
    key = (key or "").strip()
    if not key:
        return NONE
    pub = vendor_public_key()
    if pub is None:
        return Licence(state="invalid", reason="This build has no vendor key, so licences can't be checked")
    try:
        body_b64, sig_b64 = key.split(".", 1)
        body = _b64d(body_b64)
        pub.verify(_b64d(sig_b64), body)
        data = json.loads(body)
    except (ValueError, InvalidSignature):
        return Licence(state="invalid", reason="Licence key is not valid")
    lic = Licence(
        licensee=str(data.get("licensee", "")),
        edition=str(data.get("edition", "standard")),
        expires=int(data.get("expires", 0)),
        max_nodes=int(data.get("max_nodes", 0)),
        max_sites=int(data.get("max_sites", 1)),
        modules=_modules(data.get("modules", ALL_MODULES)),
        valid=True,
        reason="",
        serial=str(data.get("serial") or data.get("id") or hashlib.sha256(body).hexdigest()[:12].upper()),
        issued=int(data.get("issued", 0) or 0),
        state="active",
    )
    if lic.expires:
        # The clock can't be earlier than the day the key was made.
        now = max(_now() if now is None else now, lic.issued)
        if now >= lic.expires:
            lic.grace_until = lic.expires + GRACE_DAYS * DAY
            if now < lic.grace_until:
                lic.state, lic.reason = "grace", f"Ended on {_day(lic.expires)}; works until {_day(lic.grace_until)}"
            else:
                lic.valid, lic.state, lic.reason = False, "lapsed", f"Licence for {lic.licensee} expired on {_day(lic.expires)}"
        elif lic.expires - now <= WARN_ADMIN_DAYS * DAY:
            lic.state = "expiring"
    return lic


def details(key: str) -> dict:
    """Everything about an installed key for Licence, including what
    an expired licence said. Admins only: it holds the raw key."""
    key = (key or "").strip()
    raw, source = _vendor_raw()
    out = {"installed": bool(key), "raw": key, "signature_valid": False, "payload": {},
           "vendor_key_id": vendor_key_id(), "vendor_key_source": source if raw else ""}
    pub = vendor_public_key()
    if not key or pub is None:
        return out
    try:
        body_b64, sig_b64 = key.split(".", 1)
        body = _b64d(body_b64)
        out["payload"] = json.loads(body)
        pub.verify(_b64d(sig_b64), body)
        out["signature_valid"] = True
    except (ValueError, InvalidSignature):
        pass
    return out


# ----------------------------------------------------------------- the clock --
def clock(c) -> float:
    """Now, or the latest time this server has seen if the clock has gone back since."""
    s = db.get_setting(c, "licence_clock", {}) or {}
    return max(_now(), float(s.get("max") or 0))


def tick(c, issued: int = 0) -> None:
    """Remember the latest time seen. A key issued later than any before it resets the
    remembered time, in case the clock once ran ahead: the vendor's date is the truth."""
    s = db.get_setting(c, "licence_clock", {}) or {}
    now = _now()
    if issued and issued > int(s.get("issued_max") or 0):
        s = {"max": max(now, issued), "issued_max": issued}
    else:
        s["max"] = max(now, float(s.get("max") or 0))
    db.set_setting(c, "licence_clock", s)


def check_new(c, key: str) -> Licence:
    """A key about to be installed, judged by the time it will see once installed:
    a key issued later than any before resets a clock that ran ahead (see tick)."""
    lic = parse(key, now=clock(c))
    s = db.get_setting(c, "licence_clock", {}) or {}
    if lic.issued and lic.issued > int(s.get("issued_max") or 0):
        lic = parse(key, now=max(_now(), lic.issued))
    return lic


def site_tz(c) -> str:
    r = c.execute("SELECT timezone FROM sites ORDER BY id LIMIT 1").fetchone()
    return (r["timezone"] if r else "") or "UTC"


# ---------------------------------------------------------------- the licence --
def current(c) -> Licence:
    lic = parse(db.get_setting(c, "licence_key", ""), now=clock(c))
    if not lic.valid:  # an upgraded install with no working key may still be in its grace (see prepare)
        until = int(db.get_setting(c, "licence_grace_until", 0) or 0)
        if until and clock(c) < until:
            if lic.state != "lapsed":
                lic = Licence()
            lic.state, lic.grace_until, lic.reason = "grace", until, f"Needs a licence key by {_day(until, site_tz(c))}"
    return lic


def locked(c) -> bool:
    """Whether only the Licence page works. A server that has never had a key (and isn't an
    upgraded install in its grace) is locked straight away; one that had a licence locks only
    once enforce() has found a quiet moment."""
    lic = current(c)
    if lic.valid or lic.state == "grace":
        return False
    return not db.get_setting(c, "licence_in_use", False) or bool(db.get_setting(c, "licence_locked", False))


_cache: tuple[float, bool] = (0.0, False)


def locked_cached() -> bool:
    """locked(), looked up at most every few seconds: every request asks."""
    global _cache
    if time.monotonic() - _cache[0] > 5:
        with db.ro() as c:
            _cache = (time.monotonic(), locked(c))
    return _cache[1]


def forget() -> None:
    global _cache
    _cache = (0.0, False)


def installed(c, lic: Licence, actor: str) -> None:
    """A valid key went in (setup or Licence): the server is in use and unlocked."""
    tick(c, lic.issued)
    db.set_setting(c, "licence_in_use", True)
    db.set_setting(c, "licence_locked", False)
    db.set_setting(c, "licence_grace_until", 0)
    db.audit(c, actor, "licence.update", f"{lic.licensee} {lic.serial}".strip())
    forget()


def prepare(c) -> None:
    """Once, on the first start of a version that needs keys. An install that was already set
    up keeps working: with a valid key it carries on as before; without one (or with one that
    has run out) it gets UPGRADE_GRACE_DAYS to add one."""
    if db.get_setting(c, "licence_checked", False):
        return
    db.set_setting(c, "licence_checked", True)
    if not db.get_setting(c, "setup_complete", False):
        return
    db.set_setting(c, "licence_in_use", True)
    if not parse(db.get_setting(c, "licence_key", ""), now=clock(c)).valid:
        db.set_setting(c, "licence_grace_until", int(_now()) + UPGRADE_GRACE_DAYS * DAY)


def show_on(c) -> list[str]:
    """Rooms with a show on: a timer running or paused, or captions live."""
    from .modules import captions
    from .updates import live_rooms

    names = live_rooms(c)
    live = [rid for rid, st in captions.rooms.items() if st.ws]
    if live:
        q = ",".join("?" * len(live))
        names += [r["name"] for r in c.execute(f"SELECT name FROM rooms WHERE id IN ({q})", live)]
    return sorted(set(names))


def enforce(at_start: bool = False, hour: int | None = None) -> bool:
    """Lock a lapsed licence, but only at a restart or in the small hours, and never with a
    show on. Returns whether it locked."""
    with db.tx() as c:
        tick(c)
        lic = current(c)
        if lic.valid or lic.state == "grace" or not db.get_setting(c, "licence_in_use", False):
            return False
        if db.get_setting(c, "licence_locked", False):
            return False
        if hour is None:
            hour = datetime.now(ZoneInfo(site_tz(c))).hour
        if not at_start and hour not in LOCK_HOURS:
            return False
        if show_on(c):
            return False
        db.set_setting(c, "licence_locked", True)
        db.audit(c, "licence", "licence.locked", lic.reason or "no licence key")
    forget()
    return True


async def watch() -> None:
    at_start = True
    while True:
        try:
            await asyncio.to_thread(enforce, at_start)
            at_start = False
        except Exception as exc:  # never let the check kill the server
            print("licence:", exc)
        await asyncio.sleep(CHECK_EVERY_S)


def notice(c, role: str) -> dict | None:
    """The banner the console and the workspace show while a licence is running out:
    {"level": "warn" | "bad", "text", "admin"}. Admins hear first; everyone else in the last week."""
    lic, now, tz = current(c), clock(c), site_tz(c)
    admin = role == "admin"
    what = "trial" if lic.edition == "trial" else "subscription"
    if lic.state == "expiring":
        days = math.ceil((lic.expires - now) / DAY)
        if admin:
            return {"level": "warn", "admin": True, "text": f"Your AT-SUIT {what} ends on {_day(lic.expires, tz)} "
                    f"({days} day{'s' if days != 1 else ''}). Paste the renewal key in Licence."}
        if days <= WARN_ALL_DAYS:
            return {"level": "warn", "admin": False, "text": f"This venue's AT-SUIT {what} ends on {_day(lic.expires, tz)}. "
                    "Ask your admin to renew it."}
        return None
    if lic.state == "grace" and not lic.expires:  # an upgraded install that has never had a key
        if admin:
            return {"level": "warn", "admin": True, "text": f"AT-SUIT now needs a licence key. Add one in Licence by "
                    f"{_day(lic.grace_until, tz)}; after that it locks (never during a show)."}
        if lic.grace_until - now <= WARN_ALL_DAYS * DAY:
            return {"level": "warn", "admin": False, "text": f"AT-SUIT on this server needs a licence key by "
                    f"{_day(lic.grace_until, tz)}. Ask your admin."}
        return None
    if lic.state == "grace":
        if admin:
            return {"level": "bad", "admin": True, "text": f"Your AT-SUIT {what} ended on {_day(lic.expires, tz)}. It keeps "
                    f"working until {_day(lic.grace_until, tz)}, then locks. Paste the renewal key in Licence."}
        return {"level": "bad", "admin": False, "text": f"This venue's AT-SUIT {what} has ended. It keeps working until "
                f"{_day(lic.grace_until, tz)}. Ask your admin to renew it."}
    if not lic.valid and not locked(c):  # due to lock at the next quiet moment
        if admin:
            return {"level": "bad", "admin": True, "text": "Your AT-SUIT licence has run out. It locks tonight, once no show "
                    "is on. Paste a new key in Licence."}
        return {"level": "bad", "admin": False, "text": "This venue's AT-SUIT licence has run out. Ask your admin to renew it."}
    return None
