"""Live captions. A node streams its microphone to /ws/audio/<room>; the server
recognises speech and fans captions out to every page watching that room.
Replaces AT-LiveCaption, with the model running once on the server.

What LiveCaption did on the caption laptop now happens here, per room: mic
gain and graphic EQ, the level meter and analyser, custom vocabulary with a
boost strength, the corrections list, caption history with word confidence,
transcripts you can start and stop at any time (with SRT/VTT export) and the
appearance of every caption screen (audience, overlay, subtitle bar), pushed
live to the screens."""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .. import asr, config, db, dsp
from ..hub import hub
from ..security import Principal, require_admin, require_tech, require_user, ws_principal
from . import cluster
from .core import modules_enabled, require_module, room_or_404, site_ok

router = APIRouter(dependencies=[Depends(require_module("captions"))])
ws_router = APIRouter()

HISTORY_SIZE = 200        # finals kept per room for the Corrections tab
MAX_CORRECTIONS = 500
MAX_LINES_SAFETY_CAP = 20
SURFACES = ("audience", "overlay", "bar")
FONT_STACK = "'Saira', 'Segoe UI', system-ui, -apple-system, sans-serif"

# LiveCaption's appearance defaults; "bar" is the subtitle bar AT-SUIT adds
# for LED strips and under-stage screens. font_size is in px on a 1920-wide
# screen and scales with the screen's width.
APPEARANCE_DEFAULTS: dict[str, dict] = {
    "audience": {"font_family": FONT_STACK, "font_size": 64, "font_weight": 700, "text_align": "center",
                 "line_height": 1.3, "text_color": "#ffffff", "background_color": "#000000",
                 "background_opacity": 1.0, "page_color": "#000000", "max_lines": 3, "position": "bottom",
                 "hold_seconds": 8, "fade_seconds": 1.5, "show_disclaimer": False},
    "overlay": {"font_family": FONT_STACK, "font_size": 44, "font_weight": 700, "text_align": "center",
                "line_height": 1.2, "text_color": "#ffffff", "background_color": "#000000",
                "background_opacity": 0.6, "page_color": "transparent", "max_lines": 2, "position": "bottom",
                "hold_seconds": 8, "fade_seconds": 1.5, "show_disclaimer": False},
    "bar": {"font_family": FONT_STACK, "font_size": 56, "font_weight": 700, "text_align": "left",
            "line_height": 1.2, "text_color": "#ffffff", "background_color": "#0B1020",
            "background_opacity": 1.0, "page_color": "#000000", "max_lines": 2, "position": "bottom",
            "hold_seconds": 10, "fade_seconds": 1.0, "show_disclaimer": False},
}
COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|transparent)$")
FONT = re.compile(r"^[A-Za-z0-9 ,'\"_-]{1,200}$")

AUDIO_DEFAULTS = {"gain_db": 0.0, "eq_band_gains_db": [0.0] * len(dsp.EQ_BANDS_HZ),
                  "music_label": True, "join_acronyms": True, "sentence_case": False}


class RoomState:
    def __init__(self) -> None:
        self.source: str = ""
        self.ws: WebSocket | None = None
        self.session = None
        self.started: float = 0
        self.level: float = -100.0
        self.finals: deque[str] = deque(maxlen=8)
        self.finals_at: deque[float] = deque(maxlen=8)  # when each final was said, so a screen that reloads doesn't show old lines as new
        self.history: deque[dict] = deque(maxlen=HISTORY_SIZE)
        self.partial: str = ""
        self.transcript = None
        self.transcript_id: int | None = None
        self.auto_transcript = False  # started by the room's "record" switch, ends with the mic
        self.eq = dsp.GraphicEQ()
        self.options: dict = {"music_label": True, "join_acronyms": True, "sentence_case": False}


rooms: dict[int, RoomState] = {}


def engine_status() -> dict:
    s = asr.engine.status()
    s["active_rooms"] = sum(1 for r in rooms.values() if r.ws)
    s["max_rooms"] = cluster.capacity()
    s["helpers"] = sum(1 for h in cluster.links.values() if h.ready)
    if s["state"] != "ready" and s["helpers"]:  # a helper is doing the listening
        s.update(state="ready", detail="")
    return s


