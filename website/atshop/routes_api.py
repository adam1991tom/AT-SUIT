"""Machine endpoints: Paddle's webhook, licence renewal for AT SUITE servers, health."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from . import auth, billing, mailer
from .app import VERSION
from .paddle import verify_signature
from .web import ip, shop

router = APIRouter()
log = logging.getLogger("atshop.api")
MAX_BODY = 512 * 1024


@router.post("/webhooks/paddle")
async def paddle_webhook(request: Request):
    """Every Paddle event comes here. Only signed, recent events are read; each is applied once."""
    sh = shop(request)
    if sh.s.paddle_webhook_ip_check:
        allowed = await run_in_threadpool(sh.paddle.is_webhook_ip, ip(request))
        if allowed is None:  # Paddle's address list couldn't be read: Paddle retries a 503 later
            log.warning("Paddle webhook held: couldn't read Paddle's address list")
            return JSONResponse({"error": "try again"}, 503)
        if not allowed:
            log.warning("Paddle webhook from %s, which isn't one of Paddle's addresses", ip(request))
            return JSONResponse({"error": "forbidden"}, 403)
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return JSONResponse({"error": "too large"}, 413)
    if not verify_signature(raw, request.headers.get("paddle-signature", ""), sh.s.paddle_webhook_secret):
        log.warning("Paddle webhook with a bad or old signature from %s", ip(request))
        return JSONResponse({"error": "bad signature"}, 401)
    try:
        event = json.loads(raw)
    except ValueError:
        return JSONResponse({"error": "not json"}, 400)
    try:
        status, note = billing.handle(sh.db, sh.s, sh.cat, sh.signer, event)
    except Exception as e:  # noqa: BLE001 - record it, then let Paddle retry
        log.exception("webhook %s failed", event.get("event_id"))
        sh.db.run("INSERT OR REPLACE INTO webhook_events (event_id, event_type, occurred_at, received_at, status, note,"
                  " payload) VALUES (?,?,?,strftime('%s','now'),'failed',?,?)",
                  event.get("event_id") or "?", event.get("event_type") or "?", event.get("occurred_at"),
                  f"{type(e).__name__}: {e}"[:500], raw.decode(errors="replace"))
        return JSONResponse({"error": "not processed; will retry"}, 500)
    try:
        mailer.flush(sh.db, sh.s)
    except Exception:  # noqa: BLE001 - the background loop retries
        log.exception("email after webhook")
    return {"ok": True, "status": status}


@router.post("/api/v1/licence/refresh")
async def licence_refresh(request: Request):
    """An AT SUITE server that has internet can swap its key for the newest one of the same subscription.
    It proves it holds the current key by sending it; it gets back only that subscription's newest key.
    For a future "renew automatically" setting in the app; the app doesn't call this yet."""
    sh = shop(request)
    if auth.limited(sh.db, f"refresh:{ip(request)}", 30, 3600):
        return JSONResponse({"error": "slow down"}, 429)
    try:
        key = str((await request.json()).get("key", ""))[:4096]
    except ValueError:
        return JSONResponse({"error": "send {\"key\": \"<current licence key>\"}"}, 400)
    body = sh.signer.verify(key)
    if not body:
        return JSONResponse({"error": "not a key we issued"}, 400)
    lic = sh.db.one("SELECT * FROM licences WHERE serial=?", body.get("serial", ""))
    if not lic or lic["revoked_at"]:
        return JSONResponse({"key": None, "status": "unknown"})
    newest = lic
    for _ in range(100):
        if not newest["replaced_by"]:
            break
        nxt = sh.db.one("SELECT * FROM licences WHERE serial=?", newest["replaced_by"])
        if not nxt or nxt["revoked_at"]:
            break
        newest = nxt
    sub = sh.db.one("SELECT status FROM subscriptions WHERE id=?", lic["subscription_id"]) if lic["subscription_id"] else None
    if newest["serial"] == lic["serial"]:
        return {"key": None, "status": "current", "expires": lic["expires"], "subscription": sub["status"] if sub else None}
    return {"key": newest["key"], "serial": newest["serial"], "expires": newest["expires"],
            "subscription": sub["status"] if sub else None}


@router.get("/api/health")
def health(request: Request):
    sh = shop(request)
    sh.db.one("SELECT 1")
    return {"ok": True, "version": VERSION, "billing": sh.s.paddle_env if sh.s.billing_ready else "off",
            "signing": sh.signer.ready}
