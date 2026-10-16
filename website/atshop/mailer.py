"""Email. Messages are queued in the same transaction as whatever caused them, then sent after
it commits, so a rolled-back payment never emails anyone and a mail outage never loses a payment."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid

from .db import now

log = logging.getLogger("atshop.mail")


def queue(c, to: str, subject: str, body: str, kind: str = "") -> None:
    c.execute("INSERT INTO emails (to_addr, subject, body, kind, created_at) VALUES (?,?,?,?,?)",
              (to, subject, body, kind, now()))


def flush(db, settings) -> int:
    """Send whatever is queued. Returns how many went."""
    rows = db.all("SELECT * FROM emails WHERE status='queued' ORDER BY id LIMIT 50")
    if not rows:
        return 0
    if not settings.smtp_host:
        for r in rows:
            log.info("email (not sent, no SMTP_HOST) to %s: %s", r["to_addr"], r["subject"])
            if settings.dev:  # on your own computer, so you can follow sign-in links
                log.info("%s", r["body"])
            db.run("UPDATE emails SET status='kept' WHERE id=?", r["id"])
        return 0
    sent = 0
    try:
        if settings.smtp_port == 465:
            s = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=ssl.create_default_context())
        else:
            s = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
            if settings.smtp_tls:
                s.starttls(context=ssl.create_default_context())
        with s:
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            for r in rows:
                m = EmailMessage()
                m["From"] = settings.mail_from
                m["To"] = r["to_addr"]
                m["Subject"] = r["subject"]
                m["Message-ID"] = make_msgid(domain=settings.mail_from.rsplit("@", 1)[-1].strip(">") or None)
                m["Reply-To"] = settings.support_email
                m.set_content(r["body"])
                try:
                    s.send_message(m)
                    db.run("UPDATE emails SET status='sent', sent_at=?, error='' WHERE id=?", now(), r["id"])
                    sent += 1
                except smtplib.SMTPRecipientsRefused as e:
                    db.run("UPDATE emails SET status='failed', error=? WHERE id=?", str(e)[:500], r["id"])
    except (OSError, smtplib.SMTPException) as e:
        log.warning("SMTP unavailable, will retry: %s", e)
        for r in rows:
            db.run("UPDATE emails SET error=? WHERE id=? AND status='queued'", str(e)[:500], r["id"])
    return sent