def vocab_lines(text: str) -> list[str]:
    return [w.strip() for w in (text or "").replace(",", "\n").splitlines() if w.strip()]


def all_vocabulary() -> list[str]:
    with db.ro() as c:
        words: list[str] = []
        for r in c.execute("SELECT vocabulary FROM caption_rooms WHERE enabled=1"):
            words += vocab_lines(r["vocabulary"])
    return list(dict.fromkeys(words))


def hotwords_score(c) -> float:
    return float(db.get_setting(c, "captions.hotwords_score", asr.DEFAULT_HOTWORDS_SCORE) or asr.DEFAULT_HOTWORDS_SCORE)


async def load_engine() -> None:
    with db.ro() as c:
        score = hotwords_score(c)
    vocab = all_vocabulary()
    await cluster.push_config(vocab, score)
    await asyncio.to_thread(asr.engine.load, vocab, True, score)
    await hub.publish("fleet", "captions.engine", engine_status())


def room_settings(c, room_id: int) -> dict:
    r = c.execute("SELECT * FROM caption_rooms WHERE room_id=?", (room_id,)).fetchone()
    return dict(r) if r else {"room_id": room_id, "enabled": 1, "vocabulary": "", "record": 0}


# ------------------------------------------------------ room configuration --
def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def room_config(c, room_id: int) -> dict:
    """Audio and appearance settings for a room (kept in the settings table)."""
    saved = db.get_setting(c, f"captions.room.{room_id}", {}) or {}
    cfg = _merge(json.loads(json.dumps({**AUDIO_DEFAULTS, "appearance": APPEARANCE_DEFAULTS})), saved)
    bands = list(cfg.get("eq_band_gains_db") or [])[:len(dsp.EQ_BANDS_HZ)]
    cfg["eq_band_gains_db"] = bands + [0.0] * (len(dsp.EQ_BANDS_HZ) - len(bands))
    return cfg


def clean_appearance(surface: str, values: dict) -> dict:
    """Validate one surface's appearance fields; unknown keys are dropped."""
    out: dict = {}
    for key, v in (values or {}).items():
        if key not in APPEARANCE_DEFAULTS[surface]:
            continue
        try:
            if key == "font_family":
                if not FONT.match(str(v)):
                    raise ValueError
                out[key] = str(v)
            elif key in ("text_color", "background_color", "page_color"):
                if not COLOR.match(str(v)):
                    raise ValueError
                out[key] = str(v)
            elif key == "text_align":
                if v not in ("left", "center", "right"):
                    raise ValueError
                out[key] = v
            elif key == "position":
                if v not in ("top", "center", "bottom"):
                    raise ValueError
                out[key] = v
            elif key == "show_disclaimer":
                out[key] = bool(v)
            elif key == "font_size":
                out[key] = int(dsp.clamp(float(v), 12, 400))
            elif key == "font_weight":
                out[key] = int(dsp.clamp(round(float(v) / 100) * 100, 100, 900))
            elif key == "line_height":
                out[key] = round(dsp.clamp(float(v), 0.8, 3.0), 2)
            elif key == "background_opacity":
                out[key] = round(dsp.clamp(float(v), 0.0, 1.0), 2)
            elif key == "max_lines":
                out[key] = int(dsp.clamp(int(v), 1, MAX_LINES_SAFETY_CAP))
            elif key in ("hold_seconds", "fade_seconds"):
                out[key] = round(dsp.clamp(float(v), 0.0, 120.0), 1)
        except (TypeError, ValueError):
            raise HTTPException(422, f"{surface}: {key.replace('_', ' ')} isn't valid")
    return out


def apply_room_config(room_id: int, cfg: dict) -> RoomState:
    st = rooms.setdefault(room_id, RoomState())
    st.eq.set(gain_db=cfg["gain_db"], band_gains_db=cfg["eq_band_gains_db"])
    st.options.update(music_label=bool(cfg["music_label"]), join_acronyms=bool(cfg["join_acronyms"]),
                      sentence_case=bool(cfg["sentence_case"]))
    return st


