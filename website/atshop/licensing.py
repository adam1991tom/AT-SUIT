"""Signs AT SUITE licence keys in exactly the format tools/licence.py makes and the server checks
(server/atsuit/licence.py): base64url(json) "." base64url(Ed25519 signature).

The vendor private key is read from LICENCE_SIGNING_KEY_FILE only when a key is signed. It is
never logged, sent to a browser, or returned by any endpoint, and nothing signs on request:
keys are only made for a verified payment, a trial, or an admin.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import stat
import time
from datetime import date, datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

DAY = 86400


class SigningError(RuntimeError):
    pass


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def end_of(d: date) -> int:
    """23:59:59 UTC on that day, as tools/licence.py does."""
    return int(datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc).timestamp())


def period_expiry(period_end: str | datetime, spare_days: int) -> int:
    """A paid period ending at `period_end` (Paddle's ends_at): its last day plus spare days."""
    if isinstance(period_end, str):
        period_end = datetime.fromisoformat(period_end.replace("Z", "+00:00"))
    last = period_end.astimezone(timezone.utc).date()
    return end_of(date.fromordinal(last.toordinal() + spare_days))


def days_expiry(start: date, days: int) -> int:
    """A fixed run of days starting on `start` (trial, event pass), no spare days."""
    return end_of(date.fromordinal(start.toordinal() + days))


def new_serial() -> str:
    return f"AT-{datetime.now(timezone.utc):%y%m%d}-{secrets.token_hex(3).upper()}"


class Signer:
    def __init__(self, key_file: str):
        self.key_file = key_file
        self._key: Ed25519PrivateKey | None = None

    def _private(self) -> Ed25519PrivateKey:
        if self._key is None:
            if not self.key_file:
                raise SigningError("LICENCE_SIGNING_KEY_FILE is not set, so no licence keys can be made")
            p = Path(self.key_file)
            if not p.is_file():
                raise SigningError(f"The signing key file {p} doesn't exist")
            mode = p.stat().st_mode
            if os.name == "posix" and mode & (stat.S_IRWXG | stat.S_IRWXO) and not p.is_relative_to("/run/secrets"):
                raise SigningError(f"The signing key file {p} can be read by other users; chmod 600 it")
            key = serialization.load_pem_private_key(p.read_bytes(), None)
            if not isinstance(key, Ed25519PrivateKey):
                raise SigningError("The signing key isn't an Ed25519 key")
            self._key = key
        return self._key

    @property
    def ready(self) -> bool:
        try:
            self._private()
            return True
        except (SigningError, ValueError, OSError):
            return False

    def public_key(self) -> Ed25519PublicKey:
        return self._private().public_key()

    def public_key_b64(self) -> str:
        return b64(self.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))

    def sign(self, *, licensee: str, edition: str, max_nodes: int, expires: int, max_sites: int = 1,
             modules: list[str] | None = None, serial: str | None = None, issued: int | None = None) -> tuple[str, dict]:
        """A licence key and its body. Same fields, order and encoding as tools/licence.py."""
        licensee = " ".join((licensee or "").split())[:120]
        if not licensee:
            raise SigningError("A licence needs the licensee's name")
        body = {
            "licensee": licensee, "edition": edition,
            "max_nodes": int(max_nodes), "max_sites": int(max_sites),
            "expires": int(expires), "modules": modules or ["*"], "issued": int(issued or time.time()),
            "serial": serial or new_serial(),
        }
        raw = json.dumps(body, separators=(",", ":")).encode()
        return f"{b64(raw)}.{b64(self._private().sign(raw))}", body

    def verify(self, key: str) -> dict | None:
        """The body of a key this shop signed, or None."""
        try:
            body_b64, sig_b64 = (key or "").strip().split(".", 1)
            raw = b64d(body_b64)
            self.public_key().verify(b64d(sig_b64), raw)
            return json.loads(raw)
        except (ValueError, InvalidSignature, SigningError):
            return None
