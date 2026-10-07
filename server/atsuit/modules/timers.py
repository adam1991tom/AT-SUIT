"""Server-authoritative room countdowns. Clients get the state plus server time
and run the clock locally, so a reconnect never drifts. Stands in for the
per-room Ontime views on kiosks and stage screens."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..hub import hub
from ..security import Principal, require_tech
from .core import require_module, room_or_404

router = APIRouter(dependencies=[Depends(require_module("timers"))])


def _row(c, room_id: int):
    r = c.execute("SELECT * FROM timers WHERE room_id=?", (room_id,)).fetchone()
    if r:
        return r
    c.execute("INSERT INTO timers(room_id,updated_at) VALUES(?,?)", (room_id, db.now_iso()))
    return c.execute("SELECT * FROM timers WHERE room_id=?", (room_id,)).fetchone()


def remaining(r, at: float | None = None) -> int:
    if r["running"] and r["started_at"]:
        return int(r["remaining_ms"] - ((at or time.time()) - r["started_at"]) * 1000)
    return int(r["remaining_ms"])


def state(r, room_name: str = "") -> dict:
    now = time.time()
    return {
        "room_id": r["room_id"], "room": room_name, "title": r["title"], "duration_ms": r["duration_ms"],
        "remaining_ms": remaining(r, now), "running": bool(r["running"]), "message": r["message"],
        "message_visible": bool(r["message_visible"]), "warn_ms": r["warn_ms"], "danger_ms": r["danger_ms"],
        "server_time": now,
    }


@router.get("/api/timers/{room_id}")
def get_timer(room_id: int):
    """Public, so stage screens and kiosks need no sign-in."""
    with db.tx() as c:
        room = room_or_404(c, room_id)
        return state(_row(c, room_id), room["name"])


class TimerAction(BaseModel):
    duration_ms: int | None = Field(default=None, ge=0, le=24 * 3600 * 1000)
    delta_ms: int | None = Field(default=None, ge=-24 * 3600 * 1000, le=24 * 3600 * 1000)
    title: str | None = Field(default=None, max_length=200)
    message: str | None = Field(default=None, max_length=500)
    message_visible: bool | None = None
    warn_ms: int | None = Field(default=None, ge=0)
    danger_ms: int | None = Field(default=None, ge=0)


ACTIONS = ("set", "start", "pause", "toggle", "reset", "add", "message", "thresholds")


@router.post("/api/timers/{room_id}/{action}")
async def timer_action(room_id: int, action: str, body: TimerAction | None = None, p: Principal = Depends(require_tech)):
    body = body or TimerAction()
    if action not in ACTIONS:
        raise HTTPException(404, "Unknown timer action")
    now = time.time()
    with db.tx() as c:
        room = room_or_404(c, room_id, p)
        r = _row(c, room_id)
        left = remaining(r, now)
        running = bool(r["running"])
        fields: dict = {}
        if action == "set":
            dur = body.duration_ms if body.duration_ms is not None else r["duration_ms"]
            fields = {"duration_ms": dur, "remaining_ms": dur, "running": 0, "started_at": None}
            if body.title is not None:
                fields["title"] = body.title
        elif action in ("start", "toggle") and not running and (action == "start" or not running):
            fields = {"running": 1, "started_at": now, "remaining_ms": left}
        elif action in ("pause", "toggle") and running:
            fields = {"running": 0, "started_at": None, "remaining_ms": left}
        elif action == "reset":
            fields = {"running": 0, "started_at": None, "remaining_ms": r["duration_ms"]}
        elif action == "add":
            delta = body.delta_ms or 0
            fields = {"remaining_ms": left + delta, "started_at": now if running else None}
        elif action == "message":
            if body.message is not None:
                fields["message"] = body.message
            if body.message_visible is not None:
                fields["message_visible"] = int(body.message_visible)
        elif action == "thresholds":
            if body.warn_ms is not None:
                fields["warn_ms"] = body.warn_ms
            if body.danger_ms is not None:
                fields["danger_ms"] = body.danger_ms
        if fields:
            fields["updated_at"] = db.now_iso()
            sets = ",".join(f"{k}=?" for k in fields)
            c.execute(f"UPDATE timers SET {sets} WHERE room_id=?", (*fields.values(), room_id))
        out = state(c.execute("SELECT * FROM timers WHERE room_id=?", (room_id,)).fetchone(), room["name"])
    await hub.publish(f"timer:{room_id}", "timer", out)
    return out