# ------------------------------------------------------------- transcripts --
def _start_transcript(st: RoomState, room_id: int) -> str:
    """Always a fresh file (LiveCaption: Start never resumes the old one)."""
    _stop_transcript(st, room_id)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"room{room_id}-{stamp}.txt"
    n = 1
    while (config.cfg.transcripts / name).exists():
        n += 1
        name = f"room{room_id}-{stamp}-{n}.txt"
    config.cfg.transcripts.mkdir(parents=True, exist_ok=True)
    st.transcript = (config.cfg.transcripts / name).open("a", encoding="utf-8")
    with db.tx() as c:
        st.transcript_id = c.execute("INSERT INTO transcripts(room_id,started_at,path) VALUES(?,?,?)",
                                     (room_id, db.now_iso(), name)).lastrowid
    return name


def _stop_transcript(st: RoomState, room_id: int) -> None:
    if st.transcript:
        st.transcript.close()
        st.transcript = None
        with db.tx() as c:
            c.execute("UPDATE transcripts SET ended_at=? WHERE room_id=? AND ended_at IS NULL", (db.now_iso(), room_id))
    st.transcript_id = None
    st.auto_transcript = False


def _write_final(st: RoomState, text: str) -> None:
    """Only finalised caption text reaches disk: never audio, never partials."""
    if st.transcript and text.strip():
        st.transcript.write(f"[{datetime.now().strftime('%H:%M:%S')}] {text}\n")
        st.transcript.flush()


def _parse_lines(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        end = line.find("]")
        if line.startswith("[") and end != -1 and line[end + 1:].strip():
            out.append((line[1:end], line[end + 1:].strip()))
    return out


def _estimate_duration(text: str) -> float:
    return max(1.5, min(6.0, max(1, len(text.split())) / 2.5))  # ~150 wpm


def _srt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _blocks(text: str, ts) -> list[str]:
    lines = _parse_lines(text)
    if not lines:
        return []
    times = []
    for stamp, _ in lines:
        try:
            times.append(datetime.strptime(stamp, "%H:%M:%S"))
        except ValueError:
            times.append(times[-1] if times else datetime.strptime("00:00:00", "%H:%M:%S"))
    starts = [(t - times[0]).total_seconds() for t in times]
    for i in range(1, len(starts)):
        while starts[i] < starts[i - 1]:
            starts[i] += 86400  # the session crossed midnight
    out = []
    for i, (start, (_, caption)) in enumerate(zip(starts, lines)):
        end = starts[i + 1] if i + 1 < len(starts) else start + _estimate_duration(caption)
        out.append(f"{i + 1}\n{ts(start)} --> {ts(max(end, start + 0.5))}\n{caption}\n")
    return out


def to_srt(text: str) -> str:
    return "\n".join(_blocks(text, _srt_ts))


def to_vtt(text: str) -> str:
    blocks = _blocks(text, lambda s: _srt_ts(s).replace(",", "."))
    return "WEBVTT\n\n" + "\n".join(blocks) if blocks else "WEBVTT\n"


# ------------------------------------------------------------------ audio --
@ws_router.websocket("/ws/audio/{room_id}")
async def audio_in(ws: WebSocket, room_id: int):
    p = ws_principal(ws)
    with db.ro() as c:
        enabled = modules_enabled(c).get("captions")
        room = c.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
        settings = room_settings(c, room_id) if room else {}
        cfg = room_config(c, room_id) if room else {}
    if not enabled or not p or not p.at_least("tech") or not room or not site_ok(p, room["site_id"]):
        await ws.close(code=4403)
        return
    await ws.accept()
    if not settings.get("enabled"):
        await ws.send_json({"type": "error", "message": "Captions are off for this room"})
        await ws.close(code=4000)
        return
    st = apply_room_config(room_id, cfg)
    active = sum(1 for r in rooms.values() if r.ws)
    if not st.ws and active >= cluster.capacity():
        await ws.send_json({"type": "error", "message": "The server is captioning as many rooms as it can"})
        await ws.close(code=4001)
        return
    try:
        session = await cluster.start_session(room_id, st.options)
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
    st.ws, st.source, st.started, st.session = ws, p.name, time.time(), session
    if settings.get("record") and not st.transcript:
        _start_transcript(st, room_id)
        st.auto_transcript = True
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
            samples = st.eq.process(dsp.pcm16_to_float(data))
            try:
                events = await session.feed(samples)
            except RuntimeError as exc:  # its helper went and nothing else has room
                await ws.send_json({"type": "error", "message": str(exc)})
                await ws.close(code=4002)
                break
            now = time.time()
            if now - last_level > 0.25:
                last_level = now
                st.level = dsp.dbfs(samples)
                await hub.publish(f"captions:{room_id}", "level", {"db": st.level, "rta": dsp.compute_rta_bands(samples)})
            for evt in events:
                await caption_event(room_id, st, evt["type"], evt["text"], evt.get("words") or [])
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
        if st.ws is ws:
            st.ws, st.source, st.partial, st.session = None, "", "", None
            if st.auto_transcript:  # one started by hand keeps going until Stop
                _stop_transcript(st, room_id)
            await hub.publish(f"captions:{room_id}", "status", {"live": False, "source": ""})


async def caption_event(room_id: int, st: RoomState, kind: str, text: str, words: list[dict] | None = None) -> None:
    if kind == "final":
        st.finals.append(text)
        st.finals_at.append(time.time())
        st.history.append({"id": f"{time.time():.3f}", "text": text, "words": words or [], "ts": time.time()})
        st.partial = ""
        _write_final(st, text)
    else:
        st.partial = text
    await hub.publish(f"captions:{room_id}", kind, {"text": text, "words": words or []})


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
                        "level": st.level if st else -100, "recording": bool(st and st.transcript)})
    return {"engine": engine_status(), "rooms": out}


