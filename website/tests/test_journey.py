"""A customer from sign-up to a working licence key, then renewal, plan changes, payment trouble and cancelling.
Keys are checked with the AT SUITE server's own licence code, trusting only the throwaway test key."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

from atshop.licensing import period_expiry

from conftest import sub_data, txn_data

SERVER = Path(__file__).resolve().parents[2] / "server"


@pytest.fixture
def server_licence(vendor, monkeypatch):
    """server/atsuit/licence.py, trusting the throwaway vendor key instead of the real one."""
    monkeypatch.syspath_prepend(str(SERVER))
    from atsuit import licence
    monkeypatch.setattr(licence, "_vendor_raw", lambda: (vendor["pub"], "test"))
    return licence


def buy(h, c, plan="venue", cycle="year", hosting=False, slug=""):
    r = c.get(f"/buy?plan={plan}&cycle={cycle}&hosting={int(hosting)}")
    assert r.status_code == 200
    csrf = h.csrf(c, f"/buy?plan={plan}&cycle={cycle}")
    form = {"csrf": csrf, "kind": "subscription", "plan": plan, "cycle": cycle}
    if hosting:
        form.update(hosting="1", venue_slug=slug, venue_name="Grand Hall")
    r = c.post("/buy", data=form)
    assert r.status_code == 200, r.text
    assert 'id="checkout"' in r.text and "cdn.paddle.com" in r.text
    order = h.shop.db.one("SELECT * FROM orders ORDER BY id DESC LIMIT 1")
    return order


def pay(h, order, sub_id, prices, start="2026-10-15T12:00:00Z", end="2027-10-15T12:00:00Z"):
    """What Paddle sends after a successful checkout: the subscription, then the paid transaction."""
    acc = h.shop.db.one("SELECT * FROM accounts WHERE id=?", order["account_id"])
    cust = acc["paddle_customer_id"]
    cd = {"account_id": str(acc["id"]), "order_ref": order["ref"]}
    h.send(h.event("subscription.created", {**sub_data(sub_id, cust, prices, start=start, end=end), "custom_data": cd}))
    r = h.send(h.event("transaction.completed", txn_data(order["paddle_transaction_id"], cust, prices, sub_id=sub_id,
                                                         start=start, end=end, custom_data=cd)))
    assert r.status_code == 200, r.text


def test_full_purchase_gives_a_key_the_server_accepts(h, server_licence):
    c = h.sign_in("ops@grandhall.example", name="Ops Team", organisation="Grand Hall")
    order = buy(h, c)
    # The server, not the browser, chose the price and recorded who it's for.
    sent = h.paddle.transactions[order["paddle_transaction_id"]]
    assert [i["price_id"] for i in sent["items"]] == [h.price[("plan", "venue", "year")]]
    assert sent["custom_data"] == {"account_id": str(order["account_id"]), "order_ref": order["ref"]}

    assert c.get(f"/account/order/{order['ref']}").json() == {"status": "open", "licence": False}
    pay(h, order, "sub_1", [h.price[("plan", "venue", "year")]])
    assert c.get(f"/account/order/{order['ref']}").json() == {"status": "paid", "licence": True}

    lic = h.shop.db.one("SELECT * FROM licences WHERE account_id=?", order["account_id"])
    parsed = server_licence.parse(lic["key"])
    assert parsed.valid and parsed.state == "active"
    assert parsed.licensee == "Grand Hall"
    assert parsed.edition == "venue" and parsed.max_nodes == 30 and parsed.max_sites == 1
    assert parsed.modules == server_licence.ALL_MODULES
    assert parsed.expires == period_expiry("2027-10-15T12:00:00Z", 7)  # period end + 7 spare days
    # Shown in the portal, downloadable, and emailed.
    page = c.get("/account").text
    assert lic["serial"] in page and lic["key"] in page
    dl = c.get(f"/account/licence/{lic['serial']}.txt")
    assert dl.text.strip() == lic["key"] and "attachment" in dl.headers["content-disposition"]
    mail = h.shop.db.one("SELECT * FROM emails WHERE to_addr='ops@grandhall.example' AND kind!='login' ORDER BY id DESC")
    assert lic["key"] in mail["body"]
    # Another customer can't fetch it.
    other = h.sign_in("nosy@example.com")
    assert other.get(f"/account/licence/{lic['serial']}.txt").status_code == 404


def test_a_key_from_another_vendor_is_refused_by_the_server(server_licence):
    import json
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from atshop.licensing import b64
    raw = json.dumps({"licensee": "Fake", "edition": "venue", "max_nodes": 30, "expires": 0}).encode()
    assert not server_licence.parse(f"{b64(raw)}.{b64(Ed25519PrivateKey.generate().sign(raw))}").valid


def test_monthly_renewal_gives_a_new_key_and_replaces_the_old(h, server_licence):
    c = h.sign_in("ops@grandhall.example")
    month = h.price[("plan", "small", "month")]
    order = buy(h, c, "small", "month")
    pay(h, order, "sub_m", [month], end="2026-11-15T12:00:00Z")
    first = h.shop.db.one("SELECT * FROM licences WHERE subscription_id='sub_m'")
    assert first["expires"] == period_expiry("2026-11-15T12:00:00Z", 7)
    acc = h.shop.db.one("SELECT * FROM accounts WHERE id=?", order["account_id"])
    # Next month: Paddle renews; the subscription moves on, then the renewal payment completes.
    h.send(h.event("subscription.updated", sub_data("sub_m", acc["paddle_customer_id"], [month],
                                                    start="2026-11-15T12:00:00Z", end="2026-12-15T12:00:00Z")))
    h.send(h.event("transaction.completed", txn_data("txn_renew", acc["paddle_customer_id"], [month], sub_id="sub_m",
                                                     start="2026-11-15T12:00:00Z", end="2026-12-15T12:00:00Z",
                                                     origin="subscription_recurring")))
    keys = h.shop.db.all("SELECT * FROM licences WHERE subscription_id='sub_m' ORDER BY id")
    assert len(keys) == 2
    assert keys[0]["replaced_by"] == keys[1]["serial"]
    assert keys[1]["expires"] == period_expiry("2026-12-15T12:00:00Z", 7)
    assert server_licence.parse(keys[1]["key"]).max_nodes == 10
    subjects = [r["subject"] for r in h.shop.db.all("SELECT subject FROM emails WHERE to_addr='ops@grandhall.example'")]
    assert any(s.startswith("AT SUITE renewed") for s in subjects)
    # The refresh endpoint hands a server holding the old key the new one, and nothing for the newest.
    r = h.client().post("/api/v1/licence/refresh", json={"key": keys[0]["key"]}).json()
    assert r["key"] == keys[1]["key"] and r["subscription"] == "active"
    assert h.client().post("/api/v1/licence/refresh", json={"key": keys[1]["key"]}).json()["status"] == "current"
    assert h.client().post("/api/v1/licence/refresh", json={"key": "made.up"}).status_code == 400


def test_upgrade_and_downgrade(h, server_licence):
    c = h.sign_in("ops@grandhall.example")
    small, large = h.price[("plan", "small", "year")], h.price[("plan", "large", "year")]
    order = buy(h, c, "small", "year")
    pay(h, order, "sub_u", [small])
    acc = h.shop.db.one("SELECT * FROM accounts WHERE id=?", order["account_id"])

    r = c.post("/account/plan", data={"csrf": h.csrf(c, "/account/plan"), "plan": "large", "cycle": "year"})
    assert r.status_code == 200
    assert h.paddle.patches[-1] == {"id": "sub_u", "items": [{"price_id": large, "quantity": 1}],
                                    "proration_billing_mode": "prorated_immediately"}
    # Paddle confirms the change: a new key with the bigger laptop limit, same end date.
    h.send(h.event("subscription.updated", sub_data("sub_u", acc["paddle_customer_id"], [large])))
    cur = h.shop.db.one("SELECT * FROM licences WHERE subscription_id='sub_u' AND replaced_by IS NULL")
    p = server_licence.parse(cur["key"])
    assert p.max_nodes == 75 and p.edition == "large"
    assert p.expires == period_expiry("2027-10-15T12:00:00Z", 7)

    # Going down is credited at the next bill, not charged now.
    c.post("/account/plan", data={"csrf": h.csrf(c, "/account/plan"), "plan": "small", "cycle": "year"})
    assert h.paddle.patches[-1]["proration_billing_mode"] == "prorated_next_billing_period"


def test_payment_trouble_and_cancelling(h):
    c = h.sign_in("ops@grandhall.example")
    venue = h.price[("plan", "venue", "year")]
    order = buy(h, c)
    pay(h, order, "sub_c", [venue])
    acc = h.shop.db.one("SELECT * FROM accounts WHERE id=?", order["account_id"])
    cust = acc["paddle_customer_id"]
    key_before = h.shop.db.one("SELECT * FROM licences WHERE subscription_id='sub_c'")

    h.send(h.event("transaction.payment_failed", {"id": "txn_f", "customer_id": cust, "subscription_id": "sub_c"}))
    h.send(h.event("subscription.past_due", sub_data("sub_c", cust, [venue], status="past_due")))
    h.send(h.event("subscription.updated", sub_data("sub_c", cust, [venue], status="past_due")))  # again: no 2nd email
    mails = h.shop.db.all("SELECT subject FROM emails WHERE to_addr='ops@grandhall.example' AND kind!='login'")
    assert sum("payment" in m["subject"].lower() for m in mails) >= 1
    assert h.shop.db.one("SELECT status FROM subscriptions WHERE id='sub_c'")["status"] == "past_due"
    # The key they have keeps working: nothing is revoked or shortened for a late payment.
    assert h.shop.db.one("SELECT * FROM licences WHERE id=?", key_before["id"])["revoked_at"] is None

    sched = {"action": "cancel", "effective_at": "2027-10-15T12:00:00Z", "resume_at": None}
    h.send(h.event("subscription.updated", sub_data("sub_c", cust, [venue], scheduled_change=sched)))
    h.send(h.event("subscription.updated", sub_data("sub_c", cust, [venue], scheduled_change=sched)))
    h.send(h.event("subscription.canceled", sub_data("sub_c", cust, [venue], status="canceled")))
    subjects = [m["subject"] for m in h.shop.db.all("SELECT subject FROM emails WHERE to_addr='ops@grandhall.example'")]
    assert subjects.count("Your AT SUITE subscription will end") == 1
    assert subjects.count("Your AT SUITE subscription has ended") == 1
    assert h.shop.db.one("SELECT * FROM licences WHERE id=?", key_before["id"])["revoked_at"] is None

    # Card update and cancel go to Paddle's own portal.
    r = c.post("/account/billing/portal", data={"csrf": h.csrf(c, "/account/billing"), "go": "card"})
    assert r.status_code == 303 and r.headers["location"].startswith("https://sandbox-customer-portal.paddle.com/")


def test_cannot_buy_twice_or_with_a_bad_cycle(h):
    c = h.sign_in("ops@grandhall.example")
    order = buy(h, c)
    pay(h, order, "sub_1", [h.price[("plan", "venue", "year")]])
    r = c.post("/buy", data={"csrf": h.csrf(c, "/account"), "kind": "subscription", "plan": "large", "cycle": "year"})
    assert r.status_code == 400 and "already have a subscription" in r.text
    assert c.get("/buy?plan=venue&cycle=fortnight").status_code == 404
    assert c.get("/buy?plan=gold&cycle=year").status_code == 404


def test_event_pass(h, server_licence):
    c = h.sign_in("hire@example.com", organisation="Hire Co")
    start = date.today().isoformat()
    r = c.post("/buy", data={"csrf": h.csrf(c, "/buy/event"), "kind": "event", "event_start": start})
    assert r.status_code == 200
    order = h.shop.db.one("SELECT * FROM orders ORDER BY id DESC")
    acc = h.shop.db.one("SELECT * FROM accounts WHERE id=?", order["account_id"])
    ev = h.price[("pass", "event", "")]
    h.send(h.event("transaction.completed", txn_data(order["paddle_transaction_id"], acc["paddle_customer_id"], [ev],
                                                     custom_data={"account_id": str(acc["id"]), "order_ref": order["ref"]},
                                                     total="14900")))
    lic = h.shop.db.one("SELECT * FROM licences WHERE account_id=?", acc["id"])
    p = server_licence.parse(lic["key"])
    assert p.valid and p.edition == "event" and p.max_nodes == 10
    from atshop.licensing import days_expiry
    assert p.expires == days_expiry(date.today(), 7)
    # Too far ahead is refused before any payment.
    r = c.post("/buy", data={"csrf": h.csrf(c, "/buy/event"), "kind": "event", "event_start": "2099-01-01"})
    assert r.status_code == 400


def test_trial_once_per_account(h, server_licence):
    c = h.sign_in("try@example.com")
    assert c.post("/account/trial", data={"csrf": h.csrf(c)}).status_code == 303
    lic = h.shop.db.one("SELECT * FROM licences WHERE source='trial'")
    p = server_licence.parse(lic["key"])
    assert p.valid and p.edition == "trial" and p.max_nodes == 10
    assert c.post("/account/trial", data={"csrf": h.csrf(c)}).status_code == 400


def test_checkout_waits_for_prices(h):
    h.shop.cat.prices.clear()
    c = h.sign_in("ops@grandhall.example")
    r = c.post("/buy", data={"csrf": h.csrf(c), "kind": "subscription", "plan": "venue", "cycle": "year"})
    assert r.status_code == 400 and "switched on yet" in r.text
    assert not h.paddle.transactions


def test_paddle_errors_are_shown_kindly(h):
    c = h.sign_in("ops@grandhall.example")
    h.paddle.fail_next = (500, "internal_error")
    r = c.post("/buy", data={"csrf": h.csrf(c), "kind": "subscription", "plan": "venue", "cycle": "year"})
    assert r.status_code == 400 and "Our payment provider said" in r.text
    assert h.shop.db.one("SELECT COUNT(*) n FROM orders")["n"] == 0


def test_existing_paddle_customer_is_reused(h):
    h.paddle.customers["ops@grandhall.example"] = "ctm_existing"
    c = h.sign_in("ops@grandhall.example")
    buy(h, c)
    assert h.shop.db.one("SELECT paddle_customer_id FROM accounts")["paddle_customer_id"] == "ctm_existing"


def test_invoice_only_for_own_transactions(h):
    c = h.sign_in("ops@grandhall.example")
    order = buy(h, c)
    pay(h, order, "sub_1", [h.price[("plan", "venue", "year")]])
    r = c.get(f"/account/invoice/{order['paddle_transaction_id']}")
    assert r.status_code == 303 and r.headers["location"].startswith("https://sandbox-invoices.paddle.com/")
    other = h.sign_in("nosy@example.com")
    assert other.get(f"/account/invoice/{order['paddle_transaction_id']}").status_code == 404
