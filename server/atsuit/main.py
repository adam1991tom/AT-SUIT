"""AT-SUIT server: one app, one port, every module."""
from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import VERSION, asr, config, db, licence, updates
from .hub import can_subscribe, hub
from .modules import captions, cluster, comms, core, dashboard, fleet, imports, overlays, pairing, presenter, timers
from .security import ws_principal

STATIC = Path(__file__).parent / "static"

# While the licence is locked only these work: setup, sign-in, the Licence page and what it needs, and the guides.
OPEN_PREFIXES = ("/static/", "/api/setup", "/api/auth/", "/api/public/", "/api/admin/licence", "/guide/", "/api/guide/")
OPEN_PATHS = {"/", "/setup", "/favicon.ico", "/apple-touch-icon.png", "/api/health", "/api/bootstrap", "/api/licence/status"}


class LicenceGate:
    """Everything but the Licence page stops while AT-SUIT is locked (no key, or a lapsed one):
    the API answers 402, pages show "not licensed" (and reload by themselves once a key is in),
    and live connections are refused."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES) or not licence.locked_cached():
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 4402})
        if scope["method"] == "GET" and not path.startswith("/api/"):
            body, ctype, status = (STATIC / "locked.html").read_bytes(), b"text/html; charset=utf-8", 200
        else:
            body = json.dumps({"detail": "AT-SUIT isn't licensed. An admin can add the licence key in Licence."}).encode()
            ctype, status = b"application/json", 402
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", ctype), (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-cache")]})
        await send({"type": "http.response.body", "body": body})


async def housekeeping() -> None:
    while True:
        try:
            with db.tx() as c:
                c.execute("DELETE FROM sessions WHERE expires_at < ?", (datetime.now(timezone.utc).isoformat(),))
                days = int(db.get_setting(c, "message_retention_days", 0) or 0)
                if days:
                    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
                    from .modules.comms import remove_files  # the files go with their messages
                    remove_files(c, "m.created_at < ?", (cutoff,))
                    c.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
                c.execute("DELETE FROM node_commands WHERE status!='queued' AND created_at < ?",
                          ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),))
        except Exception as exc:  # never let housekeeping kill the server
            print("housekeeping:", exc)
        await asyncio.sleep(3600)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    db.migrate()
    with db.tx() as c:
        licence.prepare(c)
    licence.forget()
    asr.engine = asr.Engine()
    captions.rooms.clear()
    cluster.reset()
    pairing.reset()
    tasks = [asyncio.create_task(housekeeping()), asyncio.create_task(captions.load_engine()),
             asyncio.create_task(timers.end_actions()), asyncio.create_task(updates.loop()),
             asyncio.create_task(licence.watch())]
    yield
    for t in tasks:
        t.cancel()


def create_app() -> FastAPI:
    if config.cfg.role == "helper":
        from .helper import create_helper_app
        return create_helper_app()
    app = FastAPI(title="AT-SUIT", version=VERSION, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
    for module in (core, comms, timers, fleet, pairing, captions, cluster, overlays, dashboard, presenter, imports):
        app.include_router(module.router)
    app.include_router(fleet.legacy)
    app.include_router(updates.router)
    app.include_router(captions.ws_router)
    app.include_router(cluster.ws_router)
    app.include_router(timers.public)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.add_middleware(LicenceGate)

    @app.get("/api/licence/status", include_in_schema=False)
    def licence_status():
        # For the "not licensed" page: it reloads once this says unlocked.
        return {"locked": licence.locked_cached()}

    # Browsers and kiosks ask for these at the site root.
    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC / "brand" / "favicon.ico", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/apple-touch-icon.png", include_in_schema=False)
    def touch_icon():
        return FileResponse(STATIC / "brand" / "apple-touch-icon.png", headers={"Cache-Control": "public, max-age=86400"})

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        p = ws_principal(ws)
        wanted = {t for t in ws.query_params.get("topics", "").split(",") if t}
        await ws.accept()
        allowed = {t for t in wanted if can_subscribe(p, t)}
        await hub.add(ws, allowed)
        await ws.send_text(json.dumps({"type": "hello", "topics": sorted(allowed), "signed_in": bool(p)}))
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                if msg.get("type") == "subscribe":
                    extra = {t for t in msg.get("topics", []) if can_subscribe(p, t)}
                    await hub.update(ws, extra)
                    await ws.send_text(json.dumps({"type": "subscribed", "topics": sorted(extra)}))
                elif msg.get("type") == "ping":
                    await ws.send_text('{"type":"pong"}')
        except (WebSocketDisconnect, ValueError, RuntimeError):
            pass
        finally:
            await hub.remove(ws)

    def page(name: str):
        return FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})

    @app.get("/", include_in_schema=False)
    def index():
        with db.ro() as c:
            if not core.is_setup(c):
                return RedirectResponse("/setup")
        return page("app.html")

    @app.get("/setup", include_in_schema=False)
    def setup_page():
        return page("setup.html")

    @app.get("/node", include_in_schema=False)
    def node_page():
        return page("node.html")

    @app.get("/screen", include_in_schema=False)
    def screen_page():
        return page("screen.html")

    @app.get("/guide/tech", include_in_schema=False)
    def tech_card_page():
        # The printable tech quick-start card.
        return page("quickstart.html")

    @app.get("/guide/companion", include_in_schema=False)
    def companion_guide_page():
        return page("guide.html")

    @app.get("/api/guide/companion", include_in_schema=False)
    def companion_guide():
        # One source: docs/COMPANION.md (copied next to the app in the Docker image).
        for f in (Path(__file__).parent / "companion.md", Path(__file__).resolve().parents[2] / "docs" / "COMPANION.md"):
            if f.is_file():
                return PlainTextResponse(f.read_text("utf-8"), media_type="text/markdown; charset=utf-8")
        raise HTTPException(404, "Guide not found")

    @app.get("/screentest", include_in_schema=False)
    def screentest_page():
        # Test patterns for any display; no sign-in, nothing to leak.
        return page("screentest.html")

    @app.get("/present/{token}", include_in_schema=False)
    def presenter_portal(token: str):
        return page("present.html")

    @app.get("/timer/{room_id}", include_in_schema=False)
    def timer_page(room_id: int, view: str = ""):
        # Backstage is a studio clock with the stage timer, cues and help calls: a page of its own.
        # Speaker preview is the same page with the clock and the site's help calls and crew notices, no timer.
        return page("backstage.html" if view in ("backstage", "preview") else "timer.html")

    @app.get("/captions/{room_id}", include_in_schema=False)
    def captions_page(room_id: int):
        return page("captions.html")

    @app.get("/captions/{room_id}/overlay", include_in_schema=False)
    def captions_overlay(room_id: int):
        return page("captions.html")

    return app


app = create_app()