@router.get("/api/captions/{room_id}/recent")
def recent(room_id: int):
    """Public: lets a caption screen catch up after a refresh, with its looks."""
    st = rooms.get(room_id)
    with db.ro() as c:
        appearance = room_config(c, room_id)["appearance"]
    return {"finals": list(st.finals) if st else [], "finals_at": list(st.finals_at) if st else [], "now": time.time(),
            "partial": st.partial if st else "", "live": bool(st and st.ws), "appearance": appearance}


@router.get("/api/captions/{room_id}/appearance")
def appearance(room_id: int):
    """Public: how this room's caption screens look (audience, overlay, bar)."""
    with db.ro() as c:
        room_or_404(c, room_id)
        return room_config(c, room_id)["appearance"]


def _settings_out(c, room_id: int) -> dict:
    cfg = room_config(c, room_id)
    st = rooms.get(room_id)
    return {**room_settings(c, room_id), **dsp.settings_out(), "gain_db": cfg["gain_db"],
            "eq_band_gains_db": cfg["eq_band_gains_db"], "music_label": cfg["music_label"],
            "join_acronyms": cfg["join_acronyms"], "sentence_case": cfg["sentence_case"], "appearance": cfg["appearance"],
            "appearance_defaults": APPEARANCE_DEFAULTS, "hotwords_score": hotwords_score(c),
            "skipped_vocabulary": asr.engine.skipped_vocab, "live": bool(st and st.ws),
            "source": st.source if st else "", "recording": bool(st and st.transcript),
            "transcript_id": st.transcript_id if st else None}


@router.get("/api/captions/{room_id}/settings")
def get_settings(room_id: int, p: Principal = Depends(require_tech)):
    with db.ro() as c:
        room_or_404(c, room_id, p)
        return _settings_out(c, room_id)


