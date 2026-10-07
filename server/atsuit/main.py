"""AT-SUIT server: one app, one port, every module."""
from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import VERSION, asr, db
from .hub import can_subscribe, hub
from .modules import captions, comms, core, dashboard, fleet, imports, overlays, timers
from .security import ws_principal

STATIC = Path(__file__).parent / "static"


async def housekeeping() -> None:
    while True:
        try:
            with db.tx() as c:
                c.execute("DELETE FROM sessions WHERE expires_at < ?", (datetime.now(timezone.utc).isoformat(),))
                days = int(db.get_setting(c, "message_retention_days", 0) or 0)
                if days:
                    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
                    c.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
                c.execute("DELETE FROM node_commands WHERE status!='queued' AND created_at < ?",
                          ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),))
        except Exception as exc:  # never let housekeeping kill the server
            print("housekeeping:", exc)
        await asyncio.sleep(3600)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    db.migrate()
    asr.engine = asr.Engine()
    captions.rooms.clear()
    tasks = [asyncio.create_task(housekeeping()), asyncio.create_task(captions.load_engine())]
    yield
    for t in tasks:
        t.cancel()


def create_app() -> FastAPI:
    app = FastAPI(title="AT-SUIT", version=VERSION, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
    for module in (core, comms, timers, fleet, captions, overlays, dashboard, imports):
        app.include_router(module.router)
    app.include_router(fleet.legacy)
    app.include_router(captions.ws_router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

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

    @app.get("/timer/{room_id}", include_in_schema=False)
    def timer_page(room_id: int):
        return page("timer.html")

    @app.get("/captions/{room_id}", include_in_schema=False)
    def captions_page(room_id: int):
        return page("captions.html")

    @app.get("/captions/{room_id}/overlay", include_in_schema=False)
    def captions_overlay(room_id: int):
        return page("captions.html")

    return app


app = create_app()
