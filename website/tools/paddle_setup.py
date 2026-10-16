#!/usr/bin/env python3
"""Create the AT SUITE products and prices in Paddle from catalogue.toml.

    python tools/paddle_setup.py            # show what would be created (changes nothing)
    python tools/paddle_setup.py --apply    # create what's missing and save data/paddle-prices.<env>.json

Run it once when the Paddle account is new, and again after changing a price in catalogue.toml.
It reads the account first: prices that already exist at today's amount are left alone, including ones
made earlier on another computer (they carry kind/key/cycle in their custom data and are just recorded).
It only ever adds: it never edits or deletes a product or price unless you ask with --archive-old.
A changed price gets a new Paddle price; the old one stays in the prices file, mapped to its plan, so customers already on it keep getting keys.
Add --archive-old to also archive the old prices in Paddle so nobody new can buy them (subscribers on
them carry on).

It reads the same environment as the shop (PADDLE_ENV, PADDLE_API_KEY, SHOP_DATA). It refuses to touch
the live account unless SHOP_ALLOW_LIVE=yes, like the shop itself. When both price files exist it
prints the sandbox → live price id mapping.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atshop import catalogue as catmod  # noqa: E402
from atshop import config  # noqa: E402
from atshop.paddle import Paddle  # noqa: E402

PRODUCT_TEXT = {
    "plan": "AT SUITE licence for {name}: the show-room toolkit for event technicians.",
    "addon": "We run your AT SUITE server for you: one sealed server per venue, backups and updates.",
    "pass": "A one-off AT SUITE licence for a single show or hire.",
}


def product_key(kind: str, key: str) -> str:
    return f"{kind}:{key}"


def plan_changes(cat: catmod.Catalogue) -> list[tuple]:
    """(kind, key, cycle, name, amount) for every item that has no Paddle price at today's amount."""
    return [it for it in cat.items() if not cat.price_id(it[0], it[1], it[2])]


def stale_prices(cat: catmod.Catalogue) -> list[str]:
    """Active Paddle prices that are no longer the price on sale."""
    selling = {cat.price_id(k, key, c) for k, key, c, _, _ in cat.items()}
    return [pid for pid, p in cat.prices.items() if p.get("active", True) and pid not in selling]


def adopt_existing(cat: catmod.Catalogue, in_paddle: list[dict]) -> tuple[list[str], list[dict]]:
    """Record prices Paddle already has for a catalogue item at today's amount instead of making them again.
    Returns (adopted price ids, active prices that match nothing in the catalogue, which are left alone)."""
    wanted = {(k, key, c): amount * 100 for k, key, c, _, amount in cat.items()}
    adopted, other = [], []
    for p in in_paddle:
        if p["id"] in cat.prices:
            continue
        cd = p.get("custom_data") or {}
        ident = (cd.get("kind"), cd.get("key"), cd.get("cycle") or "")
        unit = p.get("unit_price") or {}
        if (ident in wanted and str(unit.get("amount")) == str(wanted[ident])
                and unit.get("currency_code") == cat.currency and not cat.price_id(*ident)):
            kind, key, cycle = ident
            cat.prices[p["id"]] = {"kind": kind, "key": key, "cycle": cycle, "amount": wanted[ident],
                                   "currency": cat.currency, "active": True}
            cat.products.setdefault(product_key(kind, key), p["product_id"])
            adopted.append(p["id"])
        else:
            other.append(p)
    return adopted, other


def print_mapping(s: config.Settings, cat: catmod.Catalogue) -> None:
    """Sandbox and live price ids side by side, matched by plan and billing period."""
    if s.paddle_env == "production":
        sandbox = catmod.load(s.catalogue_file, s.data_dir / "paddle-prices.sandbox.json")
        live = cat
    else:
        sandbox = cat
        live = catmod.load(s.catalogue_file, s.data_dir / "paddle-prices.production.json")
    if not (sandbox.prices and live.prices):
        return
    print("\nSandbox → live price ids:")
    for kind, key, cycle, name, _ in cat.items():
        print(f"  {name:<28} {cycle or 'one-off':<8} {sandbox.price_id(kind, key, cycle) or '-':<32} → "
              f"{live.price_id(kind, key, cycle) or 'not made yet'}")


def main(argv=None, settings: config.Settings | None = None, transport=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="create the missing products and prices")
    ap.add_argument("--archive-old", action="store_true", help="archive prices that are no longer on sale")
    a = ap.parse_args(argv)

    s = settings or config.load()
    s.dev = True  # this tool doesn't need SHOP_SECRET
    s.check()
    if not s.paddle_api_key:
        print("PADDLE_API_KEY isn't set. Make an API key in Paddle → Developer tools → Authentication.")
        return 2
    cat = catmod.load(s.catalogue_file, s.prices_file)
    print(f"Paddle {s.paddle_env} ({s.paddle_base}); prices file {s.prices_file}")

    paddle = Paddle(s, transport=transport)
    adopted, other = adopt_existing(cat, paddle.list_prices())
    for pid in adopted:
        p = cat.prices[pid]
        print(f"  in Paddle  {pid}  {p['kind']}:{p['key']} {p['cycle'] or 'one-off'} (recorded, not made again)")
    for p in other:
        print(f"  left alone {p['id']}  {p.get('description', '')} (in Paddle, not a catalogue price)")
    if adopted and a.apply:
        cat.save_prices()

    todo = plan_changes(cat)
    old = stale_prices(cat)
    for kind, key, cycle, name, amount in todo:
        print(f"  new price  {name:<28} {catmod.money(amount, cat.currency):>8} {('per ' + cycle) if cycle else 'one-off'}")
    if a.archive_old:
        for pid in old:
            p = cat.prices[pid]
            print(f"  archive    {pid}  {p['kind']}:{p['key']} {p.get('cycle', '')} {p['amount'] / 100:.2f}")
    if not todo and not (a.archive_old and old):
        print("Paddle already matches catalogue.toml. Nothing to do.")
        print_mapping(s, cat)
        return 0
    if not a.apply:
        print("\nNothing changed. Run again with --apply to create these.")
        return 0

    for kind, key, cycle, name, amount in todo:
        pk = product_key(kind, key)
        if pk not in cat.products:
            cat.products[pk] = paddle.create_product(name, PRODUCT_TEXT[kind].format(name=name))
            cat.save_prices()
        label = f"{name} ({'monthly' if cycle == 'month' else 'yearly' if cycle == 'year' else 'one-off'})"
        info = {"kind": kind, "key": key, "cycle": cycle}
        pid = paddle.create_price(cat.products[pk], label, amount * 100, cat.currency, cycle, custom_data=info)
        cat.prices[pid] = {**info, "amount": amount * 100, "currency": cat.currency, "active": True}
        cat.save_prices()  # after each one, so a failure halfway loses nothing
        print(f"  created {pid} for {label}")
    if a.archive_old:
        for pid in old:
            paddle.archive_price(pid)
            cat.prices[pid]["active"] = False
            cat.save_prices()
            print(f"  archived {pid}")
    print(f"\nSaved {s.prices_file}. The shop picks it up when Admin → Plans is opened, or on restart.")
    print_mapping(s, cat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