class SettingsIn(BaseModel):
    """Every field is optional; anything left out keeps its value."""
    enabled: bool | None = None
    record: bool | None = None
    vocabulary: str | None = Field(None, max_length=20000)
    hotwords_score: float | None = Field(None, ge=0.5, le=6.0)
    gain_db: float | None = Field(None, ge=-dsp.MAX_GAIN_DB, le=dsp.MAX_GAIN_DB)
    eq_band_gains_db: list[float] | None = Field(None, max_length=len(dsp.EQ_BANDS_HZ))
    eq_bands: list[dict] | None = None  # LiveCaption style: [{"index": 3, "gain_db": 4}]
    music_label: bool | None = None
    join_acronyms: bool | None = None
    sentence_case: bool | None = None
    appearance: dict | None = None  # {"audience": {...}, "overlay": {...}, "bar": {...}}
    reset_appearance: str | None = Field(None, pattern="^(audience|overlay|bar|all)$")


async def save_settings(room_id: int, body: SettingsIn, p: Principal) -> dict:
    reload_engine = False
    with db.tx() as c:
        room_or_404(c, room_id, p)
        before = room_settings(c, room_id)
        saved = db.get_setting(c, f"captions.room.{room_id}", {}) or {}
        cfg = room_config(c, room_id)
        if body.gain_db is not None:
            saved["gain_db"] = round(body.gain_db, 1)
        bands = list(cfg["eq_band_gains_db"])
        if body.eq_band_gains_db is not None:
            bands = [dsp.clamp(g, -dsp.MAX_BAND_GAIN_DB, dsp.MAX_BAND_GAIN_DB) for g in body.eq_band_gains_db]
            bands += [0.0] * (len(dsp.EQ_BANDS_HZ) - len(bands))
        for b in body.eq_bands or []:
            i = int(b.get("index", -1))
            if 0 <= i < len(bands):
                bands[i] = dsp.clamp(float(b.get("gain_db", 0)), -dsp.MAX_BAND_GAIN_DB, dsp.MAX_BAND_GAIN_DB)
        if body.eq_band_gains_db is not None or body.eq_bands:
            saved["eq_band_gains_db"] = [round(g, 1) for g in bands]
        for key in ("music_label", "join_acronyms", "sentence_case"):
            if getattr(body, key) is not None:
                saved[key] = getattr(body, key)
        appearance_changed = False
        if body.reset_appearance:
            for s in (SURFACES if body.reset_appearance == "all" else (body.reset_appearance,)):
                (saved.get("appearance") or {}).pop(s, None)
            appearance_changed = True
        for surface, values in (body.appearance or {}).items():
            if surface not in SURFACES or not isinstance(values, dict):
                raise HTTPException(422, f"Unknown caption surface {surface}")
            saved.setdefault("appearance", {}).setdefault(surface, {}).update(clean_appearance(surface, values))
            appearance_changed = True
        db.set_setting(c, f"captions.room.{room_id}", saved)
        if any(v is not None for v in (body.enabled, body.record, body.vocabulary)):
            enabled = before["enabled"] if body.enabled is None else int(body.enabled)
            record = before["record"] if body.record is None else int(body.record)
            vocab = before["vocabulary"] if body.vocabulary is None else body.vocabulary
            c.execute(
                "INSERT INTO caption_rooms(room_id,enabled,vocabulary,record) VALUES(?,?,?,?) ON CONFLICT(room_id) "
                "DO UPDATE SET enabled=excluded.enabled, vocabulary=excluded.vocabulary, record=excluded.record",
                (room_id, enabled, vocab, record))
            reload_engine = vocab_lines(vocab) != vocab_lines(before["vocabulary"]) or bool(enabled) != bool(before["enabled"])
        if body.hotwords_score is not None and abs(body.hotwords_score - hotwords_score(c)) > 1e-6:
            db.set_setting(c, "captions.hotwords_score", round(body.hotwords_score, 2))
            reload_engine = True
        db.audit(c, p.name, "captions.settings", f"room {room_id}")
        cfg = room_config(c, room_id)
        out = _settings_out(c, room_id)
    apply_room_config(room_id, cfg)
    if appearance_changed:
        await hub.publish(f"captions:{room_id}", "appearance", cfg["appearance"])
    if reload_engine and (asr.engine.state in ("ready", "error") or cluster.links):
        asyncio.get_running_loop().create_task(load_engine())
    return out


