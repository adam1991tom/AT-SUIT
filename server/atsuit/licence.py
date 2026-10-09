"""Offline licence check.

A licence key is `<base64url(json)>.<base64url(ed25519 signature)>`, signed with
the vendor's private key (see tools/licence.py). The matching public key ships
in `vendor_pubkey.txt` inside the image. Without a valid licence the server
runs in evaluation mode with the limits in EVALUATION.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ALL_MODULES = ["comms", "timers", "fleet", "captions", "overlays", "dashboard", "presenter"]
# The modules of the first release. A licence issued then that lists all of
# them was a full-suite licence, so it covers modules added since.
FIRST_MODULES = {"comms", "timers", "fleet", "captions", "overlays", "dashboard"}


@dataclass
class Licence:
    licensee: str = "Evaluation"
    edition: str = "evaluation"
    expires: int = 0  # unix time, 0 = never
    max_nodes: int = 5
    max_sites: int = 1
    modules: list[str] = field(default_factory=lambda: list(ALL_MODULES))
    valid: bool = False
    reason: str = "No licence installed"
    serial: str = ""
    issued: int = 0  # unix time, 0 = not recorded

    def allows(self, module: str) -> bool:
        return module in self.modules

    def public(self) -> dict:
        return asdict(self)


EVALUATION = Licence()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _vendor_raw() -> tuple[str, str]:
    """The vendor public key and where it came from."""
    raw = os.getenv("ATSUIT_VENDOR_PUBKEY", "").strip()
    if raw:
        return raw, "ATSUIT_VENDOR_PUBKEY"
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


def parse(key: str) -> Licence:
    key = (key or "").strip()
    if not key:
        return EVALUATION
    pub = vendor_public_key()
    if pub is None:
        return Licence(reason="This build has no vendor key, so licences can't be checked")
    try:
        body_b64, sig_b64 = key.split(".", 1)
        body = _b64d(body_b64)
        pub.verify(_b64d(sig_b64), body)
        data = json.loads(body)
    except (ValueError, InvalidSignature):
        return Licence(reason="Licence key is not valid")
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
    )
    if lic.expires and lic.expires < time.time():
        return Licence(reason=f"Licence for {lic.licensee} expired")
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


def current(c) -> Licence:
    from . import db

    return parse(db.get_setting(c, "licence_key", ""))
