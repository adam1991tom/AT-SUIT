"""Live captions. A node streams its microphone to /ws/audio/<room>; the server
recognises speech and fans captions out to every page watching that room.
Replaces AT-LiveCaption, with the model running once on the server."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import asr, config, db
from ..hub import hub
from ..security import Principal, require_admin, require_tech, require_user, ws_principal
from .core import modules_enabled, require_module, room_or_404, site_ok

router = APIRouter(dependencies=[Depends(require_module("captions"))])
ws_router = APIRouter()


class RoomState:
    def __init__(self) -> None:
        self.source: str = ""
        self.ws: WebSocket | None = None
        self.started: float = 0
        self.level: float = -100.0
        self.finals: deque[str] = deque(maxlen=8)
        self.partial: str = ""
        self.transcript = None


rooms: dict[int, RoomState] = {}


def engine_status() -> dict:
    s = asr.engine.status()
    s["active_rooms"] = sum(1 for r in rooms.values() if r.ws)
    s["max_rooms"] = config.cfg.asr_max_rooms
    return s


def all_vocabulary() -> list[str]:
    with db.ro() as c:
        words: list[str] = []
        for r in c.execute("SELECT vocabulary FROM caption_rooms WHERE enabled=1"):
            words += [w for w in r["vocabulary"].replace(",", "\n").splitlines() if w.strip()]
    return words


async def load_engine() -> None:
    await asyncio.to_thread(asr.engine.load, all_vocabulary())
    await hub.publish("fleet", "captions.engine", engine_status())


def room_settings(c, room_id: int) -> dict:
    r = c.execute("SELECT * FROM caption_rooms WHERE room_id=?", (room_id,)).fetchone()
    return dict(r) if r else {"room_id": room_id, "enabled": 1, "vocabulary": "", "record": 0}


# ------------------------------------------------------------------ audio --
@ws_router.websocket("/ws/audio/{room_id}")
async def audio_in(ws: WebSocket, room_id: int):
    p = ws_principal(ws)
    with db.ro() as c:
        enabled = modules_enabled(c).get("captions")
        room = c.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
        settings = room_settings(c, room_id) if room else {}
    if not enabled or not p or not p.at_least("tech") or not room or not site_ok(p, room["site_id"]):
        await ws.close(code=4403)
        return
    await ws.accept()
    if not settings.get("enabled"):
        await ws.send_json({"type": "error", "message": "Captions are off for this room"})
        await ws.close(code=4000)
        return
    st = rooms.setdefault(room_id, RoomState())
    active = sum(1 for r in rooms.values() if r.ws)
    if not st.ws and active >= config.cfg.asr_max_rooms:
        await ws.send_json({"type": "error", "message": "The server is captioning as many rooms as it can"})
        await ws.close(code=4001)
        return
    try:
        session = asr.engine.session()
    except RuntimeError as exc:
        await ws.send_json({"type": "error", "message": str(exc)})
        await ws.close(code=4002)
        return
    if st.ws:  # newest source wins, so a tech can take over from another laptop
        try:
            await st.ws.send_json({"type": "replaced", "by": p.name})
            await st.ws.close(code=4010)
        except Exception:
            pass
    st.ws, st.source, st.started = ws, p.name, time.time()
    if settings.get("record"):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = config.cfg.transcripts / f"room{room_id}-{stamp}.txt"
        st.transcript = path.open("a", encoding="utf-8")
        with db.tx() as c:
            c.execute("INSERT INTO transcripts(room_id,started_at,path) VALUES(?,?,?)", (room_id, db.now_iso(), path.name))
    await ws.send_json({"type": "ready", "sample_rate": asr.SAMPLE_RATE})
    await hub.publish(f"captions:{room_id}", "status", {"live": True, "source": p.name})
    last_level = 0.0
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if not data:
                continue
            results = await asyncio.to_thread(session.feed, data)
            now = time.time()
            if now - last_level > 0.25:
                last_level = now
                st.level = asr.level_db(data)
                await hub.publish(f"captions:{room_id}", "level", {"db": st.level})
            for kind, text in results:
                if kind == "final":
                    st.finals.append(text)
                    st.partial = ""
                    if st.transcript:
                        st.transcript.write(f"[{datetime.now().strftime('%H:%M:%S')}] {text}\n")
                        st.transcript.flush()
                else:
                    st.partial = text
                await hub.publish(f"captions:{room_id}", kind, {"text": text})
    except WebSocketDisconnect:
        pass
    finally:
        if st.ws is ws:
            st.ws, st.source, st.partial = None, "", ""
            if st.transcript:
                st.transcript.close()
                st.transcript = None
                with db.tx() as c:
                    c.execute("UPDATE transcripts SET ended_at=? WHERE room_id=? AND ended_at IS NULL", (db.now_iso(), room_id))
            await hub.publish(f"captions:{room_id}", "status", {"live": False, "source": ""})


# -------------------------------------------------------------------- api --
@router.get("/api/captions/status")
def status(p: Principal = Depends(require_user)):
    with db.ro() as c:
        rs = db.rows(c.execute("SELECT id,name,site_id FROM rooms ORDER BY sort,name"))
        out = []
        for r in rs:
            if not site_ok(p, r["site_id"]):
                continue
            st = rooms.get(r["id"])
            out.append({**r, **room_settings(c, r["id"]), "live": bool(st and st.ws), "source": st.source if st else "",
                        "level": st.level if st else -100})
    return {"engine": engine_status(), "rooms": out}


@router.get("/api/captions/{room_id}/recent")
def recent(room_id: int):
    """Public: lets an audience screen catch up after a refresh."""
    st = rooms.get(room_id)
    return {"finals": list(st.finals) if st else [], "partial": st.partial if st else "", "live": bool(st and st.ws)}


class CaptionRoomIn(BaseModel):
    enabled: bool = True
    vocabulary: str = Field(default="", max_length=20000)
    record: bool = False


@router.put("/api/captions/rooms/{room_id}")
async def set_room(room_id: int, body: CaptionRoomIn, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        room_or_404(c, room_id)
        before = room_settings(c, room_id)
        c.execute(
            "INSERT INTO caption_rooms(room_id,enabled,vocabulary,record) VALUES(?,?,?,?) ON CONFLICT(room_id) "
            "DO UPDATE SET enabled=excluded.enabled, vocabulary=excluded.vocabulary, record=excluded.record",
            (room_id, int(body.enabled), body.vocabulary, int(body.record)))
    if before.get("vocabulary", "") != body.vocabulary and asr.engine.state in ("ready", "error"):
        asyncio.get_running_loop().create_task(load_engine())
    return {"ok": True}


@router.post("/api/captions/engine/reload")
async def reload_engine(p: Principal = Depends(require_admin)):
    asyncio.get_running_loop().create_task(load_engine())
    return {"ok": True}


class TestIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    final: bool = True


@router.post("/api/captions/{room_id}/test")
async def test_caption(room_id: int, body: TestIn, p: Principal = Depends(require_tech)):
    """Push text to the room's caption screens, to check them before the show."""
    with db.ro() as c:
        room_or_404(c, room_id, p)
    st = rooms.setdefault(room_id, RoomState())
    if body.final:
        st.finals.append(body.text)
    await hub.publish(f"captions:{room_id}", "final" if body.final else "partial", {"text": body.text})
    return {"ok": True}


@router.post("/api/captions/{room_id}/clear")
async def clear(room_id: int, p: Principal = Depends(require_tech)):
    st = rooms.get(room_id)
    if st:
        st.finals.clear()
        st.partial = ""
    await hub.publish(f"captions:{room_id}", "clear", {})
    return {"ok": True}


@router.get("/api/captions/transcripts")
def transcripts(p: Principal = Depends(require_tech)):
    with db.ro() as c:
        return db.rows(c.execute(
            "SELECT t.*, r.name AS room FROM transcripts t LEFT JOIN rooms r ON r.id=t.room_id ORDER BY t.id DESC LIMIT 500"))


@router.get("/api/captions/transcripts/{tid}")
def transcript_file(tid: int, p: Principal = Depends(require_tech)):
    with db.ro() as c:
        t = c.execute("SELECT * FROM transcripts WHERE id=?", (tid,)).fetchone()
    if not t:
        raise HTTPException(404, "Transcript not found")
    path = config.cfg.transcripts / t["path"]
    if not path.is_file():
        raise HTTPException(404, "Transcript file is missing")
    return FileResponse(path, media_type="text/plain", filename=t["path"])
