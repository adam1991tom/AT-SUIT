"""The emails the shop sends. Plain text, short, and each says what to do next."""
from __future__ import annotations

from datetime import datetime, timezone

from . import mailer
from .db import now


def _day(ts_or_iso) -> str:
    if not ts_or_iso:
        return "the end of the paid period"
    if isinstance(ts_or_iso, str):
        d = datetime.fromisoformat(ts_or_iso.replace("Z", "+00:00"))
    else:
        d = datetime.fromtimestamp(int(ts_or_iso), timezone.utc)
    return f"{d.day} {d:%B %Y}"


def _sign_off(ctx) -> str:
    return (f"\nYour account: {ctx.s.site_url}/account\nHelp: {ctx.s.site_url}/support or {ctx.s.support_email}\n\n"
            f"AT SUITE\n")


def _hello(acc) -> str:
    return f"Hello {acc['name'].split()[0] if acc['name'] else 'there'},\n\n"


def once(ctx, acc, kind: str, ref: str, subject: str, body: str) -> bool:
    """Send this email to this account only once for (kind, ref)."""
    cur = ctx.c.execute("INSERT OR IGNORE INTO notices (account_id, kind, ref, sent_at) VALUES (?,?,?,?)",
                        (acc["id"], kind, ref, now()))
    if cur.rowcount:
        mailer.queue(ctx.c, acc["email"], subject, body, kind)
        return True
    return False


def admins(ctx, subject: str, body: str) -> None:
    for a in sorted(ctx.s.admin_emails):
        mailer.queue(ctx.c, a, f"[AT SUITE shop] {subject}", body + f"\n\n{ctx.s.site_url}/admin\n", "admin")


def key_email(ctx, acc, lic: dict, subject: str, reason: str) -> None:
    ends = _day(lic["expires"]) if lic["expires"] else "never"
    laptops = lic["max_nodes"] or "unlimited"
    lead = {
        "welcome": "Thank you for buying AT SUITE. Your payment went through and your licence key is below.",
        "renewal": "Your AT SUITE subscription has renewed. Here is the licence key for the new period.",
        "plan_change": "Your plan has changed. This key has your new laptop limit.",
        "event": "Thank you for buying an AT SUITE event pass. Your licence key is below.",
        "trial": "Your 30-day AT SUITE trial has started. Your licence key is below.",
        "manual": "Here is your AT SUITE licence key.",
    }.get(reason, "Here is your AT SUITE licence key.")
    hosted = ctx.c.execute("SELECT 1 FROM hosting WHERE subscription_id=? AND status!='ended'",
                           (lic.get("subscription_id") or "",)).fetchone() if lic.get("subscription_id") else None
    how = ("We'll put it on your hosted AT SUITE server for you; there is nothing to do."
           if hosted else
           "To use it: sign in to your AT SUITE server as an admin, open Licence, paste the key and press Save. "
           "Settings and data are kept. On a new install, paste it on the setup page.")
    body = (f"{_hello(acc)}{lead}\n\n"
            f"Licensed to: {lic['licensee']}\nPlan: {lic['edition']}\nLaptops: {laptops}\nValid until: {ends}\n"
            f"Serial: {lic['serial']}\n\nLicence key:\n{lic['key']}\n\n{how}\n"
            f"You can also copy or download it any time from your account.\n{_sign_off(ctx)}")
    mailer.queue(ctx.c, acc["email"], subject, body, f"key:{reason}")


def payment_failed(ctx, acc, ref: str) -> None:
    sub = ctx.c.execute("SELECT period_start FROM subscriptions WHERE id=?", (ref,)).fetchone()
    once(ctx, acc, "payment_failed", f"{ref}:{sub['period_start'] if sub else ''}", "AT SUITE: your payment didn't go through",
         f"{_hello(acc)}We couldn't take your last AT SUITE payment. Paddle, our reseller, will try again over the next "
         f"few days.\n\nPlease check your card at {ctx.s.site_url}/account/billing (Update payment method).\n\n"
         f"Your AT SUITE keeps working meanwhile: your current key has a few spare days, and after it ends there "
         f"are 14 days of grace before anything locks, and it never locks during a show.\n{_sign_off(ctx)}")


def cancel_scheduled_body(ctx, acc, sub_id: str, effective_at) -> str:
    return (f"{_hello(acc)}Your AT SUITE subscription is set to end on {_day(effective_at)}. It won't renew after that.\n\n"
            f"Your licence key keeps working until it ends, then AT SUITE gives you 14 days of grace before it locks. "
            f"Nothing is deleted, and a new key unlocks it at once.\n\nChanged your mind? Resume it from "
            f"{ctx.s.site_url}/account/billing before then.\n{_sign_off(ctx)}")


def canceled_body(ctx, acc, lic) -> str:
    until = _day(lic["expires"]) if lic else "the end of the paid period"
    return (f"{_hello(acc)}Your AT SUITE subscription has ended. Your last key works until {until}, followed by 14 "
            f"days of grace.\n\nWe're sorry to see you go. If something wasn't right, reply to this email and tell us.\n"
            f"To come back, choose a plan at {ctx.s.site_url}/pricing; your settings and data are kept on your server.\n"
            f"{_sign_off(ctx)}")


def expiry_reminder(ctx, acc, lic, days: int) -> None:
    once(ctx, acc, f"expiry_{days}", lic["serial"], f"AT SUITE: your licence ends in {days} days",
         f"{_hello(acc)}Your AT SUITE licence ({lic['edition']}, serial {lic['serial']}) ends on {_day(lic['expires'])} "
         f"and won't be renewed automatically.\n\nTo keep going, choose a plan at {ctx.s.site_url}/pricing. After it "
         f"ends, AT SUITE keeps working for 14 days of grace, then locks (never during a show) until a new key is "
         f"pasted in. Nothing is deleted.\n{_sign_off(ctx)}")


def login_link(c, settings, email: str, link: str, new: bool) -> None:
    what = "finish creating your account" if new else "sign in"
    mailer.queue(c, email, "Your AT SUITE sign-in link" if not new else "Confirm your AT SUITE account",
                 f"Hello,\n\nUse this link to {what}. It works once, for 20 minutes:\n\n{link}\n\n"
                 f"If you didn't ask for this, ignore this email; nothing happens without the link.\n\nAT SUITE\n",
                 "login")
