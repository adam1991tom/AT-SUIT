"""What Paddle's webhooks mean for accounts, subscriptions, licence keys and hosting.

Rules:
- An event is applied once: its event_id is stored with its effects in one transaction, so a
  retried or duplicated delivery does nothing the second time.
- Subscription events can arrive out of order; one older than the last one applied is skipped.
- What a customer bought comes from the Paddle price ids, mapped by the catalogue on the server.
  A price we don't recognise issues nothing and flags the subscription for the admin.
- A licence key is issued for each paid period (transaction.completed) and when the plan changes.
  It runs to the end of the period plus the spare days, as tools/licence.py does, and the app's own
  warnings, 14-day grace and show-safe lock take it from there. Keys are never cut short early.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from . import hosting as hostingmod
from . import mailer, notify
from .db import audit, now
from .licensing import days_expiry, period_expiry

log = logging.getLogger("atshop.billing")

HANDLED = {
    "subscription.created", "subscription.updated", "subscription.activated", "subscription.canceled",
    "subscription.past_due", "subscription.paused", "subscription.resumed", "subscription.trialing",
    "transaction.completed", "transaction.payment_failed", "transaction.past_due",
}


class Ctx:
    """What a handler needs: settings, catalogue, signer, and the open transaction."""

    def __init__(self, c, settings, cat, signer):
        self.c, self.s, self.cat, self.signer = c, settings, cat, signer


# --------------------------------------------------------------- entry point --
def handle(db, settings, cat, signer, event: dict) -> tuple[str, str]:
    """Apply one verified webhook event. Returns (status, note). Raises to make Paddle retry."""
    eid, etype = event.get("event_id") or "", event.get("event_type") or ""
    if not eid or not etype or not isinstance(event.get("data"), dict):
        return "ignored", "not a Paddle event"
    with db.tx() as c:
        seen = c.execute("SELECT status FROM webhook_events WHERE event_id=?", (eid,)).fetchone()
        if seen and seen["status"] != "failed":
            return "duplicate", f"already {seen['status']}"
        ctx = Ctx(c, settings, cat, signer)
        status, note = "ignored", "not an event the shop uses"
        if etype in HANDLED:
            status, note = "processed", ""
            if etype.startswith("subscription."):
                note = on_subscription(ctx, event)
            elif etype == "transaction.completed":
                note = on_transaction_completed(ctx, event)
            else:
                note = on_payment_failed(ctx, event)
        c.execute("INSERT OR REPLACE INTO webhook_events (event_id, event_type, occurred_at, received_at, status, note, payload)"
                  " VALUES (?,?,?,?,?,?,?)",
                  (eid, etype, event.get("occurred_at"), now(), status, note or "", json.dumps(event)))
    return status, note


# ------------------------------------------------------------------- helpers --
def _account(ctx, data: dict):
    c = ctx.c
    cd = data.get("custom_data") or {}
    acc = None
    if str(cd.get("account_id", "")).isdigit():
        acc = c.execute("SELECT * FROM accounts WHERE id=?", (int(cd["account_id"]),)).fetchone()
        # Only trust the account id if the Paddle customer is that account's (or the account has none yet).
        if acc and acc["paddle_customer_id"] and data.get("customer_id") and acc["paddle_customer_id"] != data["customer_id"]:
            acc = None
    if acc is None and data.get("customer_id"):
        acc = c.execute("SELECT * FROM accounts WHERE paddle_customer_id=?", (data["customer_id"],)).fetchone()
    if acc is None and data.get("subscription_id"):
        sub = c.execute("SELECT account_id FROM subscriptions WHERE id=?", (data["subscription_id"],)).fetchone()
        if sub and sub["account_id"]:
            acc = c.execute("SELECT * FROM accounts WHERE id=?", (sub["account_id"],)).fetchone()
    if acc is not None and not acc["paddle_customer_id"] and data.get("customer_id"):
        c.execute("UPDATE accounts SET paddle_customer_id=? WHERE id=?", (data["customer_id"], acc["id"]))
    return acc


def _price_ids(items: list[dict]) -> list[str]:
    out = []
    for it in items or []:
        if it.get("status") in ("inactive",):
            continue
        pid = (it.get("price") or {}).get("id") or it.get("price_id")
        if pid:
            out.append(pid)
    return out


def classify(cat, price_ids: list[str]) -> dict:
    """What a set of Paddle prices is: {"plan", "cycle", "hosting", "event", "unknown": [...]}."""
    out = {"plan": None, "cycle": None, "hosting": False, "event": False, "unknown": []}
    for pid in price_ids:
        p = cat.lookup(pid)
        if not p:
            out["unknown"].append(pid)
        elif p["kind"] == "plan":
            out["plan"], out["cycle"] = p["key"], p.get("cycle")
        elif p["kind"] == "addon" and p["key"] == "hosting":
            out["hosting"] = True
            out["cycle"] = out["cycle"] or p.get("cycle")
        elif p["kind"] == "pass":
            out["event"] = True
    return out


def licensee(acc) -> str:
    return acc["organisation"] or acc["name"] or acc["email"]


def fmt_day(ts: int) -> str:
    d = datetime.fromtimestamp(ts, timezone.utc)
    return f"{d.day} {d:%B %Y}"


def latest_licence(c, sub_id: str):
    return c.execute("SELECT * FROM licences WHERE subscription_id=? AND replaced_by IS NULL AND revoked_at IS NULL "
                     "ORDER BY issued DESC, id DESC LIMIT 1", (sub_id,)).fetchone()


def issue(ctx, acc, *, source: str, edition: str, max_nodes: int, expires: int, sub_id: str | None = None,
          txn_id: str | None = None, max_sites: int = 1, note: str = "", actor: str = "system") -> dict:
    """Sign a key, store it, and mark the key it replaces. Returns the licence row as a dict."""
    key, body = ctx.signer.sign(licensee=licensee(acc), edition=edition, max_nodes=max_nodes, expires=expires,
                                max_sites=max_sites)
    c = ctx.c
    prev = latest_licence(c, sub_id) if sub_id else None
    c.execute("INSERT INTO licences (serial, account_id, subscription_id, transaction_id, source, edition, licensee,"
              " max_nodes, max_sites, expires, issued, key, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (body["serial"], acc["id"], sub_id, txn_id, source, edition, body["licensee"], body["max_nodes"],
               body["max_sites"], body["expires"], body["issued"], key, note))
    if prev:
        c.execute("UPDATE licences SET replaced_by=? WHERE id=?", (body["serial"], prev["id"]))
    audit(c, actor, "licence.issue", body["serial"], {"account": acc["id"], "source": source, "edition": edition,
                                                     "nodes": max_nodes, "expires": expires, "subscription": sub_id})
    return {**body, "key": key, "source": source, "subscription_id": sub_id}


def _hosting_for(c, sub_id: str):
    return c.execute("SELECT * FROM hosting WHERE subscription_id=? AND status!='ended' ORDER BY id DESC LIMIT 1",
                     (sub_id,)).fetchone()


def _push_key_to_hosting(ctx, sub_id: str, serial: str) -> None:
    h = _hosting_for(ctx.c, sub_id)
    if h and h["status"] in ("active", "pending", "suspended"):
        # A venue still being made gets its key with the "new" job.
        pending_new = ctx.c.execute("SELECT 1 FROM hosting_jobs WHERE hosting_id=? AND action='new' AND status='todo'",
                                    (h["id"],)).fetchone()
        if pending_new:
            ctx.c.execute("UPDATE hosting_jobs SET licence_serial=? WHERE hosting_id=? AND action='new' AND status='todo'",
                          (serial, h["id"]))
        else:
            hostingmod.queue(ctx.c, h["id"], "licence", serial)


# --------------------------------------------------------------- subscription --
def on_subscription(ctx, event: dict) -> str:
    c, d = ctx.c, event["data"]
    sub_id = d.get("id")
    if not sub_id:
        return "no subscription id"
    occurred = event.get("occurred_at") or ""
    row = c.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if row and occurred and row["event_at"] and occurred < row["event_at"]:
        return f"older than the last update applied ({row['event_at']})"
    acc = _account(ctx, {**d, "subscription_id": sub_id})
    kind = classify(ctx.cat, _price_ids(d.get("items")))
    period = d.get("current_billing_period") or {}
    problem = f"Unknown Paddle price(s): {', '.join(kind['unknown'])}" if kind["unknown"] else None
    if not kind["plan"]:
        problem = problem or "No AT SUITE plan in this subscription"
    sched = d.get("scheduled_change")
    values = (acc["id"] if acc else None, d.get("customer_id"), d.get("status") or "active", kind["plan"],
              kind["cycle"], int(kind["hosting"]), json.dumps(_price_ids(d.get("items"))), period.get("starts_at"),
              period.get("ends_at"), d.get("next_billed_at"), json.dumps(sched) if sched else None, problem,
              occurred or (row["event_at"] if row else ""), now())
    if row:
        c.execute("UPDATE subscriptions SET account_id=COALESCE(?, account_id), paddle_customer_id=?, status=?, plan=?, cycle=?,"
                  " hosting=?, items=?, period_start=?, period_end=?, next_billed_at=?, scheduled_change=?, problem=?,"
                  " event_at=?, updated_at=? WHERE id=?", (*values, sub_id))
    else:
        c.execute("INSERT INTO subscriptions (account_id, paddle_customer_id, status, plan, cycle, hosting, items, period_start,"
                  " period_end, next_billed_at, scheduled_change, problem, event_at, updated_at, id, created_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (*values, sub_id, now()))
    if problem:
        notify.admins(ctx, f"Subscription needs a look: {sub_id}", f"{problem}\n\nAccount: {acc['email'] if acc else 'unknown'}")
        audit(c, "paddle", "subscription.problem", sub_id, {"problem": problem})
    if acc is None:
        return "subscription saved; no account matched yet"
    notes = []
    status = d.get("status")
    was = row["status"] if row else None
    # Plan changed while active: a new key for the rest of this period, with the new laptop limit.
    if status in ("active", "trialing") and kind["plan"] and period.get("ends_at"):
        plan = ctx.cat.plans[kind["plan"]]
        cur = latest_licence(c, sub_id)
        if cur and (cur["max_nodes"] != plan.laptops or cur["edition"] != plan.licence_plan):
            lic = issue(ctx, acc, source="subscription", edition=plan.licence_plan, max_nodes=plan.laptops,
                        expires=max(period_expiry(period["ends_at"], ctx.cat.spare_days), cur["expires"]),
                        sub_id=sub_id, note=f"plan changed to {plan.name}")
            _push_key_to_hosting(ctx, sub_id, lic["serial"])
            notify.key_email(ctx, acc, lic, f"Your AT SUITE plan is now {plan.name}", reason="plan_change")
            notes.append("plan change: new key")
    # Payment trouble, cancellation and endings: tell the customer once each.
    if status == "past_due" and was != "past_due":
        notify.payment_failed(ctx, acc, sub_id)
        notes.append("past due")
    if sched and sched.get("action") == "cancel" and not (row and row["scheduled_change"]):
        notify.once(ctx, acc, "cancel_scheduled", sub_id, "Your AT SUITE subscription will end",
                    notify.cancel_scheduled_body(ctx, acc, sub_id, sched.get("effective_at")))
        notes.append("cancel scheduled")
    if status == "canceled" and was != "canceled":
        lic = latest_licence(c, sub_id)
        notify.once(ctx, acc, "canceled", sub_id, "Your AT SUITE subscription has ended",
                    notify.canceled_body(ctx, acc, lic))
        h = _hosting_for(c, sub_id)
        if h:
            notify.admins(ctx, f"Hosted venue {h['slug']} subscription ended",
                          f"{acc['email']}'s subscription {sub_id} ended. The venue keeps working until its key's grace "
                          f"runs out. Suspend it from Admin → Hosting when you're ready; nothing is removed automatically.")
        notes.append("canceled")
    audit(c, "paddle", f"subscription.{status}", sub_id, {"plan": kind["plan"], "cycle": kind["cycle"],
                                                          "hosting": kind["hosting"]})
    return "; ".join(notes) or "saved"


# ---------------------------------------------------------------- transaction --
def on_transaction_completed(ctx, event: dict) -> str:
    c, d = ctx.c, event["data"]
    txn_id = d.get("id")
    acc = _account(ctx, d)
    totals = (d.get("details") or {}).get("totals") or {}
    period = d.get("billing_period") or {}
    c.execute("INSERT OR REPLACE INTO transactions (id, account_id, subscription_id, status, origin, total, currency,"
              " period_start, period_end, billed_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (txn_id, acc["id"] if acc else None, d.get("subscription_id"), d.get("status") or "completed",
               d.get("origin"), totals.get("grand_total") or totals.get("total"), d.get("currency_code"),
               period.get("starts_at"), period.get("ends_at"), d.get("billed_at"), now()))
    cd = d.get("custom_data") or {}
    order = None
    if cd.get("order_ref"):
        order = c.execute("SELECT * FROM orders WHERE ref=?", (str(cd["order_ref"]),)).fetchone()
    if order is None and txn_id:
        order = c.execute("SELECT * FROM orders WHERE paddle_transaction_id=?", (txn_id,)).fetchone()
    if order:
        c.execute("UPDATE orders SET status='paid' WHERE id=?", (order["id"],))
    if acc is None:
        notify.admins(ctx, f"Payment with no account: {txn_id}",
                      f"Paddle customer {d.get('customer_id')} paid ({txn_id}) but no account matches. "
                      f"Issue their key from Admin → Licences once you know who it is.")
        return "no account matched; admin told"
    kind = classify(ctx.cat, _price_ids(d.get("items")))
    sub_id = d.get("subscription_id")
    if kind["unknown"]:
        notify.admins(ctx, f"Payment for an unknown price: {txn_id}",
                      f"{acc['email']} paid for {', '.join(kind['unknown'])}, which isn't in the catalogue. No key was issued.")
    if sub_id:
        return _subscription_payment(ctx, acc, d, kind, order)
    if kind["event"]:
        start = date.today()
        if order and order["event_start"]:
            try:
                start = max(date.fromisoformat(order["event_start"]), date.today())
            except ValueError:
                pass
        ev = ctx.cat.event
        lic = issue(ctx, acc, source="event", edition=ev["licence_plan"], max_nodes=int(ev["laptops"]),
                    expires=days_expiry(start, int(ev["days"])), txn_id=txn_id, note=f"event from {start}")
        notify.key_email(ctx, acc, lic, "Your AT SUITE event pass", reason="event")
        return "event pass issued"
    return "nothing to issue"


def _subscription_payment(ctx, acc, d, kind, order) -> str:
    c, txn_id, sub_id = ctx.c, d.get("id"), d["subscription_id"]
    if d.get("origin") == "subscription_payment_method_change":
        return "card change, nothing to issue"
    sub = c.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    plan_key = kind["plan"] or (sub["plan"] if sub else None)
    period = d.get("billing_period") or {}
    if not plan_key or not period.get("ends_at"):
        notify.admins(ctx, f"Couldn't issue a key for {txn_id}",
                      f"{acc['email']} paid ({txn_id}, subscription {sub_id}) but the plan or period is missing.")
        return "no plan or period; admin told"
    if sub is None:
        # The subscription's own event hasn't arrived yet; start a record so the key and hosting have a home.
        c.execute("INSERT INTO subscriptions (id, account_id, paddle_customer_id, status, plan, cycle, hosting, items,"
                  " period_start, period_end, event_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sub_id, acc["id"], d.get("customer_id"), "active", plan_key, kind["cycle"], int(kind["hosting"]),
                   json.dumps(_price_ids(d.get("items"))), period.get("starts_at"), period.get("ends_at"), "", now(), now()))
    plan = ctx.cat.plans[plan_key]
    expires = period_expiry(period["ends_at"], ctx.cat.spare_days)
    cur = latest_licence(c, sub_id)
    first = cur is None and c.execute("SELECT 1 FROM licences WHERE subscription_id=?", (sub_id,)).fetchone() is None
    out = []
    if cur and cur["max_nodes"] == plan.laptops and cur["edition"] == plan.licence_plan and cur["expires"] >= expires:
        out.append("key already covers this period")
        lic = None
    else:
        lic = issue(ctx, acc, source="subscription", edition=plan.licence_plan, max_nodes=plan.laptops,
                    expires=max(expires, cur["expires"] if cur else 0), sub_id=sub_id, txn_id=txn_id)
        out.append("key issued")
    # Hosting: the first payment that includes it makes the venue; later keys go to the venue.
    wants_hosting = kind["hosting"] or (sub and sub["hosting"])
    h = _hosting_for(c, sub_id)
    if wants_hosting and h is None:
        slug = order["venue_slug"] if order and order["venue_slug"] else None
        if slug and not c.execute("SELECT 1 FROM hosting WHERE slug=?", (slug,)).fetchone():
            hid = c.execute("INSERT INTO hosting (account_id, subscription_id, slug, name, status, created_at)"
                            " VALUES (?,?,?,?,?,?)", (acc["id"], sub_id, slug, order["venue_name"] or licensee(acc),
                                                      "pending", now())).lastrowid
            hostingmod.queue(c, hid, "new", (lic or dict(cur))["serial"] if (lic or cur) else None)
            notify.admins(ctx, f"New hosted venue: {slug}",
                          f"{acc['email']} bought managed hosting. The venue job is in Admin → Hosting.")
            out.append("hosting queued")
        else:
            notify.admins(ctx, f"Hosting paid but no venue address: {sub_id}",
                          f"{acc['email']} pays for hosting but no free venue address was chosen. Set one in Admin → Hosting.")
            out.append("hosting needs an address")
    elif lic and h:
        _push_key_to_hosting(ctx, sub_id, lic["serial"])
    if lic:
        if first:
            notify.key_email(ctx, acc, lic, "Welcome to AT SUITE: your licence key", reason="welcome")
        else:
            notify.key_email(ctx, acc, lic, "AT SUITE renewed: your new licence key", reason="renewal")
    return "; ".join(out)


def on_payment_failed(ctx, event: dict) -> str:
    d = event["data"]
    acc = _account(ctx, d)
    if not acc:
        return "no account matched"
    ref = d.get("subscription_id") or d.get("id")
    notify.payment_failed(ctx, acc, ref)
    return "customer told"


__all__ = ["handle", "classify", "issue", "latest_licence", "mailer"]
