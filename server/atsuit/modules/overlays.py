"""Drives AT-LiveOverlay on laptops through its token-protected HTTP API
(port 8765), so techs and Companion control every overlay from one place."""
from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..security import Principal, decrypt, encrypt, require_admin, require_tech
from .core import require_module, site_ok

router = APIRouter(dependencies=[Depends(require_module("overlays"))])

OVERLAY_ACTIONS = ("show", "hide", "reload", "lock", "unlock", "live", "edit", "close", "seturl", "opacity", "refresh", "rotate")
TIMEOUT = httpx.Timeout(4.0)


class TargetIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=8, max_length=300)
    token: str = ""
    room_id: int | None = None


def _clean_base(url: str) -> str:
    url = url.strip().rstrip("/")
    if not re.match(r"^https?://[A-Za-z0-9.\-]+(:\d+)?$", url):
        raise HTTPException(400, "Use the overlay laptop's address, like http://10.100.70.88:8765")
    return url


def _target(c, target_id: int, p: Principal):
    t = c.execute("SELECT t.*, r.site_id FROM overlay_targets t LEFT JOIN rooms r ON r.id=t.room_id WHERE t.id=?",
                  (target_id,)).fetchone()
    if not t or (t["site_id"] is not None and not site_ok(p, t["site_id"])):
        raise HTTPException(404, "Overlay laptop not found")
    return t


async def _call(t, path: str, params: dict | None = None) -> dict:
    q = {"token": decrypt(t["token_enc"]), **(params or {})}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"{t['base_url']}{path}", params=q)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Couldn't reach {t['name']}: {exc.__class__.__name__}")
    if r.status_code == 401 or r.status_code == 403:
        raise HTTPException(502, f"{t['name']} rejected the API token")
    try:
        return r.json()
    except ValueError:
        return {"status": r.status_code, "text": r.text[:500]}


@router.get("/api/overlays/targets")
def list_targets(p: Principal = Depends(require_tech)):
    with db.ro() as c:
        rows = db.rows(c.execute(
            "SELECT t.id,t.name,t.room_id,t.base_url,r.name AS room,r.site_id FROM overlay_targets t "
            "LEFT JOIN rooms r ON r.id=t.room_id ORDER BY t.name"))
    return [r for r in rows if r["site_id"] is None or site_ok(p, r["site_id"])]


@router.post("/api/overlays/targets")
def add_target(body: TargetIn, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        cur = c.execute("INSERT INTO overlay_targets(name,room_id,base_url,token_enc) VALUES(?,?,?,?)",
                        (body.name.strip(), body.room_id, _clean_base(body.base_url), encrypt(body.token.strip())))
        db.audit(c, p.name, "overlay.add", body.name)
    return {"id": cur.lastrowid}


@router.put("/api/overlays/targets/{target_id}")
def edit_target(target_id: int, body: TargetIn, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        t = _target(c, target_id, p)
        token = encrypt(body.token.strip()) if body.token.strip() else t["token_enc"]
        c.execute("UPDATE overlay_targets SET name=?,room_id=?,base_url=?,token_enc=? WHERE id=?",
                  (body.name.strip(), body.room_id, _clean_base(body.base_url), token, target_id))
    return {"ok": True}


@router.delete("/api/overlays/targets/{target_id}")
def delete_target(target_id: int, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        c.execute("DELETE FROM overlay_targets WHERE id=?", (target_id,))
    return {"ok": True}


@router.get("/api/overlays/targets/{target_id}/status")
async def target_status(target_id: int, p: Principal = Depends(require_tech)):
    with db.ro() as c:
        t = _target(c, target_id, p)
    return await _call(t, "/status")


class OverlayAction(BaseModel):
    overlay: str = "all"  # overlay number or "all"
    action: str
    url: str | None = None
    value: int | None = None
    seconds: int | None = None
    degrees: int | None = None


@router.post("/api/overlays/targets/{target_id}/action")
async def overlay_action(target_id: int, body: OverlayAction, p: Principal = Depends(require_tech)):
    if body.action not in OVERLAY_ACTIONS:
        raise HTTPException(400, "Unknown overlay action")
    if body.overlay != "all" and not body.overlay.isdigit():
        raise HTTPException(400, "Overlay must be a number or 'all'")
    if body.overlay == "all" and body.action not in ("show", "hide", "reload"):
        raise HTTPException(400, "Only show, hide and reload work on all overlays")
    params = {k: v for k, v in {"url": body.url, "value": body.value, "seconds": body.seconds,
                                "degrees": body.degrees}.items() if v is not None}
    with db.ro() as c:
        t = _target(c, target_id, p)
    return await _call(t, f"/overlay/{body.overlay}/{body.action}", params)


class SceneIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.get("/api/overlays/targets/{target_id}/scenes")
async def scenes(target_id: int, p: Principal = Depends(require_tech)):
    with db.ro() as c:
        t = _target(c, target_id, p)
    return await _call(t, "/scene/list")


@router.post("/api/overlays/targets/{target_id}/scenes/load")
async def load_scene(target_id: int, body: SceneIn, p: Principal = Depends(require_tech)):
    with db.ro() as c:
        t = _target(c, target_id, p)
    return await _call(t, "/scene/load", {"name": body.name})
