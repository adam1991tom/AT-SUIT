"""The customer portal: licence keys, plan, billing, hosting, details and support."""
from __future__ import annotations

import json
import secrets
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import hosting as hostingmod
from . import mailer, notify
from .billing import Ctx, classify, issue, latest_licence
from .db import audit, now
from .licensing import days_expiry
from .paddle import PaddleError
from .web import form, ip, need_login, redirect, render, shop

router = APIRouter()


def _account(sh, sess):
    return sh.db.one("SELECT * FROM accounts WHERE id=?", sess["account_id"])


def _overview(sh, acc) -> dict:
    subs = sh.db.all("SELECT * FROM subscriptions WHERE account_id=? ORDER BY created_at DESC", acc["id"])
    lics = sh.db.all("SELECT * FROM licences WHERE account_id=? ORDER BY issued DESC, id DESC", acc["id"])
    current = [lc for lc in lics if not lc["replaced_by"] and not lc["revoked_at"]]
    venues = sh.db.all("SELECT * FROM hosting WHERE account_id=? ORDER BY id", acc["id"])
    return {"subs": subs, "licences": lics, "current": current, "venues": venues,
            "active_sub": next((s for s in subs if s["status"] in ("active", "trialing", "past_due", "paused")), None),
            "had_trial": any(lc["source"] == "trial" for lc in lics),
            "orders_open": sh.db.all("SELECT * FROM orders WHERE account_id=? AND status='open' AND created_at>? "
                                     "ORDER BY id DESC", acc["id"], now() - 3600)}


@router.get("/account")
def dashboard(request: Request):
    sess = need_login(request)
    sh = shop(request)
    acc = _account(sh, sess)
    ov = _overview(sh, acc)
    health = {v["slug"]: hostingmod.health(sh.s, v["slug"], sh.health_transport) for v in ov["venues"]
              if v["status"] == "active"}
    return render(request, "account/dashboard.html", {"title": "Your account", "acc": acc, **ov, "health": health,
                                                      "address": lambda slug: hostingmod.address(sh.s, slug)})


@router.get("/account/licence/{serial}.txt")
def licence_file(request: Request, serial: str):
    sess = need_login(request)
    lic = shop(request).db.one("SELECT * FROM licences WHERE serial=? AND account_id=?", serial, sess["account_id"])
    if not lic:
        raise HTTPException(404)
    return PlainTextResponse(lic["key"] + "\n", headers={
        "Content-Disposition": f'attachment; filename="AT-SUITE-licence-{lic["serial"]}.txt"'})


@router.post("/account/trial")
async def trial(request: Request):
    sess = need_login(request)
    await form(request)
    sh = shop(request)
    with sh.db.tx() as c:
        acc = c.execute("SELECT * FROM accounts WHERE id=?", (sess["account_id"],)).fetchone()
        if c.execute("SELECT 1 FROM licences WHERE account_id=?", (acc["id"],)).fetchone():
            raise HTTPException(400, "A free trial is for new accounts. Choose a plan, or contact us for more time.")
        t = sh.cat.trial
        lic = issue(Ctx(c, sh.s, sh.cat, sh.signer), acc, source="trial", edition=t["licence_plan"],
                    max_nodes=int(t["laptops"]), expires=days_expiry(date.today(), int(t["days"])),
                    actor=acc["email"], note="free trial")
        notify.key_email(Ctx(c, sh.s, sh.cat, sh.signer), acc, lic, "Your AT SUITE trial licence key", reason="trial")
    mailer.flush(sh.db, sh.s)
    return redirect("/account#licence")


# --------------------------------------------------------------------- buy --
def _buy_ctx(sh, plan: str, cycle: str, hosting: bool) -> dict:
    if plan not in sh.cat.plans or cycle not in ("month", "year"):
        raise HTTPException(404)
    p = sh.cat.plans[plan]
    hosting_amount = sh.cat.amount_of("addon", "hosting", cycle) if hosting else 0
    return {"plan": p, "cycle": cycle, "hosting": hosting, "amount": p.amount(cycle),
            "hosting_amount": hosting_amount, "total": p.amount(cycle) + hosting_amount}


@router.get("/buy")
def buy(request: Request, plan: str = "venue", cycle: str = "year", hosting: int = 0):
    sess = need_login(request)
    sh = shop(request)
    acc = _account(sh, sess)
    ctx = _buy_ctx(sh, plan, cycle, bool(hosting))
    active = sh.db.one("SELECT * FROM subscriptions WHERE account_id=? AND status IN ('active','trialing','past_due','paused')",
                       acc["id"])
    return render(request, "account/buy.html", {"title": "Checkout", "acc": acc, "active": active, **ctx,
                                                "billing_ready": sh.s.billing_ready,
                                                "hosting_domain": sh.s.hosting_domain})