@router.put("/api/captions/{room_id}/settings")
async def put_settings(room_id: int, body: SettingsIn, p: Principal = Depends(require_tech)):
    """Any tech can tune their room: audio, vocabulary and how the screens look."""
    return await save_settings(room_id, body, p)


class CaptionRoomIn(BaseModel):
    enabled: bool = True
    vocabulary: str = Field(default="", max_length=20000)
    record: bool = False


@router.put("/api/captions/rooms/{room_id}")
async def set_room(room_id: int, body: CaptionRoomIn, p: Principal = Depends(require_tech)):
    await save_settings(room_id, SettingsIn(enabled=body.enabled, vocabulary=body.vocabulary, record=body.record), p)
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
    words = [{"text": w, "confidence": 1.0} for w in body.text.split()]  # typed, so not "unsure"
    await caption_event(room_id, st, "final" if body.final else "partial", body.text, words)
    return {"ok": True}


@router.post("/api/captions/{room_id}/clear")
async def clear(room_id: int, p: Principal = Depends(require_tech)):
    st = rooms.get(room_id)
    if st:
        st.finals.clear()
        st.finals_at.clear()
        st.partial = ""
    await hub.publish(f"captions:{room_id}", "clear", {})
    return {"ok": True}


# ------------------------------------------------- history and corrections --
@router.get("/api/captions/{room_id}/history")
def history(room_id: int, p: Principal = Depends(require_tech)):
    """The room's last 200 finals with each word's confidence, newest first."""
    with db.ro() as c:
        room_or_404(c, room_id, p)
    st = rooms.get(room_id)
    return list(reversed(st.history)) if st else []


@router.post("/api/captions/{room_id}/history/clear")
def clear_history(room_id: int, p: Principal = Depends(require_tech)):
    with db.ro() as c:
        room_or_404(c, room_id, p)
    st = rooms.get(room_id)
    if st:
        st.history.clear()
    return {"ok": True}


@router.get("/api/captions/{room_id}/corrections")
def corrections(room_id: int, p: Principal = Depends(require_tech)):
    with db.ro() as c:
        room_or_404(c, room_id, p)
        return list(reversed(db.get_setting(c, f"captions.corrections.{room_id}", []) or []))


class CorrectionIn(BaseModel):
    original: str = Field(min_length=1, max_length=200)
    corrected: str = Field(min_length=1, max_length=200)


def add_correction(c, room_id: int, original: str, corrected: str, by: str) -> bool:
    """Log the fix and add the right word to the room's vocabulary, so it is
    boosted next time (it doesn't retrain the model). True if the
    vocabulary changed."""
    entries = db.get_setting(c, f"captions.corrections.{room_id}", []) or []
    entries.append({"original": original, "corrected": corrected, "ts": time.time(), "by": by})
    db.set_setting(c, f"captions.corrections.{room_id}", entries[-MAX_CORRECTIONS:])
    settings = room_settings(c, room_id)
    words = vocab_lines(settings["vocabulary"])
    if corrected.lower() in (w.lower() for w in words):
        return False
    vocab = "\n".join(words + [corrected])
    c.execute(
        "INSERT INTO caption_rooms(room_id,enabled,vocabulary,record) VALUES(?,?,?,?) ON CONFLICT(room_id) "
        "DO UPDATE SET vocabulary=excluded.vocabulary", (room_id, settings["enabled"], vocab, settings["record"]))
    return True


@router.post("/api/captions/{room_id}/corrections")
async def correct(room_id: int, body: CorrectionIn, p: Principal = Depends(require_tech)):
    original, corrected = " ".join(body.original.split()), " ".join(body.corrected.split())
    if not original or not corrected:
        raise HTTPException(422, "Both the misheard and the right word are needed")
    with db.tx() as c:
        room_or_404(c, room_id, p)
        added = add_correction(c, room_id, original, corrected, p.name)
        db.audit(c, p.name, "captions.correction", f"room {room_id}: {original} -> {corrected}")
    if added and (asr.engine.state in ("ready", "error") or cluster.links):
        # Rooms already captioning keep going and pick up the new word at their next pause.
        asyncio.get_running_loop().create_task(load_engine())
    return {"ok": True, "added_to_vocabulary": added}


