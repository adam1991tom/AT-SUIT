"""Paddle Billing: the few API calls the shop makes, and webhook signature checks.

Docs: https://developer.paddle.com/api-reference/overview and
https://developer.paddle.com/webhooks/signature-verification
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import threading
import time

import httpx

SIGNATURE_TOLERANCE_S = 300
WEBHOOK_IPS_TTL_S = 3600


class PaddleError(RuntimeError):
    def __init__(self, message: str, status: int = 0, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


def verify_signature(raw_body: bytes, header: str, secret: str, now: float | None = None,
                     tolerance: int = SIGNATURE_TOLERANCE_S) -> bool:
    """Paddle-Signature: ts=<unix>;h1=<hex hmac-sha256 of "<ts>:<body>">. Several h1 during a secret rotation.
    Old timestamps are refused so a captured request can't be replayed later."""
    if not secret or not header:
        return False
    ts, sigs = None, []
    for part in header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "ts":
            ts = v
        elif k == "h1":
            sigs.append(v)
    if not ts or not ts.isdigit() or not sigs:
        return False
    if abs((now or time.time()) - int(ts)) > tolerance:
        return False
    want = hmac.new(secret.encode(), ts.encode() + b":" + raw_body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(want, s) for s in sigs)


def sign(raw_body: bytes, secret: str, ts: int | None = None) -> str:
    """A Paddle-Signature header for this body (tests and the local webhook replayer use it)."""
    ts = int(ts or time.time())
    return f"ts={ts};h1=" + hmac.new(secret.encode(), f"{ts}:".encode() + raw_body, hashlib.sha256).hexdigest()


class Paddle:
    def __init__(self, settings, transport: httpx.BaseTransport | None = None):
        self.s = settings
        self._client = httpx.Client(
            base_url=settings.paddle_base, timeout=20, transport=transport,
            headers={"Authorization": f"Bearer {settings.paddle_api_key}", "Paddle-Version": "1",
                     "Content-Type": "application/json"})
        self._ips: list = []
        self._ips_at = 0.0
        self._ips_lock = threading.Lock()

    def _call(self, method: str, path: str, **kw) -> dict:
        if not self.s.paddle_api_key:
            raise PaddleError("Billing isn't set up yet (no PADDLE_API_KEY)")
        try:
            r = self._client.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise PaddleError(f"Couldn't reach Paddle: {e}") from e
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            err = data.get("error") or {}
            raise PaddleError(err.get("detail") or f"Paddle answered {r.status_code}", r.status_code, err.get("code", ""))
        return data

    # ------------------------------------------------------------ customers --
    def find_or_create_customer(self, email: str, name: str = "") -> str:
        try:
            return self._call("POST", "/customers", json={"email": email, **({"name": name} if name else {})})["data"]["id"]
        except PaddleError as e:
            if e.status != 409 and e.code != "customer_already_exists":
                raise
        found = self._call("GET", "/customers", params={"email": email})["data"]
        if not found:
            raise PaddleError("Paddle says the customer exists but can't find them")
        return found[0]["id"]

    def portal_session(self, customer_id: str, subscription_ids: list[str]) -> dict:
        """Links into Paddle's customer portal: update card, cancel, invoices."""
        return self._call("POST", f"/customers/{customer_id}/portal-sessions",
                          json={"subscription_ids": subscription_ids})["data"]["urls"]

    # --------------------------------------------------------- transactions --
    def create_transaction(self, customer_id: str, price_ids: list[str], custom_data: dict) -> dict:
        """A draft transaction the browser's checkout then completes. Prices and what the order is
        for are fixed here, on the server, not by anything the browser sends."""
        return self._call("POST", "/transactions", json={
            "customer_id": customer_id,
            "items": [{"price_id": p, "quantity": 1} for p in price_ids],
            "custom_data": custom_data,
            "collection_mode": "automatic",
        })["data"]

    def transactions(self, customer_id: str, limit: int = 30) -> list[dict]:
        return self._call("GET", "/transactions", params={
            "customer_id": customer_id, "status": "billed,paid,completed,past_due",
            "order_by": "billed_at[DESC]", "per_page": limit})["data"]

    def invoice_url(self, transaction_id: str) -> str:
        return self._call("GET", f"/transactions/{transaction_id}/invoice")["data"]["url"]

    # -------------------------------------------------------- subscriptions --
    def subscription(self, sub_id: str) -> dict:
        return self._call("GET", f"/subscriptions/{sub_id}")["data"]

    def change_items(self, sub_id: str, price_ids: list[str], proration: str) -> dict:
        """Change plan or add/remove hosting. proration: prorated_immediately | prorated_next_billing_period."""
        return self._call("PATCH", f"/subscriptions/{sub_id}", json={
            "items": [{"price_id": p, "quantity": 1} for p in price_ids],
            "proration_billing_mode": proration})["data"]

    def preview_change(self, sub_id: str, price_ids: list[str], proration: str) -> dict:
        return self._call("PATCH", f"/subscriptions/{sub_id}/preview", json={
            "items": [{"price_id": p, "quantity": 1} for p in price_ids],
            "proration_billing_mode": proration})["data"]

    # ------------------------------------------------------ catalogue setup --
    def create_product(self, name: str, description: str) -> str:
        return self._call("POST", "/products", json={"name": name, "description": description,
                                                     "tax_category": "standard"})["data"]["id"]

    def create_price(self, product_id: str, description: str, amount_minor: int, currency: str,
                     cycle: str = "", custom_data: dict | None = None) -> str:
        body = {"product_id": product_id, "description": description,
                "unit_price": {"amount": str(amount_minor), "currency_code": currency},
                # Prices are before VAT; Paddle adds it at checkout.
                "tax_mode": "external", "custom_data": custom_data or {}}
        if cycle:
            body["billing_cycle"] = {"interval": cycle, "frequency": 1}
        return self._call("POST", "/prices", json=body)["data"]["id"]

    def archive_price(self, price_id: str) -> None:
        self._call("PATCH", f"/prices/{price_id}", json={"status": "archived"})

    def list_prices(self) -> list[dict]:
        """Every active price in the account (read only), following Paddle's pages."""
        out, params, path = [], {"status": "active", "per_page": 200}, "/prices"
        while path:
            page = self._call("GET", path, params=params)
            out += page["data"]
            nxt = (page.get("meta") or {}).get("pagination", {})
            path = nxt.get("next") if nxt.get("has_more") else None
            params = None  # the next link carries its own query
        return out

    # ------------------------------------------------------------- webhooks --
    def webhook_ips(self) -> list:
        """The addresses Paddle sends webhooks from, read from Paddle's own /ips (live or sandbox,
        to match PADDLE_ENV) and kept for an hour. If Paddle can't be reached the last list is kept;
        an empty list means we don't know yet, and the webhook asks Paddle to retry."""
        with self._ips_lock:
            if self._ips and time.time() - self._ips_at < WEBHOOK_IPS_TTL_S:
                return self._ips
            try:
                r = self._client.get("/ips")
                r.raise_for_status()
                nets = [ipaddress.ip_network(c) for c in r.json()["data"]["ipv4_cidrs"]]
                if nets:
                    self._ips, self._ips_at = nets, time.time()
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                pass
            return self._ips

    def is_webhook_ip(self, addr: str) -> bool | None:
        """True or False, or None when Paddle's list couldn't be read."""
        nets = self.webhook_ips()
        if not nets:
            return None
        try:
            a = ipaddress.ip_address(addr)
        except ValueError:
            return False
        return any(a in n for n in nets)