@router.get("/buy/event")
def buy_event(request: Request):
    sess = need_login(request)
    sh = shop(request)
    return render(request, "account/buy_event.html", {"title": "Event pass", "acc": _account(sh, sess),
                                                      "today": date.today().isoformat(),
                                                      "latest": (date.today() + timedelta(days=90)).isoformat(),
                                                      "billing_ready": sh.s.billing_ready})


def _checkout_error(request, msg: str, back: str):
    return render(request, "site/message.html", {"title": "Checkout", "heading": "We couldn't start checkout",
                                                 "text": msg, "action": (back, "Back")}, status=400)


@router.post("/buy")
async def buy_start(request: Request):
    """Make the order and a Paddle transaction for it on the server, then open Paddle's checkout on it."""
    sess = need_login(request)
    data = await form(request)
    sh = shop(request)
    acc = _account(sh, sess)
    kind = data.get("kind", "subscription")
    ref = secrets.token_urlsafe(12)
    if kind == "event":
        back = "/buy/event"
        try:
            start = date.fromisoformat(data.get("event_start", ""))
        except ValueError:
            return _checkout_error(request, "Please pick the first day of your event.", back)
        if not date.today() <= start <= date.today() + timedelta(days=90):
            return _checkout_error(request, "The event has to start within the next 90 days.", back)
        prices = [sh.cat.price_id("pass", "event")]
        order = {"kind": "event", "event_start": start.isoformat()}
    else:
        plan, cycle, hosting = data.get("plan", ""), data.get("cycle", ""), data.get("hosting") == "1"
        back = f"/buy?plan={plan}&cycle={cycle}&hosting={int(hosting)}"
        _buy_ctx(sh, plan, cycle, hosting)
        if sh.db.one("SELECT 1 FROM subscriptions WHERE account_id=? AND status IN ('active','trialing','past_due','paused')",
                     acc["id"]):
            return _checkout_error(request, "You already have a subscription. Change its plan from your account instead.",
                                   "/account/plan")
        prices = [sh.cat.price_id("plan", plan, cycle)]
        order = {"kind": "subscription", "plan": plan, "cycle": cycle, "hosting": int(hosting)}
        if hosting:
            prices.append(sh.cat.price_id("addon", "hosting", cycle))
            try:
                order["venue_slug"] = hostingmod.check_slug(data.get("venue_slug", ""))
            except ValueError as e:
                return _checkout_error(request, f"Venue address: {e}", back)
            order["venue_name"] = " ".join((data.get("venue_name") or acc["organisation"] or "").split())[:80]
            if sh.db.one("SELECT 1 FROM hosting WHERE slug=?", order["venue_slug"]) or sh.db.one(
                    "SELECT 1 FROM orders WHERE venue_slug=? AND status='open' AND account_id!=? AND created_at>?",
                    order["venue_slug"], acc["id"], now() - 3600):
                return _checkout_error(request, "That venue address is taken. Please pick another.", back)
    if not all(prices):
        return _checkout_error(request, "Checkout isn't switched on yet. Please contact us and we'll set you up.", back)
    if not (acc["organisation"] or acc["name"]):
        return _checkout_error(request, "Please add your name or organisation in your details first; it goes on your "
                                        "licence.", "/account/details")
    try:
        cust = acc["paddle_customer_id"] or sh.paddle.find_or_create_customer(acc["email"], acc["name"])
        txn = sh.paddle.create_transaction(cust, prices, {"account_id": str(acc["id"]), "order_ref": ref})
    except PaddleError as e:
        return _checkout_error(request, f"Our payment provider said: {e}. Please try again in a minute.", back)
    with sh.db.tx() as c:
        if not acc["paddle_customer_id"]:
            c.execute("UPDATE accounts SET paddle_customer_id=? WHERE id=? AND paddle_customer_id IS NULL", (cust, acc["id"]))
        c.execute("INSERT INTO orders (ref, account_id, kind, plan, cycle, hosting, venue_slug, venue_name, event_start,"
                  " paddle_transaction_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (ref, acc["id"], order["kind"], order.get("plan"), order.get("cycle"), order.get("hosting", 0),
                   order.get("venue_slug"), order.get("venue_name"), order.get("event_start"), txn["id"], now()))
        audit(c, acc["email"], "checkout.start", ref, {**order, "transaction": txn["id"]}, ip(request))
    return render(request, "account/checkout.html", {"title": "Checkout", "txn": txn["id"], "ref": ref,
                                                     "client_token": sh.s.paddle_client_token, "customer": cust,
                                                     "sandbox": sh.s.paddle_env == "sandbox", "email": acc["email"]})