# ------------------------------------------------------------- transcripts --
@router.post("/api/captions/{room_id}/transcript/start")
async def transcript_start(room_id: int, p: Principal = Depends(require_tech)):
    """Start saving this room's captions now, in a new file (even mid-session)."""
    with db.ro() as c:
        room_or_404(c, room_id, p)
    st = rooms.setdefault(room_id, RoomState())
    name = _start_transcript(st, room_id)
    await hub.publish(f"captions:{room_id}", "recording", {"on": True, "filename": name})
    return {"ok": True, "filename": name, "id": st.transcript_id}


@router.post("/api/captions/{room_id}/transcript/stop")
async def transcript_stop(room_id: int, p: Principal = Depends(require_tech)):
    with db.ro() as c:
        room_or_404(c, room_id, p)
    st = rooms.get(room_id)
    if st:
        _stop_transcript(st, room_id)
    await hub.publish(f"captions:{room_id}", "recording", {"on": False})
    return {"ok": True}


@router.get("/api/captions/transcripts")
def transcripts(p: Principal = Depends(require_tech), room_id: int | None = None):
    with db.ro() as c:
        rows = db.rows(c.execute(
            "SELECT t.*, r.name AS room, r.site_id FROM transcripts t LEFT JOIN rooms r ON r.id=t.room_id "
            "WHERE (? IS NULL OR t.room_id=?) ORDER BY t.id DESC LIMIT 500", (room_id, room_id)))
    out = []
    for t in rows:
        if not site_ok(p, t.pop("site_id")):
            continue
        f = config.cfg.transcripts / t["path"]
        t["size"] = f.stat().st_size if f.is_file() else 0
        out.append(t)
    return out


def _transcript(tid: int, p: Principal):
    with db.ro() as c:
        t = c.execute("SELECT t.*, r.site_id FROM transcripts t LEFT JOIN rooms r ON r.id=t.room_id WHERE t.id=?",
                      (tid,)).fetchone()
    if not t or not site_ok(p, t["site_id"]):
        raise HTTPException(404, "Transcript not found")
    path = config.cfg.transcripts / t["path"]
    if not path.is_file():
        raise HTTPException(404, "Transcript file is missing")
    return t, path


@router.get("/api/captions/transcripts/{tid}")
def transcript_file(tid: int, p: Principal = Depends(require_tech)):
    t, path = _transcript(tid, p)
    return FileResponse(path, media_type="text/plain", filename=t["path"])


@router.get("/api/captions/transcripts/{tid}/export.{fmt}")
def transcript_export(tid: int, fmt: str, p: Principal = Depends(require_tech)):
    """Subtitles from a saved transcript: each line runs until the next one."""
    if fmt not in ("srt", "vtt"):
        raise HTTPException(404)
    t, path = _transcript(tid, p)
    text = path.read_text(encoding="utf-8", errors="replace")
    name = t["path"].rsplit(".", 1)[0] + "." + fmt
    body, media = (to_srt(text), "application/x-subrip") if fmt == "srt" else (to_vtt(text), "text/vtt")
    return PlainTextResponse(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.delete("/api/captions/transcripts/{tid}")
async def transcript_delete(tid: int, p: Principal = Depends(require_tech)):
    t, path = _transcript(tid, p)
    for st in rooms.values():
        if st.transcript_id == tid:
            raise HTTPException(409, "That transcript is still recording; stop it first")
    path.unlink(missing_ok=True)
    with db.tx() as c:
        c.execute("DELETE FROM transcripts WHERE id=?", (tid,))
        db.audit(c, p.name, "captions.transcript_delete", t["path"])
    return {"ok": True}
