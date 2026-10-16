"""Admin: who can get in, the second factor, hand-issued keys, hosting jobs and the audit log."""
from __future__ import annotations

import re
import time
from datetime import date, timedelta

from atshop import auth, hosting

from conftest import ADMIN, Harness, sub_data, txn_data


def admin_client(h):
    c = h.sign_in(ADMIN, name="Adam", organisation="AT SUITE")
    r = c.get("/admin")
    assert r.status_code == 303 and r.headers["location"] == "/admin/2fa"
    page = c.get("/admin/2fa").text
    secret = re.search(r'id="secret">([A-Z2-7]+)<', page).group(1)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    assert c.post("/admin/2fa", data={"csrf": csrf, "code": "000000" if auth.totp(secret) != "000000" else "111111"}
                  ).status_code == 400
    r = c.post("/admin/2fa", data={"csrf": csrf, "code": auth.totp(secret)})
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    return c, secret


def test_customers_cannot_open_admin(h):
    c = h.sign_in("customer@example.com")
    assert c.get("/admin").status_code == 403
    assert c.get("/admin/licences").status_code == 403
    assert c.post("/admin/licences/issue", data={"csrf": h.csrf(c)}).status_code == 403
    assert h.client().get("/admin").status_code == 303  # signed out: to the sign-in page


def test_admin_needs_the_second_factor_every_session(h):
    c, secret = admin_client(h)
    assert c.get("/admin").status_code == 200
    for page in ("/admin/customers", "/admin/licences", "/admin/plans", "/admin/hosting", "/admin/tickets",
                 "/admin/events", "/admin/emails", "/admin/audit"):
        assert c.get(page).status_code == 200, page
    # A fresh sign-in asks for the code again; the secret is kept, not shown again.
    c2 = h.sign_in(ADMIN)
    assert c2.get("/admin").headers["location"] == "/admin/2fa"
    assert secret not in c2.get("/admin/2fa").text
    stored = h.shop.db.one("SELECT totp_secret FROM accounts WHERE email=?", ADMIN)["totp_secret"]
    assert stored == secret


def test_totp_matches_rfc6238():
    # RFC 6238 test vector (SHA-1, 8 digits at T=59 → 94287082); our 6-digit code is its last 6 digits.
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32 of "12345678901234567890"
    assert auth.totp(secret, at=59) == "287082"
    assert auth.totp_ok(secret, auth.totp(secret, at=time.time() - 30))
    assert not auth.totp_ok(secret, "12345")


def test_manual_key_is_audited_and_needs_a_reason(h):
    c, _ = admin_client(h)
    csrf = h.csrf(c, "/admin/licences")
    until = (date.today() + timedelta(days=365)).isoformat()
    base = {"csrf": csrf, "email": "bigco@example.com", "licensee": "Big Co", "edition": "enterprise", "nodes": "200",
            "sites": "3", "until": until}
    assert c.post("/admin/licences/issue", data=base).status_code == 400  # no note
    r = c.post("/admin/licences/issue", data={**base, "note": "Enterprise deal, signed PO 1234", "send": "1"})
    assert r.status_code == 303
    lic = h.shop.db.one("SELECT * FROM licences WHERE source='manual'")
    assert lic["max_nodes"] == 200 and lic["max_sites"] == 3 and lic["licensee"] == "Big Co"
    assert h.shop.signer.verify(lic["key"])["edition"] == "enterprise"
    rows = h.shop.db.all("SELECT * FROM audit WHERE target=?", lic["serial"])
    assert {r["action"] for r in rows} >= {"licence.issue", "admin.licence_issue"}
    assert all(r["actor"] == ADMIN for r in rows)
    # Revoking stops refreshes and is logged too.
    c.post(f"/admin/licences/{lic['serial']}/revoke", data={"csrf": csrf, "reason": "test"})
    assert h.shop.db.one("SELECT revoked_at FROM licences WHERE id=?", lic["id"])["revoked_at"]
    assert h.client().post("/api/v1/licence/refresh", json={"key": lic["key"]}).json()["status"] == "unknown"