@router.get("/account/thanks")
def thanks(request: Request, order: str = ""):
    need_login(request)
    return render(request, "account/thanks.html", {"title": "Thank you", "ref": order})


@router.get("/account/order/{ref}")
def order_status(request: Request, ref: str):
    sess = need_login(request)
    sh = shop(request)
    o = sh.db.one("SELECT * FROM orders WHERE ref=? AND account_id=?", ref, sess["account_id"])
    if not o:
        raise HTTPException(404)
    lic = sh.db.one("SELECT l.serial FROM licences l LEFT JOIN transactions t ON t.subscription_id = l.subscription_id "
                    "WHERE l.account_id=? AND (l.transaction_id=? OR t.id=?) LIMIT 1",
                    sess["account_id"], o["paddle_transaction_id"], o["paddle_transaction_id"])
    return JSONResponse({"status": o["status"], "licence": bool(lic)})


# ----------------------------------------------------------------- billing --
@router.get("/account/billing")
def billing(request: Request):
    sess = need_login(request)
    sh = shop(request)
    acc = _account(sh, sess)
    txns, error = [], ""
    if acc["paddle_customer_id"] and sh.s.billing_ready:
        try:
            txns = sh.paddle.transactions(acc["paddle_customer_id"])
        except PaddleError as e:
            error = str(e)
    local = sh.db.all("SELECT * FROM transactions WHERE account_id=? ORDER BY billed_at DESC", acc["id"])
    return render(request, "account/billing.html", {"title": "Billing", "acc": acc, "txns": txns, "local": local,
                                                    "error": error, **_overview(sh, acc)})


@router.post("/account/billing/portal")
async def billing_portal(request: Request):
    """Paddle's own pages to update the card, cancel or see invoices. Links are made fresh each time."""
    sess = need_login(request)
    data = await form(request)
    sh = shop(request)
    acc = _account(sh, sess)
    if not acc["paddle_customer_id"]:
        return redirect("/account/billing")
    subs = [r["id"] for r in sh.db.all("SELECT id FROM subscriptions WHERE account_id=? AND status!='canceled'", acc["id"])]
    try:
        urls = sh.paddle.portal_session(acc["paddle_customer_id"], subs)
    except PaddleError as e:
        return _checkout_error(request, f"Our payment provider said: {e}.", "/account/billing")
    target = urls["general"]["overview"]
    want = data.get("go", "")
    for s in urls.get("subscriptions") or []:
        if want == "cancel" and s.get("cancel_subscription"):
            target = s["cancel_subscription"]
        elif want == "card" and s.get("update_subscription_payment_method"):
            target = s["update_subscription_payment_method"]
    with sh.db.tx() as c:
        audit(c, acc["email"], "billing.portal", want or "overview", ip=ip(request))
    return redirect(target)


@router.get("/account/invoice/{txn_id}")
def invoice(request: Request, txn_id: str):
    sess = need_login(request)
    sh = shop(request)
    acc = _account(sh, sess)
    owned = sh.db.one("SELECT 1 FROM transactions WHERE id=? AND account_id=?", txn_id, acc["id"])
    if not owned and acc["paddle_customer_id"]:
        try:
            owned = any(t["id"] == txn_id for t in sh.paddle.transactions(acc["paddle_customer_id"]))
        except PaddleError:
            owned = False
    if not owned:
        raise HTTPException(404)
    try:
        return redirect(sh.paddle.invoice_url(txn_id))
    except PaddleError as e:
        return _checkout_error(request, f"The invoice isn't ready yet ({e}).", "/account/billing")


# ------------------------------------------------------------- plan change --
def _sub(sh, acc):
    return sh.db.one("SELECT * FROM subscriptions WHERE account_id=? AND status IN ('active','trialing','past_due')"
                     " ORDER BY created_at DESC", acc["id"])


@router.get("/account/plan")
def plan_page(request: Request):
    sess = need_login(request)
    sh = shop(request)
    acc = _account(sh, sess)
    sub = _sub(sh, acc)
    if not sub:
        return redirect("/pricing")
    return render(request, "account/plan.html", {"title": "Change plan", "acc": acc, "sub": sub,
                                                 "hosting_domain": sh.s.hosting_domain})


