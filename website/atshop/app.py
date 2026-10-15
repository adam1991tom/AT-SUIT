"""The AT SUITE website, shop, customer portal and admin, as one small FastAPI app.

    uvicorn atshop.app:app --port 8090        (settings from the environment; see .env.example)
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import markdown as md
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import catalogue as catmod
from . import config, jobs
from .db import DB
from .licensing import Signer
from .paddle import Paddle

HERE = Path(__file__).resolve().parent
VERSION = "2.0.0"
log = logging.getLogger("atshop")


class Shop:
    """Everything a request needs, on app.state.shop."""

    def __init__(self, settings: config.Settings, paddle_transport=None, hosting_runner=None, health_transport=None):
        settings.check()
        self.s = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = DB(settings.data_dir / "shop.db")
        self.signer = Signer(settings.signing_key_file)
        self.paddle = Paddle(settings, transport=paddle_transport)
        self.hosting_runner = hosting_runner
        self.health_transport = health_transport
        self.reload_catalogue()

    def reload_catalogue(self) -> None:
        self.cat = catmod.load(self.s.catalogue_file, self.s.prices_file)


def _day(ts) -> str:
    """A unix time or ISO date as '12 Nov 2027'; 0 means it never ends."""
    from datetime import datetime, timezone
    if ts in (None, ""):
        return ""
    if isinstance(ts, str):
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    elif int(ts) == 0:
        return "never"
    else:
        d = datetime.fromtimestamp(int(ts), timezone.utc)
    return f"{d.day} {d:%b %Y}"


def _markdown(text: str) -> str:
    return md.markdown(text, extensions=["tables", "fenced_code", "toc", "sane_lists"], output_format="html")


def content(kind: str, slug: str) -> tuple[str, str] | None:
    """(title, html) of content/<kind>/<slug>.md, or None. Slugs are checked, so no path tricks."""
    if not slug.replace("-", "").isalnum():
        return None
    f = HERE / "content" / kind / f"{slug}.md"
    if not f.is_file():
        return None
    text = f.read_text()
    first = text.splitlines()[0] if text else ""
    title = first.lstrip("# ").strip() if first.startswith("# ") else slug
    return title, _markdown(text)


def create_app(settings: config.Settings | None = None, *, paddle_transport=None, hosting_runner=None,
               health_transport=None, background: bool = True) -> FastAPI:
    shop = Shop(settings or config.load(), paddle_transport, hosting_runner, health_transport)
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_app):
        t = None
        if background:
            t = threading.Thread(target=jobs.loop, args=(shop, stop), name="atshop-jobs", daemon=True)
            t.start()
        yield
        stop.set()

    app = FastAPI(title="AT SUITE", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.shop = shop
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals.update(s=shop.s, money=catmod.money, version=VERSION)
    templates.env.globals["shop"] = shop
    app.state.templates = templates

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        h.setdefault("Content-Security-Policy",
                     "default-src 'self'; script-src 'self' https://cdn.paddle.com; "
                     "style-src 'self' 'unsafe-inline' https://cdn.paddle.com; img-src 'self' data: https://*.paddle.com; "
                     "frame-src https://*.paddle.com; connect-src 'self' https://*.paddle.com; "
                     "font-src 'self'; form-action 'self' https://*.paddle.com; frame-ancestors 'none'; base-uri 'self'")
        if shop.s.secure_cookies:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith(("/account", "/admin", "/login", "/buy")):
            h.setdefault("Cache-Control", "no-store")
        return resp

    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    from . import routes_account, routes_admin, routes_api, routes_site
    templates.env.filters["when"] = routes_admin.when
    templates.env.filters["day"] = _day
    templates.env.filters["fromjson"] = lambda v: __import__("json").loads(v) if v else {}
    templates.env.filters["ago_days"] = lambda ts: int((int(ts) - __import__("time").time()) // 86400) if ts else None
    app.include_router(routes_site.router)
    app.include_router(routes_account.router)
    app.include_router(routes_admin.router)
    app.include_router(routes_api.router)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(HERE / "static" / "img" / "favicon.ico")

    @app.get("/robots.txt", include_in_schema=False)
    def robots():
        return PlainTextResponse(f"User-agent: *\nDisallow: /account\nDisallow: /admin\nDisallow: /login\n"
                                 f"Sitemap: {shop.s.site_url}/sitemap.xml\n")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith(("/api/", "/webhooks/")) or exc.status_code in (301, 302, 303, 307):
            from fastapi.exception_handlers import http_exception_handler
            return await http_exception_handler(request, exc)
        msg = {404: "That page doesn't exist.", 403: "You can't open that page.",
               401: "Please sign in first."}.get(exc.status_code, str(exc.detail))
        from .web import render
        return render(request, "site/error.html", {"title": f"Error {exc.status_code}", "code": exc.status_code,
                                                   "message": msg}, status=exc.status_code)

    return app


def _app_from_env() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()


class _Lazy:
    """`uvicorn atshop.app:app` builds the app from the environment only when it starts."""
    _app = None

    async def __call__(self, scope, receive, send):
        if _Lazy._app is None:
            _Lazy._app = _app_from_env()
        await _Lazy._app(scope, receive, send)


app = _Lazy()
__all__ = ["create_app", "app", "content", "HTMLResponse"]