def test_forms_without_the_right_csrf_token_are_refused(h):
    c = h.sign_in("customer@example.com")
    assert c.post("/account/details", data={"name": "X"}).status_code == 403
    assert c.post("/account/details", data={"name": "X", "csrf": "wrong"}).status_code == 403
    assert c.post("/account/trial", data={}).status_code == 403
    assert c.post("/logout", data={}).status_code == 403
    assert c.post("/account/details", data={"name": "Sam", "csrf": h.csrf(c)}).status_code == 303


def test_sign_in_links_work_once_and_expire(h):
    c = h.client()
    c.post("/register", data={"email": "new@example.com", "name": "New", "terms": "1"})
    token = re.search(r"token=([\w-]+)", h.last_link("new@example.com")).group(1)
    link = h.last_link("new@example.com")
    assert link.split("#")[0] == "/login/confirm" and "?token" not in link  # the token stays out of the address
    page = c.get("/login/confirm")  # what an email scanner or the browser fetches: uses nothing up
    assert page.status_code == 200 and token not in page.text and 'id="link-token"' in page.text
    assert c.post("/login/confirm", data={"token": token}).status_code == 303
    assert h.client().post("/login/confirm", data={"token": token}).status_code == 400  # used
    c.post("/login", data={"email": "new@example.com"})
    token = re.search(r"token=([\w-]+)", h.last_link("new@example.com")).group(1)
    h.shop.db.run("UPDATE login_tokens SET expires_at=?", int(time.time()) - 1)
    assert h.client().post("/login/confirm", data={"token": token}).status_code == 400  # expired
    # Unknown addresses get the same answer and no email; links can't bounce to another site.
    r = h.client().post("/login", data={"email": "stranger@example.com"})
    assert r.status_code == 200 and "Check your email" in r.text
    assert not h.shop.db.one("SELECT 1 FROM emails WHERE to_addr='stranger@example.com'")
    from atshop.web import safe_next
    assert safe_next("//evil.example") == "/account" and safe_next("https://evil.example") == "/account"


def _hosted_customer(h):
    c = h.sign_in("ops@grandhall.example")
    csrf = h.csrf(c, "/buy?plan=venue&cycle=year&hosting=1")
    assert c.post("/buy", data={"csrf": csrf, "kind": "subscription", "plan": "venue", "cycle": "year", "hosting": "1",
                                "venue_slug": "admin", "venue_name": "Grand Hall"}).status_code == 400  # reserved
    r = c.post("/buy", data={"csrf": csrf, "kind": "subscription", "plan": "venue", "cycle": "year", "hosting": "1",
                             "venue_slug": "grand-hall", "venue_name": "Grand Hall"})
    assert r.status_code == 200
    order = h.shop.db.one("SELECT * FROM orders ORDER BY id DESC")
    prices = [h.price[("plan", "venue", "year")], h.price[("addon", "hosting", "year")]]
    assert [i["price_id"] for i in h.paddle.transactions[order["paddle_transaction_id"]]["items"]] == prices
    acc = h.shop.db.one("SELECT * FROM accounts WHERE id=?", order["account_id"])
    cd = {"account_id": str(acc["id"]), "order_ref": order["ref"]}
    h.send(h.event("subscription.created", {**sub_data("sub_h", acc["paddle_customer_id"], prices), "custom_data": cd}))
    h.send(h.event("transaction.completed", txn_data(order["paddle_transaction_id"], acc["paddle_customer_id"], prices,
                                                     sub_id="sub_h", custom_data=cd)))
    return c, acc, prices


