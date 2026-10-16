"""Bits every page uses: who's signed in, CSRF, rendering, redirects."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from . import auth


def shop(request: Request):
    return request.app.state.shop


def ip(request: Request) -> str:
    # Cloudflare sets CF-Connecting-IP itself. Otherwise the last X-Forwarded-For entry is the one our own
    # proxy added (earlier entries are whatever the visitor sent); the socket address if there's no proxy.
    v = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[-1]
    if v.strip():
        return v.strip()[:64]
    return request.client.host if request.client else ""


def current(request: Request):
    if not hasattr(request.state, "sess"):
        request.state.sess = auth.session(shop(request).db, request.cookies.get(auth.COOKIE))
    return request.state.sess


def is_admin(request: Request, sess=None) -> bool:
    sess = sess if sess is not None else current(request)
    return bool(sess and sess["email"] in shop(request).s.admin_emails)


def render(request: Request, template: str, ctx: dict | None = None, status: int = 200):
    sess = current(request)
    base = {"sess": sess, "csrf": sess["csrf"] if sess else "", "is_admin": is_admin(request, sess),
            "cat": shop(request).cat, "path": request.url.path}
    return request.app.state.templates.TemplateResponse(request, template, {**base, **(ctx or {})}, status_code=status)


def redirect(url: str, status: int = 303) -> RedirectResponse:
    return RedirectResponse(url, status_code=status)


def need_login(request: Request):
    sess = current(request)
    if not sess:
        nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise HTTPException(303, headers={"Location": f"/login?next={quote(nxt)}"})
    return sess


async def form(request: Request) -> dict:
    """The posted form, after checking its CSRF token against the session."""
    data = dict(await request.form())
    sess = current(request)
    if not auth.csrf_ok(sess, data.get("csrf")):
        raise HTTPException(403, "This form has expired. Go back, reload the page and try again.")
    return data


def safe_next(v: str | None, default: str = "/account") -> str:
    """Only same-site paths, so a sign-in link can't bounce someone to another site."""
    if v and v.startswith("/") and not v.startswith("//") and "\\" not in v:
        return v
    return default
