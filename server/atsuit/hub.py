"""One WebSocket per client, with topic subscriptions.

Topics: site:<id>, room:<id>, dm:<account_id>, timer:<room_id>,
captions:<room_id>, fleet, help. Timer and caption topics are public so
kiosk and audience screens work without signing in.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket

from .security import Principal

PUBLIC_PREFIXES = ("timer:", "captions:")


def can_subscribe(p: Principal | None, topic: str) -> bool:
    if topic.startswith(PUBLIC_PREFIXES):
        return True
    if p is None:
        return False
    if topic.startswith("dm:"):
        return p.kind == "account" and topic == f"dm:{p.id}"
    if topic in ("fleet", "audit", "presenter"):
        return p.at_least("tech")
    if p.site_id is None:
        return True
    kind, _, ident = topic.partition(":")
    if kind == "site":
        return ident == str(p.site_id)
    if kind == "room":
        from . import db

        with db.ro() as c:
            r = c.execute("SELECT site_id FROM rooms WHERE id=?", (ident,)).fetchone()
        return bool(r) and r["site_id"] == p.site_id
    return True


class Hub:
    def __init__(self) -> None:
        self._subs: dict[WebSocket, set[str]] = {}
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket, topics: set[str]) -> None:
        async with self._lock:
            self._subs[ws] = topics

    async def update(self, ws: WebSocket, topics: set[str]) -> None:
        async with self._lock:
            if ws in self._subs:
                self._subs[ws] |= topics

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self._subs.pop(ws, None)

    def count(self, topic: str | None = None) -> int:
        if topic is None:
            return len(self._subs)
        return sum(1 for t in self._subs.values() if topic in t)

    async def publish(self, topic: str, type_: str, data: Any) -> None:
        text = json.dumps({"topic": topic, "type": type_, "data": data})
        async with self._lock:
            targets = [ws for ws, t in self._subs.items() if topic in t]
        dead = []
        for ws in targets:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._subs.pop(ws, None)


hub = Hub()
