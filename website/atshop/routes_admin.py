"""Admin: customers, subscriptions, licences, plans, hosting, support, webhooks, email and the audit log.

Only addresses in ADMIN_EMAILS get in, and only after a 6-digit authenticator code in this session.
Every change made here is written to the audit log with who did it and from where.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from . import auth, billing, mailer
from . import hosting as hostingmod
from .billing import Ctx, issue
from .db import audit, now
from .licensing import SigningError, end_of
from .web import current, form, ip, is_admin, need_login, redirect, render, shop

router = APIRouter(prefix="/admin")
EDITIONS = ["small", "venue", "large", "enterprise", "trial", "event", "owner"]


def need_admin(request: Request, second_factor: bool = True):
    sess = need_login(request)
    if not is_admin(request, sess):
        raise HTTPException(403)
    if second_factor and not sess["second_factor"]:
        raise HTTPException(303, headers={"Location": "/admin/2fa"})
    return sess


def _log(sh, sess, request, action, target="", detail=None):
    with sh.db.tx() as c:
        audit(c, sess["email"], action, target, detail, ip(request))


# ------------------------------------------------------------ second factor --
@router.get("/2fa")
def two_factor(request: Request):
    sess = need_admin(request, second_factor=False)
    sh = shop(request)
    acc = sh.db.one("SELECT * FROM accounts WHERE id=?", sess["account_id"])
    secret = acc["totp_secret"]
    setup = not secret
    if setup:
        secret = auth.new_totp_secret()
        sh.db.run("UPDATE accounts SET totp_secret=? WHERE id=? AND totp_secret IS NULL", "pending:" + secret, acc["id"])
    elif secret.startswith("pending:"):
        setup, secret = True, secret.split(":", 1)[1]
    return render(request, "admin/2fa.html", {"title": "Admin sign-in", "setup": setup,
                                              "secret": secret if setup else "",
                                              "uri": auth.otpauth_uri(secret, acc["email"]) if setup else ""})


@router.post("/2fa")
async def two_factor_check(request: Request):
    sess = need_admin(request, second_factor=False)
    data = await form(request)
    sh = shop(request)
    if auth.limited(sh.db, f"2fa:{sess['account_id']}", 8, 900):
        raise HTTPException(429, "Too many tries. Wait 15 minutes.")
    acc = sh.db.one("SELECT * FROM accounts WHERE id=?", sess["account_id"])
    pending = (acc["totp_secret"] or "").startswith("pending:")
    secret = (acc["totp_secret"] or "").removeprefix("pending:")
    if not auth.totp_ok(secret, data.get("code", "")):
        _log(sh, sess, request, "admin.2fa_failed")
        return render(request, "admin/2fa.html", {"title": "Admin sign-in", "setup": pending,
                                                  "secret": secret if pending else "",
                                                  "uri": auth.otpauth_uri(secret, acc["email"]) if pending else "",
                                                  "errors": ["That code didn't match. Check the time on your phone."]},
                      status=400)
    with sh.db.tx() as c:
        c.execute("UPDATE accounts SET totp_secret=? WHERE id=?", (secret, acc["id"]))
        c.execute("UPDATE sessions SET second_factor=1, expires_at=MIN(expires_at, ?) WHERE token_hash=?",
                  (now() + auth.ADMIN_TTL, auth.sha(request.cookies.get(auth.COOKIE, ""))))
        audit(c, sess["email"], "admin.2fa_ok", ip=ip(request))
    return redirect("/admin")


# ----------------------------------------------------------------- overview --
@router.get("")
def overview(request: Request):
    need_admin(request)
    sh = shop(request)
    subs = sh.db.all("SELECT plan, cycle, hosting, status FROM subscriptions")
    mrr = 0.0
    for s in subs:
        if s["status"] in ("active", "past_due") and s["plan"] in sh.cat.plans:
            p = sh.cat.plans[s["plan"]]
            mrr += p.monthly if s["cycle"] == "month" else p.yearly / 12
            if s["hosting"]:
                mrr += sh.cat.amount_of("addon", "hosting", s["cycle"]) / (1 if s["cycle"] == "month" else 12)
    stats = {
        "customers": sh.db.one("SELECT COUNT(*) n FROM accounts")["n"],
        "active": sum(1 for s in subs if s["status"] == "active"),
        "past_due": sum(1 for s in subs if s["status"] == "past_due"),
        "mrr": round(mrr),
        "trials": sh.db.one("SELECT COUNT(*) n FROM licences WHERE source='trial' AND expires>?", now())["n"],
        "tickets": sh.db.one("SELECT COUNT(*) n FROM tickets WHERE status='open'")["n"],
        "jobs": sh.db.one("SELECT COUNT(*) n FROM hosting_jobs WHERE status!='done'")["n"],
    }
    return render(request, "admin/overview.html", {
        "title": "Admin", "stats": stats,
        "problems": sh.db.all("SELECT s.*, a.email FROM subscriptions s LEFT JOIN accounts a ON a.id=s.account_id "
                              "WHERE s.problem IS NOT NULL OR s.account_id IS NULL"),
        "failed": sh.db.all("SELECT * FROM webhook_events WHERE status='failed' ORDER BY received_at DESC LIMIT 20"),
        "events": sh.db.all("SELECT event_id, event_type, status, note, received_at FROM webhook_events "
                            "ORDER BY received_at DESC LIMIT 10"),
        "missing_prices": sh.cat.missing_prices(), "signing_ready": sh.signer.ready})


# ---------------------------------------------------------------- customers --
@router.get("/customers")
def customers(request: Request, q: str = ""):
    need_admin(request)
    sh = shop(request)
    like = f"%{q.strip()}%"
    rows = sh.db.all(
        "SELECT a.*, (SELECT status FROM subscriptions s WHERE s.account_id=a.id ORDER BY created_at DESC LIMIT 1) sub_status,"
        " (SELECT plan FROM subscriptions s WHERE s.account_id=a.id ORDER BY created_at DESC LIMIT 1) sub_plan"
        " FROM accounts a WHERE a.email LIKE ? OR a.name LIKE ? OR a.organisation LIKE ? ORDER BY a.created_at DESC LIMIT 200",
        like, like, like)
    return render(request, "admin/customers.html", {"title": "Customers", "rows": rows, "q": q})


@router.get("/customers/{aid}")
def customer(request: Request, aid: int):
    need_admin(request)
    sh = shop(request)
    acc = sh.db.one("SELECT * FROM accounts WHERE id=?", aid)
    if not acc:
        raise HTTPException(404)
    return render(request, "admin/customer.html", {
        "title": acc["email"], "acc": acc, "editions": EDITIONS,
        "subs": sh.db.all("SELECT * FROM subscriptions WHERE account_id=? ORDER BY created_at DESC", aid),
        "licences": sh.db.all("SELECT * FROM licences WHERE account_id=? ORDER BY issued DESC", aid),
        "txns": sh.db.all("SELECT * FROM transactions WHERE account_id=? ORDER BY billed_at DESC", aid),
        "venues": sh.db.all("SELECT * FROM hosting WHERE account_id=?", aid),
        "tickets": sh.db.all("SELECT * FROM tickets WHERE account_id=? OR email=? ORDER BY updated_at DESC", aid, acc["email"]),
        "orders": sh.db.all("SELECT * FROM orders WHERE account_id=? ORDER BY id DESC LIMIT 20", aid),
        "audit": sh.db.all("SELECT * FROM audit WHERE actor=? OR target=? ORDER BY at DESC LIMIT 50", acc["email"], str(aid)),
    })


@router.post("/customers/{aid}/access")
async def customer_access(request: Request, aid: int):
    sess = need_admin(request)
    data = await form(request)
    sh = shop(request)
    disabled = int(data.get("disabled") == "1")
    with sh.db.tx() as c:
        c.execute("UPDATE accounts SET disabled=? WHERE id=?", (disabled, aid))
        if disabled:
            c.execute("DELETE FROM sessions WHERE account_id=?", (aid,))
        audit(c, sess["email"], "account.disable" if disabled else "account.enable", str(aid), ip=ip(request))
    return redirect(f"/admin/customers/{aid}")


# ----------------------------------------------------------------- licences --
@router.get("/licences")
def licences(request: Request, q: str = ""):
    need_admin(request)
    sh = shop(request)
    like = f"%{q.strip()}%"
    rows = sh.db.all("SELECT l.*, a.email FROM licences l JOIN accounts a ON a.id=l.account_id WHERE l.serial LIKE ? OR "
                     "a.email LIKE ? OR l.licensee LIKE ? ORDER BY l.issued DESC LIMIT 300", like, like, like)
    return render(request, "admin/licences.html", {"title": "Licences", "rows": rows, "q": q, "editions": EDITIONS})


@router.post("/licences/issue")
async def licence_issue(request: Request):
    """A key by hand: enterprise deals, extra trial time, an owner key, or replacing one."""
    sess = need_admin(request)
    data = await form(request)
    sh = shop(request)
    try:
        email = auth.clean_email(data.get("email", ""))
        edition = data.get("edition", "")
        if edition not in EDITIONS:
            raise ValueError("Pick a plan")
        nodes = int(data.get("nodes") or 0)
        sites = int(data.get("sites") or 1)
        if nodes < 0 or sites < 0:
            raise ValueError("Laptops and sites can't be negative")
        if data.get("never") == "1" or edition == "owner":
            expires = 0
        else:
            until = date.fromisoformat(data.get("until", ""))
            if until <= date.today():
                raise ValueError("The end date must be in the future")
            expires = end_of(until)
        note = (data.get("note") or "").strip()[:300]
        if not note:
            raise ValueError("Say why in the note (it goes in the audit log)")
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    try:
        with sh.db.tx() as c:
            acc = c.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
            if acc is None:
                c.execute("INSERT INTO accounts (email, organisation, created_at) VALUES (?,?,?)",
                          (email, (data.get("licensee") or "").strip()[:120], now()))
                acc = c.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
            if data.get("licensee"):
                acc = {**dict(acc), "organisation": data["licensee"].strip()[:120]}
            ctx = Ctx(c, sh.s, sh.cat, sh.signer)
            lic = issue(ctx, acc, source="manual", edition=edition, max_nodes=nodes, max_sites=sites, expires=expires,
                        note=note, actor=sess["email"])
            if data.get("send") == "1":
                from . import notify
                notify.key_email(ctx, acc, lic, "Your AT SUITE licence key", reason="manual")
            audit(c, sess["email"], "admin.licence_issue", lic["serial"], {"email": email, "note": note}, ip(request))
    except SigningError as e:
        raise HTTPException(500, str(e)) from e
    mailer.flush(sh.db, sh.s)
    return redirect(f"/admin/customers/{acc['id']}")


@router.post("/licences/{serial}/revoke")
async def licence_revoke(request: Request, serial: str):
    """Stops the shop handing out or renewing this key. A key already pasted into a server keeps
    working offline until it expires; only time ends an offline key."""
    sess = need_admin(request)
    data = await form(request)
    sh = shop(request)
    with sh.db.tx() as c:
        c.execute("UPDATE licences SET revoked_at=?, note=note || ? WHERE serial=? AND revoked_at IS NULL",
                  (now(), f" | revoked: {(data.get('reason') or '')[:200]}", serial))
        audit(c, sess["email"], "admin.licence_revoke", serial, {"reason": data.get("reason", "")}, ip(request))
    return redirect("/admin/licences")


@router.get("/licences/{serial}.txt")
def licence_txt(request: Request, serial: str):
    sess = need_admin(request)
    sh = shop(request)
    lic = sh.db.one("SELECT key FROM licences WHERE serial=?", serial)
    if not lic:
        raise HTTPException(404)
    _log(sh, sess, request, "admin.licence_download", serial)
    return PlainTextResponse(lic["key"] + "\n", headers={"Content-Disposition": f'attachment; filename="{serial}.txt"'})


# -------------------------------------------------------------------- plans --
@router.get("/plans")
def plans(request: Request):
    need_admin(request)
    sh = shop(request)
    sh.reload_catalogue()
    rows = []
    for kind, key, cycle, name, amount in sh.cat.items():
        rows.append({"kind": kind, "key": key, "cycle": cycle, "name": name, "amount": amount,
                     "price_id": sh.cat.price_id(kind, key, cycle)})
    return render(request, "admin/plans.html", {"title": "Plans and prices", "rows": rows,
                                                "old": [(k, v) for k, v in sh.cat.prices.items() if not v.get("active", True)
                                                        or k not in {r["price_id"] for r in rows}]})


# ------------------------------------------------------------------ hosting --
@router.get("/hosting")
def hosting(request: Request):
    need_admin(request)
    sh = shop(request)
    venues = sh.db.all("SELECT h.*, a.email, s.status sub_status FROM hosting h JOIN accounts a ON a.id=h.account_id "
                       "LEFT JOIN subscriptions s ON s.id=h.subscription_id ORDER BY h.id DESC")
    jobs = sh.db.all("SELECT j.*, h.slug, h.name FROM hosting_jobs j JOIN hosting h ON h.id=j.hosting_id "
                     "ORDER BY j.status='done', j.id DESC LIMIT 100")
    cmds = {j["id"]: hostingmod.manual_text(sh.s, j, j) for j in jobs}
    return render(request, "admin/hosting.html", {"title": "Hosting", "venues": venues, "jobs": jobs, "cmds": cmds,
                                                  "mode": sh.s.hosting_mode,
                                                  "address": lambda slug: hostingmod.address(sh.s, slug)})


@router.post("/hosting/jobs/{jid}/done")
async def hosting_job_done(request: Request, jid: int):
    sess = need_admin(request)
    data = await form(request)
    sh = shop(request)
    with sh.db.tx() as c:
        job = c.execute("SELECT * FROM hosting_jobs WHERE id=?", (jid,)).fetchone()
        if not job:
            raise HTTPException(404)
        h = c.execute("SELECT * FROM hosting WHERE id=?", (job["hosting_id"],)).fetchone()
        ok = data.get("result") != "failed"
        c.execute("UPDATE hosting_jobs SET status=?, result=?, done_at=? WHERE id=?",
                  ("done" if ok else "failed", (data.get("output") or "marked by hand")[:4000], now(), jid))
        if ok:
            hostingmod.apply_done(c, sh.s, job, h, data.get("output") or "")
        audit(c, sess["email"], f"hosting.{job['action']}.{'done' if ok else 'failed'}", h["slug"], ip=ip(request))
    return redirect("/admin/hosting")


@router.post("/hosting/jobs/{jid}/retry")
async def hosting_job_retry(request: Request, jid: int):
    sess = need_admin(request)
    await form(request)
    sh = shop(request)
    with sh.db.tx() as c:
        c.execute("UPDATE hosting_jobs SET status='todo', done_at=NULL WHERE id=?", (jid,))
        audit(c, sess["email"], "hosting.retry", str(jid), ip=ip(request))
    return redirect("/admin/hosting")


@router.post("/hosting/{hid}/{action}")
async def hosting_action(request: Request, hid: int, action: str):
    sess = need_admin(request)
    data = await form(request)
    sh = shop(request)
    if action not in ("suspend", "resume", "licence", "slug", "end"):
        raise HTTPException(404)
    with sh.db.tx() as c:
        h = c.execute("SELECT * FROM hosting WHERE id=?", (hid,)).fetchone()
        if not h:
            raise HTTPException(404)
        if action == "slug":
            try:
                slug = hostingmod.check_slug(data.get("slug", ""))
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            if h["status"] != "pending":
                raise HTTPException(400, "Only a venue that isn't made yet can change address")
            c.execute("UPDATE hosting SET slug=? WHERE id=?", (slug, hid))
        elif action == "end":
            # The record only; the venue and its data stay until you run `atcloud remove` yourself.
            c.execute("UPDATE hosting SET status='ended' WHERE id=?", (hid,))
        elif action == "licence":
            lic = billing.latest_licence(c, h["subscription_id"]) if h["subscription_id"] else None
            if not lic:
                raise HTTPException(400, "No current key for this venue's subscription")
            hostingmod.queue(c, hid, "licence", lic["serial"])
        else:
            hostingmod.queue(c, hid, action)
        audit(c, sess["email"], f"admin.hosting_{action}", h["slug"], dict(data) | {"csrf": None}, ip(request))
    return redirect("/admin/hosting")


@router.post("/hosting/new")
async def hosting_new(request: Request):
    """A hosted venue by hand, for an account (an enterprise deal, or a customer who paid without an address)."""
    sess = need_admin(request)
    data = await form(request)
    sh = shop(request)
    try:
        slug = hostingmod.check_slug(data.get("slug", ""))
        email = auth.clean_email(data.get("email", ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    with sh.db.tx() as c:
        acc = c.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
        if not acc:
            raise HTTPException(400, "No account with that email")
        if c.execute("SELECT 1 FROM hosting WHERE slug=?", (slug,)).fetchone():
            raise HTTPException(400, "That address is taken")
        sub = c.execute("SELECT id FROM subscriptions WHERE account_id=? AND status!='canceled' ORDER BY created_at DESC",
                        (acc["id"],)).fetchone()
        lic = c.execute("SELECT serial FROM licences WHERE account_id=? AND replaced_by IS NULL AND revoked_at IS NULL "
                        "ORDER BY issued DESC LIMIT 1", (acc["id"],)).fetchone()
        hid = c.execute("INSERT INTO hosting (account_id, subscription_id, slug, name, status, created_at) VALUES (?,?,?,?,?,?)",
                        (acc["id"], sub["id"] if sub else None, slug, (data.get("name") or acc["organisation"] or slug)[:80],
                         "pending", now())).lastrowid
        hostingmod.queue(c, hid, "new", lic["serial"] if lic else None)
        audit(c, sess["email"], "admin.hosting_new", slug, {"email": email}, ip(request))
    return redirect("/admin/hosting")


@router.get("/hosting/jobs/{jid}/key.txt")
def hosting_job_key(request: Request, jid: int):
    sess = need_admin(request)
    sh = shop(request)
    j = sh.db.one("SELECT j.licence_serial, h.slug FROM hosting_jobs j JOIN hosting h ON h.id=j.hosting_id WHERE j.id=?", jid)
    lic = sh.db.one("SELECT key FROM licences WHERE serial=?", j["licence_serial"]) if j and j["licence_serial"] else None
    if not lic:
        raise HTTPException(404)
    _log(sh, sess, request, "admin.licence_download", j["licence_serial"])
    return PlainTextResponse(lic["key"] + "\n", headers={"Content-Disposition": f'attachment; filename="{j["slug"]}-key.txt"'})


# ------------------------------------------------------------------ support --
@router.get("/tickets")
def tickets(request: Request, status: str = "open"):
    need_admin(request)
    sh = shop(request)
    rows = sh.db.all("SELECT * FROM tickets WHERE (?='all' OR status=?) ORDER BY updated_at DESC LIMIT 200", status, status)
    return render(request, "admin/tickets.html", {"title": "Support", "rows": rows, "status": status})


@router.get("/tickets/{tid}")
def ticket(request: Request, tid: int):
    need_admin(request)
    sh = shop(request)
    t = sh.db.one("SELECT * FROM tickets WHERE id=?", tid)
    if not t:
        raise HTTPException(404)
    return render(request, "admin/ticket.html", {"title": f"#{tid} {t['subject']}", "t": t,
                                                 "msgs": sh.db.all("SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY id", tid)})


@router.post("/tickets/{tid}")
async def ticket_answer(request: Request, tid: int):
    sess = need_admin(request)
    data = await form(request)
    sh = shop(request)
    body = (data.get("message") or "").strip()[:8000]
    with sh.db.tx() as c:
        t = c.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
        if not t:
            raise HTTPException(404)
        status = data.get("status") if data.get("status") in ("open", "answered", "closed") else "answered"
        if body:
            c.execute("INSERT INTO ticket_messages (ticket_id, author, author_email, body, created_at) VALUES (?,?,?,?,?)",
                      (tid, "staff", sess["email"], body, now()))
            mailer.queue(c, t["email"], f"Re: {t['subject']} (#{tid})",
                         f"{body}\n\n--\nReply from your account: {sh.s.site_url}/account/support\nAT SUITE support\n", "ticket")
        c.execute("UPDATE tickets SET status=?, updated_at=? WHERE id=?", (status, now(), tid))
        audit(c, sess["email"], "ticket.answer", str(tid), {"status": status, "replied": bool(body)}, ip(request))
    mailer.flush(sh.db, sh.s)
    return redirect(f"/admin/tickets/{tid}")


# --------------------------------------------------------- webhooks, email --
@router.get("/events")
def events(request: Request):
    need_admin(request)
    sh = shop(request)
    return render(request, "admin/events.html", {"title": "Paddle events", "rows": sh.db.all(
        "SELECT event_id, event_type, occurred_at, received_at, status, note FROM webhook_events ORDER BY received_at DESC LIMIT 300")})


@router.post("/events/{eid}/retry")
async def event_retry(request: Request, eid: str):
    sess = need_admin(request)
    await form(request)
    sh = shop(request)
    row = sh.db.one("SELECT payload, status FROM webhook_events WHERE event_id=?", eid)
    if not row or row["status"] != "failed":
        raise HTTPException(400, "Only failed events can be retried")
    status, note = billing.handle(sh.db, sh.s, sh.cat, sh.signer, json.loads(row["payload"]))
    _log(sh, sess, request, "admin.event_retry", eid, {"status": status, "note": note})
    mailer.flush(sh.db, sh.s)
    return redirect("/admin/events")


@router.get("/emails")
def emails(request: Request):
    need_admin(request)
    sh = shop(request)
    return render(request, "admin/emails.html", {"title": "Emails", "smtp": bool(sh.s.smtp_host), "rows": sh.db.all(
        "SELECT * FROM emails ORDER BY id DESC LIMIT 200")})


@router.get("/audit")
def audit_log(request: Request, q: str = ""):
    need_admin(request)
    sh = shop(request)
    like = f"%{q}%"
    rows = sh.db.all("SELECT * FROM audit WHERE actor LIKE ? OR action LIKE ? OR target LIKE ? ORDER BY id DESC LIMIT 500",
                     like, like, like)
    return render(request, "admin/audit.html", {"title": "Audit log", "rows": rows, "q": q})


def when(ts) -> str:
    if not ts:
        return ""
    if isinstance(ts, str):
        return ts[:16].replace("T", " ")
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M")


__all__ = ["router", "when", "current", "time"]
