"""Paddle webhooks: signatures, duplicates and the order events arrive in."""
from __future__ import annotations

import json
import time

from atshop import paddle

from conftest import WEBHOOK_SECRET, Harness, sub_data, txn_data


def test_signature_checks():
    body = b'{"event_id":"evt_1"}'
    now = int(time.time())
    good = paddle.sign(body, "secret", now)
    assert paddle.verify_signature(body, good, "secret")
    assert not paddle.verify_signature(body + b" ", good, "secret")  # body changed
    assert not paddle.verify_signature(body, good, "other")  # wrong secret
    assert not paddle.verify_signature(body, paddle.sign(body, "secret", now - 3600), "secret")  # replayed later
    assert not paddle.verify_signature(body, "", "secret")
    assert not paddle.verify_signature(body, good, "")  # no secret set: nothing is trusted
    assert not paddle.verify_signature(body, "ts=abc;h1=00", "secret")
    # During a secret rotation Paddle sends two h1 values; either may match.
    rotated = f"ts={now};h1=deadbeef;h1=" + good.split("h1=")[1]
    assert paddle.verify_signature(body, rotated, "secret")


def test_unsigned_or_stale_webhooks_are_refused(h):
    ev = h.event("transaction.completed", txn_data("txn_x", "ctm_x", []))
    assert h.send(ev, secret="wrong").status_code == 401
    assert h.send(ev, ts=int(time.time()) - 600).status_code == 401
    c = h.client()
    assert c.post("/webhooks/paddle", content=b"{}").status_code == 401
    assert h.shop.db.one("SELECT COUNT(*) n FROM webhook_events")["n"] == 0


def test_webhooks_only_from_paddles_published_addresses(tmp_path, vendor):
    h = Harness(tmp_path, vendor, paddle_webhook_ip_check=True)
    c = h.client()

    def send_from(addr):
        ev = h.event("customer.updated", {"id": "ctm_nobody", "email": "x@example.com"})
        raw = json.dumps(ev).encode()
        return c.post("/webhooks/paddle", content=raw, headers={
            "Paddle-Signature": paddle.sign(raw, WEBHOOK_SECRET), "X-Forwarded-For": addr})

    h.paddle.ips = None  # Paddle's /ips can't be read yet: hold the event so Paddle retries
    assert send_from("34.237.3.244").status_code == 503
    h.paddle.ips = ["34.237.3.244/32", "52.11.166.252/32"]
    assert send_from("34.237.3.244").status_code == 200
    assert send_from("203.0.113.9").status_code == 403  # correctly signed, but not from Paddle
    assert send_from("not-an-ip").status_code == 403
    h.paddle.ips = None  # a later outage keeps the list already read
    assert send_from("52.11.166.252").status_code == 200
    assert sum(1 for m, p, _ in h.paddle.calls if p == "/ips") == 2


def _customer(h, email="buyer@example.com"):
    c = h.sign_in(email)
    acc = h.shop.db.one("SELECT * FROM accounts WHERE email=?", email)
    h.shop.db.run("UPDATE accounts SET paddle_customer_id='ctm_buyer' WHERE id=?", acc["id"])
    return c, acc


def test_duplicate_delivery_issues_one_key(h):
    _, acc = _customer(h)
    venue = h.price[("plan", "venue", "year")]
    ev = h.event("transaction.completed", txn_data("txn_1", "ctm_buyer", [venue], sub_id="sub_1"))
    r1, r2 = h.send(ev), h.send(ev)
    assert r1.json()["status"] == "processed"
    assert r2.json()["status"] == "duplicate"
    assert h.shop.db.one("SELECT COUNT(*) n FROM licences WHERE account_id=?", acc["id"])["n"] == 1
    # The same payment again under a new event id (Paddle resends) still makes no second key.
    again = h.event("transaction.completed", txn_data("txn_1", "ctm_buyer", [venue], sub_id="sub_1"))
    assert h.send(again).json()["status"] == "processed"
    assert h.shop.db.one("SELECT COUNT(*) n FROM licences WHERE account_id=?", acc["id"])["n"] == 1


def test_older_subscription_event_is_skipped(h):
    _, acc = _customer(h)
    small, venue = h.price[("plan", "small", "year")], h.price[("plan", "venue", "year")]
    newer = h.event("subscription.updated", sub_data("sub_1", "ctm_buyer", [venue]), occurred_at="2026-10-15T13:00:00Z")
    older = h.event("subscription.created", sub_data("sub_1", "ctm_buyer", [small]), occurred_at="2026-10-15T12:00:00Z")
    h.send(newer)
    h.send(older)
    assert h.shop.db.one("SELECT plan FROM subscriptions WHERE id='sub_1'")["plan"] == "venue"
    ev = h.shop.db.one("SELECT note FROM webhook_events WHERE event_id=?", older["event_id"])
    assert "older" in ev["note"]


def test_unknown_price_issues_nothing_and_flags_it(h):
    _, acc = _customer(h)
    h.send(h.event("subscription.created", sub_data("sub_9", "ctm_buyer", ["pri_someone_else"])))
    h.send(h.event("transaction.completed", txn_data("txn_9", "ctm_buyer", ["pri_someone_else"], sub_id="sub_9")))
    sub = h.shop.db.one("SELECT * FROM subscriptions WHERE id='sub_9'")
    assert "Unknown Paddle price" in sub["problem"]
    assert h.shop.db.one("SELECT COUNT(*) n FROM licences")["n"] == 0
    assert h.shop.db.one("SELECT COUNT(*) n FROM emails WHERE to_addr='boss@example.com'")["n"] >= 1


def test_payment_from_unknown_customer_tells_admin(h):
    venue = h.price[("plan", "venue", "year")]
    r = h.send(h.event("transaction.completed", txn_data("txn_7", "ctm_nobody", [venue], sub_id="sub_7")))
    assert r.status_code == 200
    assert h.shop.db.one("SELECT COUNT(*) n FROM licences")["n"] == 0
    assert "Payment with no account" in h.shop.db.one(
        "SELECT subject FROM emails WHERE to_addr='boss@example.com' ORDER BY id DESC")["subject"]


def test_a_failing_event_is_recorded_and_retried(h, monkeypatch):
    _, acc = _customer(h)
    venue = h.price[("plan", "venue", "year")]
    ev = h.event("transaction.completed", txn_data("txn_1", "ctm_buyer", [venue], sub_id="sub_1"))
    from atshop import billing
    real = billing.on_transaction_completed
    monkeypatch.setattr(billing, "on_transaction_completed", lambda ctx, e: 1 / 0)
    r = h.send(ev)
    assert r.status_code == 500  # Paddle retries a non-2xx
    assert h.shop.db.one("SELECT status FROM webhook_events WHERE event_id=?", ev["event_id"])["status"] == "failed"
    assert h.shop.db.one("SELECT COUNT(*) n FROM licences")["n"] == 0  # nothing half-done
    monkeypatch.setattr(billing, "on_transaction_completed", real)
    assert h.send(ev).json()["status"] == "processed"
    assert h.shop.db.one("SELECT COUNT(*) n FROM licences")["n"] == 1


def test_secret_is_needed(h):
    h.s.paddle_webhook_secret = ""
    ev = h.event("transaction.completed", txn_data("txn_x", "ctm_x", []))
    assert h.send(ev, secret="").status_code == 401
    h.s.paddle_webhook_secret = WEBHOOK_SECRET
