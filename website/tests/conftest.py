"""Test set-up: a throwaway signing key (never the real one), a fake Paddle and a fake hosting server."""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from atshop import config, paddle  # noqa: E402
from atshop.app import create_app  # noqa: E402
from atshop.licensing import b64  # noqa: E402

WEBHOOK_SECRET = "pdl_ntfset_test_" + "x" * 32
ADMIN = "boss@example.com"


@pytest.fixture
def vendor(tmp_path):
    """A throwaway vendor key pair, written the way the real one is kept (mode 600)."""
    priv = Ed25519PrivateKey.generate()
    f = tmp_path / "vendor-test.pem"
    f.write_bytes(priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    os.chmod(f, 0o600)
    pub = b64(priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    return {"file": f, "pub": pub}


class FakePaddle:
    """Enough of Paddle Billing's API for the shop, kept in memory."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.customers: dict[str, str] = {}
        self.transactions: dict[str, dict] = {}
        self.products: dict[str, dict] = {}
        self.prices: dict[str, dict] = {}
        self.patches: list[dict] = []
        self.fail_next: tuple[int, str] | None = None
        self.ips: list[str] | None = ["34.237.3.244/32", "52.11.166.252/32"]  # None: /ips is down

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        path, m = request.url.path, request.method
        self.calls.append((m, path, body))
        assert request.headers["authorization"] == "Bearer test_api_key"
        assert request.headers["paddle-version"] == "1"
        if self.fail_next:
            status, code = self.fail_next
            self.fail_next = None
            return httpx.Response(status, json={"error": {"code": code, "detail": "nope"}})
        if m == "POST" and path == "/customers":
            if body["email"] in self.customers:
                return httpx.Response(409, json={"error": {"code": "customer_already_exists", "detail": "exists"}})
            cid = "ctm_" + secrets.token_hex(8)
            self.customers[body["email"]] = cid
            return httpx.Response(201, json={"data": {"id": cid}})
        if m == "GET" and path == "/customers":
            e = request.url.params["email"]
            return httpx.Response(200, json={"data": [{"id": self.customers[e]}] if e in self.customers else []})
        if m == "POST" and path == "/transactions":
            tid = "txn_" + secrets.token_hex(8)
            self.transactions[tid] = body
            return httpx.Response(201, json={"data": {"id": tid, "status": "ready"}})
        if m == "GET" and path == "/transactions":
            return httpx.Response(200, json={"data": []})
        if m == "GET" and re.fullmatch(r"/transactions/txn_\w+/invoice", path):
            return httpx.Response(200, json={"data": {"url": "https://sandbox-invoices.paddle.com/inv.pdf"}})
        if m == "PATCH" and re.fullmatch(r"/subscriptions/sub_\w+", path):
            self.patches.append({"id": path.rsplit("/", 1)[1], **body})
            return httpx.Response(200, json={"data": {"id": path.rsplit("/", 1)[1]}})
        if m == "POST" and re.fullmatch(r"/customers/ctm_\w+/portal-sessions", path):
            return httpx.Response(201, json={"data": {"urls": {
                "general": {"overview": "https://sandbox-customer-portal.paddle.com/overview"},
                "subscriptions": [{"id": s, "cancel_subscription": f"https://sandbox-customer-portal.paddle.com/cancel/{s}",
                                   "update_subscription_payment_method": f"https://sandbox-customer-portal.paddle.com/card/{s}"}
                                  for s in body["subscription_ids"]]}}})
        if m == "POST" and path == "/products":
            pid = "pro_" + secrets.token_hex(8)
            self.products[pid] = body
            return httpx.Response(201, json={"data": {"id": pid}})
        if m == "POST" and path == "/prices":
            pid = "pri_" + secrets.token_hex(8)
            self.prices[pid] = body
            return httpx.Response(201, json={"data": {"id": pid}})
        if m == "GET" and path == "/prices":
            return httpx.Response(200, json={"data": [
                {"id": pid, "product_id": b["product_id"], "description": b["description"], "status": "active",
                 "unit_price": b["unit_price"], "custom_data": b.get("custom_data")}
                for pid, b in self.prices.items() if b.get("status", "active") == "active"],
                "meta": {"pagination": {"has_more": False, "next": None}}})
        if m == "GET" and path == "/ips":
            if self.ips is None:
                return httpx.Response(502, text="bad gateway")
            return httpx.Response(200, json={"data": {"ipv4_cidrs": self.ips}})
        if m == "PATCH" and re.fullmatch(r"/prices/pri_\w+", path):
            self.prices[path.rsplit("/", 1)[1]]["status"] = body["status"]
            return httpx.Response(200, json={"data": {}})
        return httpx.Response(404, json={"error": {"code": "not_found", "detail": f"{m} {path}"}})


class FakeHosting:
    """Stands in for `ssh host atcloud ...`: records the commands and answers like atcloud."""

    def __init__(self):
        self.runs: list[tuple[list[str], str | None]] = []
        self.fail = False

    def __call__(self, argv, stdin):
        self.runs.append((argv, stdin))
        if self.fail:
            return 1, "atcloud: something broke"
        if argv[1] == "new":
            return 0, f"Venue ready.\nOpen https://{argv[2]}.venues.example/setup#code=ABC123 to finish set-up.\n"
        return 0, "ok"


def make_settings(tmp_path, vendor, **over) -> config.Settings:
    s = config.Settings(
        site_url="http://testserver", data_dir=tmp_path / "data", secret="s" * 40, dev=False,
        paddle_env="sandbox", paddle_api_key="test_api_key", paddle_client_token="test_client_token",
        paddle_webhook_secret=WEBHOOK_SECRET, signing_key_file=str(vendor["file"]),
        admin_emails={ADMIN}, hosting_domain="venues.example",
        paddle_webhook_ip_check=False)  # TestClient isn't one of Paddle's addresses; test_webhooks turns it on
    for k, v in over.items():
        setattr(s, k, v)
    return s


def seed_prices(s: config.Settings) -> dict:
    """Paddle price ids for every catalogue item, as tools/paddle_setup.py would save them."""
    from atshop import catalogue
    cat = catalogue.load(s.catalogue_file, s.prices_file)
    ids = {}
    for kind, key, cycle, _, amount in cat.items():
        pid = f"pri_{kind}_{key}_{cycle or 'once'}"
        cat.prices[pid] = {"kind": kind, "key": key, "cycle": cycle, "amount": amount * 100, "currency": "GBP",
                           "active": True}
        ids[(kind, key, cycle)] = pid
    cat.save_prices()
    return ids


class Harness:
    def __init__(self, tmp_path, vendor, **over):
        self.paddle = FakePaddle()
        self.hosting = FakeHosting()
        self.health = httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True, "version": "1.8.0"}))
        self.s = make_settings(tmp_path, vendor, **over)
        self.s.data_dir.mkdir(parents=True, exist_ok=True)
        self.price = seed_prices(self.s)
        self.app = create_app(self.s, paddle_transport=httpx.MockTransport(self.paddle),
                              hosting_runner=self.hosting, health_transport=self.health, background=False)
        self.shop = self.app.state.shop
        self.vendor = vendor
        self.n = 0

    def client(self) -> TestClient:
        return TestClient(self.app, follow_redirects=False)

    # -- sign-in through the emailed link, like a customer would --
    def last_link(self, email: str) -> str:
        row = self.shop.db.one("SELECT body FROM emails WHERE to_addr=? AND kind='login' ORDER BY id DESC LIMIT 1", email)
        m = re.search(r"http://testserver(/login/confirm\S*#token=[\w-]+)", row["body"])
        return m.group(1)

    def sign_in(self, email: str, name: str = "Sam Tech", organisation: str = "Grand Hall") -> TestClient:
        c = self.client()
        exists = self.shop.db.one("SELECT 1 FROM accounts WHERE email=?", email)
        if exists:
            r = c.post("/login", data={"email": email})
        else:
            r = c.post("/register", data={"email": email, "name": name, "organisation": organisation, "terms": "1"})
        assert r.status_code == 200, r.text
        link = self.last_link(email)
        token = re.search(r"token=([\w-]+)", link).group(1)
        r = c.post("/login/confirm", data={"token": token, "next": "/account"})
        assert r.status_code == 303, r.text
        return c

    def csrf(self, c: TestClient, page: str = "/account") -> str:
        r = c.get(page)
        m = re.search(r'name="csrf" value="([^"]+)"', r.text)
        assert m, f"no csrf on {page}"
        return m.group(1)

    # -- Paddle webhooks, signed with the test secret --
    def event(self, etype: str, data: dict, *, event_id: str | None = None, occurred_at: str | None = None) -> dict:
        self.n += 1
        return {"event_id": event_id or f"evt_{self.n:04d}_{secrets.token_hex(4)}", "event_type": etype,
                "occurred_at": occurred_at or f"2026-10-15T12:{self.n // 60:02d}:{self.n % 60:02d}.000000Z",
                "notification_id": f"ntf_{self.n}", "data": data}

    def send(self, event: dict, *, secret: str = WEBHOOK_SECRET, ts: int | None = None, client=None):
        raw = json.dumps(event).encode()
        c = client or self.client()
        return c.post("/webhooks/paddle", content=raw, headers={
            "Paddle-Signature": paddle.sign(raw, secret, ts or int(time.time())), "Content-Type": "application/json"})


@pytest.fixture
def h(tmp_path, vendor) -> Harness:
    return Harness(tmp_path, vendor)


def sub_data(sub_id, customer, prices, *, status="active", start="2026-10-15T12:00:00Z", end="2027-10-15T12:00:00Z",
             account_id=None, scheduled_change=None):
    return {"id": sub_id, "status": status, "customer_id": customer,
            "items": [{"status": "active", "price": {"id": p}, "quantity": 1} for p in prices],
            "current_billing_period": {"starts_at": start, "ends_at": end}, "next_billed_at": end,
            "scheduled_change": scheduled_change,
            "custom_data": {"account_id": str(account_id)} if account_id else None}


def txn_data(txn_id, customer, prices, *, sub_id=None, start="2026-10-15T12:00:00Z", end="2027-10-15T12:00:00Z",
             custom_data=None, origin="web", total="229000"):
    d = {"id": txn_id, "status": "completed", "customer_id": customer, "subscription_id": sub_id, "origin": origin,
         "currency_code": "GBP", "billed_at": start,
         "items": [{"price": {"id": p}, "quantity": 1} for p in prices],
         "details": {"totals": {"grand_total": total}}, "custom_data": custom_data or {}}
    if sub_id:
        d["billing_period"] = {"starts_at": start, "ends_at": end}
    return d