def test_hosting_manual_mode(h):
    c, acc, prices = _hosted_customer(h)
    venue = h.shop.db.one("SELECT * FROM hosting WHERE slug='grand-hall'")
    assert venue["status"] == "pending"
    job = h.shop.db.one("SELECT * FROM hosting_jobs WHERE hosting_id=?", venue["id"])
    lic = h.shop.db.one("SELECT * FROM licences WHERE subscription_id='sub_h'")
    assert job["action"] == "new" and job["licence_serial"] == lic["serial"]
    assert hosting.run_pending(h.shop.db, h.s, h.hosting) == 0  # manual: the shop runs nothing itself
    admin, _ = admin_client(h)
    page = admin.get("/admin/hosting").text
    assert "atcloud new grand-hall --name &#39;Grand Hall&#39; --licence grand-hall-key.txt" in page \
        or "atcloud new grand-hall --name 'Grand Hall' --licence grand-hall-key.txt" in page
    assert admin.get(f"/admin/hosting/jobs/{job['id']}/key.txt").text.strip() == lic["key"]
    out = "Grand Hall is ready\n  https://grand-hall.venues.example/setup?code=XYZ\n"
    admin.post(f"/admin/hosting/jobs/{job['id']}/done", data={"csrf": h.csrf(admin, "/admin/hosting"), "output": out})
    venue = h.shop.db.one("SELECT * FROM hosting WHERE id=?", venue["id"])
    assert venue["status"] == "active" and venue["setup_link"].endswith("/setup?code=XYZ")
    # The customer sees their venue and whether it's up.
    page = c.get("/account").text
    assert "grand-hall.venues.example" in page


def test_hosting_ssh_mode_runs_atcloud_with_the_key_on_stdin(tmp_path, vendor):
    h = Harness(tmp_path, vendor, hosting_mode="ssh", hosting_ssh="atcloud@hosting.example")
    c, acc, prices = _hosted_customer(h)
    assert hosting.run_pending(h.shop.db, h.s, h.hosting) == 1
    argv, stdin = h.hosting.runs[0]
    lic = h.shop.db.one("SELECT * FROM licences WHERE subscription_id='sub_h'")
    assert argv == ["atcloud", "new", "grand-hall", "--name", "Grand Hall", "--licence", "/dev/stdin"]
    assert stdin == lic["key"]  # never on the command line
    venue = h.shop.db.one("SELECT * FROM hosting WHERE slug='grand-hall'")
    assert venue["status"] == "active" and "setup#code=ABC123" in venue["setup_link"]
    # A renewal sends the new key to the venue.
    h.send(h.event("transaction.completed", txn_data("txn_2", acc["paddle_customer_id"], prices, sub_id="sub_h",
                                                     start="2027-10-15T12:00:00Z", end="2028-10-15T12:00:00Z")))
    assert hosting.run_pending(h.shop.db, h.s, h.hosting) == 1
    argv, stdin = h.hosting.runs[1]
    new = h.shop.db.one("SELECT * FROM licences WHERE subscription_id='sub_h' AND replaced_by IS NULL")
    assert argv == ["atcloud", "licence", "grand-hall", "/dev/stdin"] and stdin == new["key"]
    # A failed run is kept for a retry, not lost.
    h.hosting.fail = True
    admin, _ = admin_client(h)
    admin.post(f"/admin/hosting/{venue['id']}/suspend", data={"csrf": h.csrf(admin, "/admin/hosting")})
    hosting.run_pending(h.shop.db, h.s, h.hosting)
    assert h.shop.db.one("SELECT status FROM hosting_jobs ORDER BY id DESC")["status"] == "failed"
    assert h.shop.db.one("SELECT status FROM hosting WHERE id=?", venue["id"])["status"] == "active"


def test_cancelled_hosted_venue_is_not_removed(h):
    c, acc, prices = _hosted_customer(h)
    h.send(h.event("subscription.canceled", sub_data("sub_h", acc["paddle_customer_id"], prices, status="canceled")))
    assert h.shop.db.one("SELECT status FROM hosting WHERE slug='grand-hall'")["status"] == "pending"
    assert not h.shop.db.one("SELECT 1 FROM hosting_jobs WHERE action IN ('suspend')")
    subjects = [r["subject"] for r in h.shop.db.all("SELECT subject FROM emails WHERE to_addr=?", ADMIN)]
    assert any("grand-hall" in s and "ended" in s for s in subjects)


def test_slug_rules():
    assert hosting.check_slug("Grand-Hall") == "grand-hall"
    for bad in ("www", "-x", "x-", "a--b", "has space", "x" * 40, "admin"):
        try:
            hosting.check_slug(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
