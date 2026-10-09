#!/usr/bin/env python3
"""Vendor licence tool. Keep the private key off customer servers and out of git.

    python tools/licence.py keygen                       # once: makes vendor-private-key.pem, prints the public key
    python tools/licence.py issue --licensee "Venue Ltd" --plan venue            # a year of the Venue plan
    python tools/licence.py issue --licensee "Venue Ltd" --plan small --months 1 # a month (monthly billing)
    python tools/licence.py issue --licensee "Venue Ltd" --plan venue --until 2027-10-31
    python tools/licence.py issue --licensee "Hire Co" --plan event              # a 7-day event pass
    python tools/licence.py issue --licensee "Venue Ltd" --plan trial            # a 30-day free trial
    python tools/licence.py show <key>

Plans set how many laptops (nodes) can join; every plan has every module. A paid
period gets SPARE_DAYS on top, so a renewal that's a few days late never trips
the warnings; after the end there are 14 days of grace before AT-SUIT locks.
Renewing is issuing a new key for the next period: the customer pastes it into Licence.

The public key printed by keygen lives in server/atsuit/vendor_pubkey.txt and is
the only key AT-SUIT trusts. Licences are checked offline.
"""
from __future__ import annotations

import argparse
import base64
import calendar
import json
import secrets
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MODULES = ["comms", "timers", "fleet", "captions", "overlays", "dashboard", "presenter"]
SPARE_DAYS = 7
# nodes: laptops that can join (0 = unlimited); days: a fixed length instead of --months.
PLANS = {
    "small": {"nodes": 10},
    "venue": {"nodes": 30},
    "large": {"nodes": 75},
    "enterprise": {"nodes": 0},
    "trial": {"nodes": 10, "days": 30},
    "event": {"nodes": 10, "days": 7},
    "owner": {"nodes": 0, "sites": 0, "never": True},  # your own servers: unlimited, never ends
}


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def end_of(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc).timestamp())


def expiry(plan: str, a) -> int:
    """When the key stops: the end of the paid period, plus spare days for a paid plan."""
    p = PLANS[plan]
    if p.get("never") or a.days == 0:
        return 0
    today = datetime.now(timezone.utc).date()
    spare = a.spare if a.spare is not None else (0 if plan in ("trial", "event") else SPARE_DAYS)
    if a.until:
        end = date.fromisoformat(a.until)
    elif a.days or p.get("days"):
        end = date.fromordinal(today.toordinal() + (a.days or p["days"]))
    else:
        end = add_months(today, a.months)
    return end_of(date.fromordinal(end.toordinal() + spare))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--out", default="vendor-private-key.pem")
    i = sub.add_parser("issue")
    i.add_argument("--key", default="vendor-private-key.pem", help="the vendor private key")
    i.add_argument("--licensee", required=True)
    i.add_argument("--plan", choices=PLANS, default="venue")
    i.add_argument("--nodes", type=int, help="laptops that can join, overriding the plan (0 = unlimited)")
    i.add_argument("--sites", type=int, help="venues (sites) on this server, default 1 (0 = unlimited)")
    i.add_argument("--months", type=int, default=12, help="length of the paid period (default 12)")
    i.add_argument("--days", type=int, help="length in days instead of months (0 = never ends)")
    i.add_argument("--until", help="last day of the paid period, YYYY-MM-DD, instead of --months")
    i.add_argument("--spare", type=int, help=f"extra days after the period (default {SPARE_DAYS}; 0 for trial and event)")
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
        plan = PLANS[a.plan]
        mods = ["*"] if a.modules.strip() == "*" else [m.strip() for m in a.modules.split(",") if m.strip() in MODULES]
        now = int(time.time())
        body = {
            "licensee": a.licensee, "edition": a.plan,
            "max_nodes": plan["nodes"] if a.nodes is None else a.nodes,
            "max_sites": plan.get("sites", 1) if a.sites is None else a.sites,
            "expires": expiry(a.plan, a), "modules": mods, "issued": now,
            "serial": f"AT-{datetime.now(timezone.utc):%y%m%d}-{secrets.token_hex(2).upper()}",
        }
        raw = json.dumps(body, separators=(",", ":")).encode()
        ends = datetime.fromtimestamp(body["expires"], timezone.utc).date().isoformat() if body["expires"] else "never"
        print(f"{body['serial']}: {a.licensee}, {a.plan}, {body['max_nodes'] or 'unlimited'} laptops, ends {ends}", file=sys.stderr)
        print(f"{b64(raw)}.{b64(priv.sign(raw))}")
    else:
        body = json.loads(b64d(a.licence.split(".", 1)[0]))
        for k in ("issued", "expires"):
            if body.get(k):
                body[f"{k}_date"] = datetime.fromtimestamp(body[k], timezone.utc).isoformat()
        print(json.dumps(body, indent=2))


if __name__ == "__main__":
    main()
