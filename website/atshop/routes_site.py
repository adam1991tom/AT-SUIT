"""Public pages, contact/support forms, and signing in."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from . import auth, notify
from .app import content
from .db import audit, now
from .web import current, form, ip, redirect, render, safe_next, shop

router = APIRouter()

PAGES = {
    "/": ("site/home.html", "AT SUITE: show control software for live events"),
    "/features": ("site/features.html", "Features"),
    "/how-it-works": ("site/how.html", "How it works"),
    "/pricing": ("site/pricing.html", "Pricing"),
    "/hosting": ("site/hosting.html", "Managed hosting or your own server"),
    "/faq": ("site/faq.html", "Questions and answers"),
    "/support": ("site/support.html", "Support"),
    "/downloads": ("site/downloads.html", "Downloads"),
}
LEGAL = {"terms": "Terms of Service", "privacy": "Privacy Policy", "dpa": "Data Processing Agreement",
         "billing": "Billing, cancellation and refunds", "acceptable-use": "Acceptable use"}
DOCS = ["getting-started", "self-hosted-install", "managed-hosting", "licence-keys", "laptop-app", "billing"]


def _page(path: str, template: str, title: str):
    def view(request: Request):
        return render(request, template, {"title": title})
    view.__name__ = "page_" + (path.strip("/").replace("-", "_") or "home")
    router.add_api_route(path, view, methods=["GET"])


for _p, (_t, _title) in PAGES.items():
    _page(_p, _t, _title)


@router.get("/legal/{slug}")
def legal(request: Request, slug: str):
    if slug not in LEGAL:
        raise HTTPException(404)
    page = content("legal", slug)
    if not page:
        raise HTTPException(404)
    return render(request, "site/prose.html", {"title": LEGAL[slug], "html": page[1], "draft": True, "section": "legal"})


@router.get("/docs")
def docs_index(request: Request):
    items = [(slug, content("docs", slug)[0]) for slug in DOCS if content("docs", slug)]
    return render(request, "site/docs.html", {"title": "Documentation", "items": items})


@router.get("/docs/{slug}")
def doc(request: Request, slug: str):
    page = content("docs", slug) if slug in DOCS else None
    if not page:
        raise HTTPException(404)
    return render(request, "site/prose.html", {"title": page[0], "html": page[1], "section": "docs",
                                               "docs": [(s, content("docs", s)[0]) for s in DOCS if content("docs", s)]})


@router.get("/sitemap.xml")
def sitemap(request: Request):
    base = shop(request).s.site_url
    urls = list(PAGES) + ["/contact", "/docs"] + [f"/docs/{d}" for d in DOCS] + [f"/legal/{k}" for k in LEGAL]
    body = "".join(f"<url><loc>{base}{u}</loc></url>" for u in urls)
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    f"{body}</urlset>", media_type="application/xml")


# ----------------------------------------------------------------- contact --
KINDS = {"sales": "Sales and quotes", "support": "Help with AT SUITE", "billing": "Billing", "privacy": "Privacy and data"}


@router.get("/contact")
def contact(request: Request, kind: str = "sales", plan: str = ""):
    return render(request, "site/contact.html", {"title": "Contact us", "kinds": KINDS,
                                                 "kind": kind if kind in KINDS else "sales", "plan": plan})


@router.post("/contact")
async def contact_send(request: Request):
    sh = shop(request)
    data = dict(await request.form())
    sess = current(request)
    if sess and not auth.csrf_ok(sess, data.get("csrf")):
        raise HTTPException(403, "This form has expired. Reload the page and try again.")
    if data.get("website"):  # a field people can't see; bots fill it in
        return redirect("/contact/sent")
    errors = []
    try:
        email = sess["email"] if sess else auth.clean_email(data.get("email", ""))
    except ValueError as e:
        errors.append(str(e))
        email = ""
    subject = " ".join((data.get("subject") or "").split())[:150]
    body = (data.get("message") or "").strip()[:8000]
    kind = data.get("kind") if data.get("kind") in KINDS else "support"
    if not subject:
        errors.append("Please add a subject.")
    if len(body) < 10:
        errors.append("Please tell us a little more.")
    if not errors and auth.limited(sh.db, f"contact:{ip(request)}", 5, 3600):
        errors.append("You've sent a few messages already. Please wait a while, or email us.")
    if errors:
        return render(request, "site/contact.html", {"title": "Contact us", "kinds": KINDS, "kind": kind,
                                                     "errors": errors, "form": data}, status=400)
    with sh.db.tx() as c:
        tid = c.execute("INSERT INTO tickets (account_id, email, name, organisation, kind, subject, created_at, updated_at)"
                        " VALUES (?,?,?,?,?,?,?,?)", (sess["account_id"] if sess else None, email,
                                                      (data.get("name") or (sess["name"] if sess else ""))[:120],
                                                      (data.get("organisation") or "")[:120], kind, subject, now(), now())).lastrowid
        c.execute("INSERT INTO ticket_messages (ticket_id, author, author_email, body, created_at) VALUES (?,?,?,?,?)",
                  (tid, "customer", email, body, now()))
        from .billing import Ctx
        notify.admins(Ctx(c, sh.s, sh.cat, sh.signer), f"New {kind} message #{tid}: {subject}",
                      f"From {email}\n\n{body}")
        from . import mailer
        mailer.queue(c, email, f"We've got your message (#{tid})",
                     f"Hello,\n\nThanks for getting in touch. We've got your message and will reply by email."
                     f"\n\nAT SUITE\n", "ticket")
        audit(c, email, "ticket.create", str(tid), {"kind": kind}, ip(request))
    return redirect("/contact/sent")


@router.get("/contact/sent")
def contact_sent(request: Request):
    return render(request, "site/message.html", {"title": "Message sent", "heading": "Thanks, we've got it",
                                                 "text": "We'll reply by email. You'll find a copy in your inbox."})


# ------------------------------------------------------------------ sign in --
@router.get("/login")
def login(request: Request, next: str = ""):
    if current(request):
        return redirect(safe_next(next))
    return render(request, "site/login.html", {"title": "Sign in", "next": safe_next(next, "")})


@router.get("/register")
def register(request: Request, next: str = ""):
    return render(request, "site/register.html", {"title": "Create your account", "next": safe_next(next, "")})


async def _send_link(request: Request, new: bool):
    sh = shop(request)
    data = dict(await request.form())
    nxt = safe_next(data.get("next"), "")
    tpl = "site/register.html" if new else "site/login.html"
    try:
        email = auth.clean_email(data.get("email", ""))
    except ValueError as e:
        return render(request, tpl, {"title": "Sign in", "errors": [str(e)], "form": data, "next": nxt}, status=400)
    if new and not data.get("terms"):
        return render(request, tpl, {"title": "Create your account", "form": data, "next": nxt,
                                     "errors": ["Please agree to the terms to create an account."]}, status=400)
    if new and not (data.get("name") or "").strip():
        return render(request, tpl, {"title": "Create your account", "form": data, "next": nxt,
                                     "errors": ["Please add your name."]}, status=400)
    # Same answer whether or not the address has an account, so the form can't be used to find customers.
    if not auth.limited(sh.db, f"link:{email}", 5, 3600) and not auth.limited(sh.db, f"linkip:{ip(request)}", 20, 3600):
        with sh.db.tx() as c:
            exists = c.execute("SELECT 1 FROM accounts WHERE email=?", (email,)).fetchone()
            payload = {} if exists else {"name": (data.get("name") or "").strip(),
                                         "organisation": (data.get("organisation") or "").strip(),
                                         "phone": (data.get("phone") or "").strip(), "marketing": bool(data.get("marketing"))}
            if exists or new:
                link = auth.make_link(c, sh.s, email, payload, ip(request), nxt)
                notify.login_link(c, sh.s, email, link, new=not exists)
    from . import mailer
    mailer.flush(sh.db, sh.s)
    return render(request, "site/message.html", {"title": "Check your email", "heading": "Check your email",
                  "text": f"If {email} {'can sign in' if not new else 'is right'}, a link is on its way. It works once, "
                          f"for 20 minutes. No email? Check spam, or try again in a minute."})


@router.post("/login")
async def login_send(request: Request):
    return await _send_link(request, new=False)


@router.post("/register")
async def register_send(request: Request):
    return await _send_link(request, new=True)


@router.get("/login/confirm")
def login_confirm(request: Request, next: str = ""):
    # A button rather than signing in on GET, so email scanners that open links can't use them up.
    return render(request, "site/confirm.html", {"title": "Sign in", "next": safe_next(next)})


@router.post("/login/confirm")
async def login_finish(request: Request):
    sh = shop(request)
    data = dict(await request.form())
    acc = auth.use_link(sh.db, data.get("token", ""))
    if not acc:
        return render(request, "site/message.html", {"title": "Link expired", "heading": "That link has expired",
                      "text": "Sign-in links work once, for 20 minutes. Ask for a new one below.",
                      "action": ("/login", "Send a new link")}, status=400)
    token = auth.start_session(sh.db, acc["id"], ip(request), request.headers.get("user-agent", ""))
    with sh.db.tx() as c:
        audit(c, acc["email"], "login", str(acc["id"]), ip=ip(request))
    r = redirect(safe_next(data.get("next")))
    r.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_TTL, httponly=True, samesite="lax",
                 secure=sh.s.secure_cookies, path="/")
    return r


@router.post("/logout")
async def logout(request: Request):
    await form(request)
    auth.end_session(shop(request).db, request.cookies.get(auth.COOKIE))
    r = redirect("/")
    r.delete_cookie(auth.COOKIE, path="/")
    return r