@router.post("/account/plan")
async def plan_change(request: Request):
    sess = need_login(request)
    data = await form(request)
    sh = shop(request)
    acc = _account(sh, sess)
    sub = _sub(sh, acc)
    if not sub:
        raise HTTPException(400, "No active subscription")
    plan, cycle = data.get("plan", ""), data.get("cycle", sub["cycle"] or "year")
    add_hosting = data.get("add_hosting") == "1"
    if plan not in sh.cat.plans or cycle not in ("month", "year"):
        raise HTTPException(400, "Unknown plan")
    hosting = bool(sub["hosting"]) or add_hosting
    prices = [sh.cat.price_id("plan", plan, cycle)] + ([sh.cat.price_id("addon", "hosting", cycle)] if hosting else [])
    if not all(prices):
        return _checkout_error(request, "That plan isn't on sale yet. Please contact us.", "/account/plan")
    slug = None
    if add_hosting and not sub["hosting"]:
        try:
            slug = hostingmod.check_slug(data.get("venue_slug", ""))
        except ValueError as e:
            return _checkout_error(request, f"Venue address: {e}", "/account/plan")
        if sh.db.one("SELECT 1 FROM hosting WHERE slug=?", slug):
            return _checkout_error(request, "That venue address is taken. Please pick another.", "/account/plan")
    old = sh.cat.plans.get(sub["plan"])
    bigger = old is None or sh.cat.plans[plan].laptops > old.laptops or add_hosting or cycle != sub["cycle"]
    # More laptops or hosting: charged now for the rest of the period. Fewer: credited at the next bill.
    proration = "prorated_immediately" if bigger else "prorated_next_billing_period"
    try:
        sh.paddle.change_items(sub["id"], prices, proration)
    except PaddleError as e:
        return _checkout_error(request, f"Our payment provider said: {e}.", "/account/plan")
    with sh.db.tx() as c:
        if slug:
            hid = c.execute("INSERT INTO hosting (account_id, subscription_id, slug, name, status, created_at)"
                            " VALUES (?,?,?,?,?,?)", (acc["id"], sub["id"], slug,
                                                      (data.get("venue_name") or acc["organisation"] or slug)[:80],
                                                      "pending", now())).lastrowid
            cur = latest_licence(c, sub["id"])
            hostingmod.queue(c, hid, "new", cur["serial"] if cur else None)
            notify.admins(Ctx(c, sh.s, sh.cat, sh.signer), f"New hosted venue: {slug}",
                          f"{acc['email']} added managed hosting. The venue job is in Admin → Hosting.")
        audit(c, acc["email"], "subscription.change", sub["id"], {"plan": plan, "cycle": cycle, "hosting": hosting,
                                                                  "proration": proration}, ip(request))
    return render(request, "site/message.html", {"title": "Plan changed", "heading": "Done",
                  "text": "Your plan is changing. Your new licence key will be in your account and inbox in a minute.",
                  "action": ("/account", "Back to your account")})


# ----------------------------------------------------------------- details --
@router.get("/account/details")
def details(request: Request):
    sess = need_login(request)
    return render(request, "account/details.html", {"title": "Your details", "acc": _account(shop(request), sess)})


@router.post("/account/details")
async def details_save(request: Request):
    sess = need_login(request)
    data = await form(request)
    sh = shop(request)
    vals = {k: " ".join((data.get(k) or "").split())[:120] for k in ("name", "organisation", "phone")}
    with sh.db.tx() as c:
        c.execute("UPDATE accounts SET name=?, organisation=?, phone=?, marketing=? WHERE id=?",
                  (vals["name"], vals["organisation"], vals["phone"], int(data.get("marketing") == "1"), sess["account_id"]))
        audit(c, sess["email"], "account.details", str(sess["account_id"]), vals, ip(request))
    return redirect("/account/details?saved=1")


# ----------------------------------------------------------------- tickets --
@router.get("/account/support")
def my_tickets(request: Request):
    sess = need_login(request)
    sh = shop(request)
    tickets = sh.db.all("SELECT * FROM tickets WHERE account_id=? OR email=? ORDER BY updated_at DESC",
                        sess["account_id"], sess["email"])
    msgs = {t["id"]: sh.db.all("SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY id", t["id"]) for t in tickets}
    return render(request, "account/support.html", {"title": "Your messages", "tickets": tickets, "msgs": msgs})


@router.post("/account/support/{tid}")
async def ticket_reply(request: Request, tid: int):
    sess = need_login(request)
    data = await form(request)
    sh = shop(request)
    t = sh.db.one("SELECT * FROM tickets WHERE id=? AND (account_id=? OR email=?)", tid, sess["account_id"], sess["email"])
    body = (data.get("message") or "").strip()[:8000]
    if not t or not body:
        raise HTTPException(404)
    with sh.db.tx() as c:
        c.execute("INSERT INTO ticket_messages (ticket_id, author, author_email, body, created_at) VALUES (?,?,?,?,?)",
                  (tid, "customer", sess["email"], body, now()))
        c.execute("UPDATE tickets SET status='open', updated_at=? WHERE id=?", (now(), tid))
        notify.admins(Ctx(c, sh.s, sh.cat, sh.signer), f"Reply on #{tid}: {t['subject']}", f"From {sess['email']}\n\n{body}")
    return redirect("/account/support")


__all__ = ["router", "classify", "json"]
