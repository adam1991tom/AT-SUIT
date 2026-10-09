#!/usr/bin/env python3
"""Vendor licence tool. Keep the private key off customer servers.

    python tools/licence.py keygen                      # once: makes vendor-private-key.pem, prints the public key
    python tools/licence.py issue --licensee "Venue Ltd" --nodes 40 --sites 2 --days 365
    python tools/licence.py show <key>

Put the printed public key in server/atsuit/vendor_pubkey.txt before building
images you sell (or set ATSUIT_VENDOR_PUBKEY). Licences are checked offline.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MODULES = ["comms", "timers", "fleet", "captions", "overlays", "dashboard", "presenter"]


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--out", default="vendor-private-key.pem")
    i = sub.add_parser("issue")
    i.add_argument("--key", default="vendor-private-key.pem")
    i.add_argument("--licensee", required=True)
    i.add_argument("--edition", default="standard")
    i.add_argument("--nodes", type=int, default=0, help="0 = unlimited")
    i.add_argument("--sites", type=int, default=1, help="0 = unlimited")
    i.add_argument("--days", type=int, default=365, help="0 = never expires")
    i.add_argument("--modules", default="*", help="comma-separated, or * for every module (including future ones)")
    s = sub.add_parser("show")
    s.add_argument("licence")
    a = ap.parse_args()

    if a.cmd == "keygen":
        out = Path(a.out)
        if out.exists():
            sys.exit(f"{out} already exists; refusing to overwrite a vendor key")
        priv = Ed25519PrivateKey.generate()
        out.write_bytes(priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
        out.chmod(0o600)
        pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        print(f"Private key: {out} (keep it secret, back it up)")
        print(f"Public key for server/atsuit/vendor_pubkey.txt:\n{b64(pub)}")
    elif a.cmd == "issue":
        priv = serialization.load_pem_private_key(Path(a.key).read_bytes(), None)
        mods = ["*"] if a.modules.strip() == "*" else [m.strip() for m in a.modules.split(",") if m.strip() in MODULES]
        body = json.dumps({
            "licensee": a.licensee, "edition": a.edition, "max_nodes": a.nodes, "max_sites": a.sites,
            "expires": int(time.time() + a.days * 86400) if a.days else 0, "modules": mods, "issued": int(time.time()),
        }, separators=(",", ":")).encode()
        print(f"{b64(body)}.{b64(priv.sign(body))}")
    else:
        print(json.dumps(json.loads(b64d(a.licence.split(".", 1)[0])), indent=2))


if __name__ == "__main__":
    main()
