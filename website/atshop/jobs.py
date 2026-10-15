"""Background work: send queued email, run hosting jobs (ssh mode), and once a day the expiry
reminders and housekeeping. Runs in one thread next to the web server."""
from __future__ import annotations

import logging
import time

from . import hosting, mailer, notify
from .billing import Ctx
from .db import now

log = logging.getLogger("atshop.jobs")
DAY = 86400


def reminders(shop) -> int:
    """Licences that end soon and won't renew (trials, event passes, cancelled or unpaid
    subscriptions): one email 30 days and one 7 days before."""
    sent = 0
    t = now()
    rows = shop.db.all(
        "SELECT l.*, s.status AS sub_status, s.scheduled_change FROM licences l "
        "LEFT JOIN subscriptions s ON s.id = l.subscription_id "
        "WHERE l.replaced_by IS NULL AND l.revoked_at IS NULL AND l.expires > ? AND l.expires < ?",
        t, t + 31 * DAY)
    for lic in rows:
        renews = lic["subscription_id"] and lic["sub_status"] in ("active", "trialing") and not lic["scheduled_change"]
        if renews or lic["source"] == "event":
            continue
        left = (lic["expires"] - t) / DAY
        days = 7 if left <= 7 else 30 if left <= 30 else 0
        if not days:
            continue
        with shop.db.tx() as c:
            acc = c.execute("SELECT * FROM accounts WHERE id=?", (lic["account_id"],)).fetchone()
            if acc and not acc["disabled"]:
                notify.expiry_reminder(Ctx(c, shop.s, shop.cat, shop.signer), acc, lic, days)
                sent += 1
    return sent


def housekeeping(shop) -> None:
    t = now()
    shop.db.run("DELETE FROM login_tokens WHERE expires_at < ?", t - DAY)
    shop.db.run("DELETE FROM sessions WHERE expires_at < ?", t)
    shop.db.run("DELETE FROM rate WHERE window < ?", t // 3600 - 48)
    # Checkout pages opened but never paid.
    shop.db.run("UPDATE orders SET status='abandoned' WHERE status='open' AND created_at < ?", t - 7 * DAY)


def tick(shop, last_daily: float) -> float:
    try:
        hosting.run_pending(shop.db, shop.s, shop.hosting_runner)
    except Exception:  # noqa: BLE001 - a background loop must keep going
        log.exception("hosting jobs")
    if time.time() - last_daily > DAY:
        try:
            reminders(shop)
            housekeeping(shop)
        except Exception:  # noqa: BLE001
            log.exception("daily jobs")
        last_daily = time.time()
    try:
        mailer.flush(shop.db, shop.s)
    except Exception:  # noqa: BLE001
        log.exception("email")
    return last_daily


def loop(shop, stop) -> None:
    last_daily = 0.0
    while not stop.is_set():
        last_daily = tick(shop, last_daily)
        stop.wait(30)
