"""Link boards: the Homarr "USER CONTROL" dashboard, plus per-room links
(Companion buttons, Ontime views, kiosk pages) that show up on each node."""
from __future__ import annotations

import asyncio
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..security import Principal, require_admin, require_manager, require_user
from .core import require_module, site_ok

router = APIRouter(dependencies=[Depends(require_module("dashboard"))])

BOARDS = ("public", "admin")
KINDS = ("link", "timer", "buttons", "kiosk", "device", "tool")
_ping_cache: dict[str, tuple[float, bool]] = {}


class LinkIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=4, max_length=1000)
    board: str = "public"
    kind: str = "link"
    site_id: int | None = None
    room_id: int | None = None
    sort: int = 0


def _validate(body: LinkIn) -> None:
    if body.board not in BOARDS:
        raise HTTPException(400, "Board must be public or admin")
    if body.kind not in KINDS:
        raise HTTPException(400, "Unknown link kind")
    if not body.url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "Links must start with http:// or https://")


@router.get("/api/dashboard/links")
def list_links(room_id: int | None = None, p: Principal = Depends(require_user)):
    with db.ro() as c:
        q = ("SELECT l.*, r.name AS room, COALESCE(l.site_id, r.site_id) AS eff_site FROM links l "
             "LEFT JOIN rooms r ON r.id=l.room_id")
        args: list = []
        if room_id is not None:
            q += " WHERE l.room_id=? OR l.room_id IS NULL"
            args.append(room_id)
        rows = db.rows(c.execute(q + " ORDER BY l.board, r.sort, r.name, l.sort, l.label", args))
    out = []
    for r in rows:
        if r["board"] == "admin" and not p.at_least("admin"):
            continue
        if r["eff_site"] is not None and not site_ok(p, r["eff_site"]):
            continue
        out.append(r)
    return out


def _place(c, body: LinkIn, p: Principal) -> None:
    """A room's link belongs to that room's site; admin-board links are for admins."""
    if body.board == "admin" and not p.at_least("admin"):
        raise HTTPException(403, "Only an admin can make admin-board links")
    if body.room_id is not None:
        room = c.execute("SELECT site_id FROM rooms WHERE id=?", (body.room_id,)).fetchone()
        if not room:
            raise HTTPException(404, "Room not found")
        body.site_id = room["site_id"]
    elif p.site_id is not None:
        body.site_id = p.site_id
    if body.site_id is not None and not site_ok(p, body.site_id):
        raise HTTPException(404, "Site not found")


def _own_link(c, link_id: int, p: Principal):
    r = c.execute("SELECT l.*, COALESCE(l.site_id, r.site_id) AS eff_site FROM links l LEFT JOIN rooms r ON r.id=l.room_id WHERE l.id=?",
                  (link_id,)).fetchone()
    if not r or (r["eff_site"] is not None and not site_ok(p, r["eff_site"])) or (r["board"] == "admin" and not p.at_least("admin")):
        raise HTTPException(404, "Link not found")
    return r


@router.post("/api/dashboard/links")
def add_link(body: LinkIn, p: Principal = Depends(require_manager)):
    _validate(body)
    with db.tx() as c:
        _place(c, body, p)
        cur = c.execute("INSERT INTO links(site_id,room_id,board,label,url,kind,sort) VALUES(?,?,?,?,?,?,?)",
                        (body.site_id, body.room_id, body.board, body.label.strip(), body.url.strip(), body.kind, body.sort))
        db.audit(c, p.name, "link.add", body.label.strip())
    return {"id": cur.lastrowid}


@router.put("/api/dashboard/links/{link_id}")
def edit_link(link_id: int, body: LinkIn, p: Principal = Depends(require_manager)):
    _validate(body)
    with db.tx() as c:
        _own_link(c, link_id, p)
        _place(c, body, p)
        c.execute("UPDATE links SET site_id=?,room_id=?,board=?,label=?,url=?,kind=?,sort=? WHERE id=?",
                  (body.site_id, body.room_id, body.board, body.label.strip(), body.url.strip(), body.kind, body.sort, link_id))
        db.audit(c, p.name, "link.edit", body.label.strip())
    return {"ok": True}


@router.delete("/api/dashboard/links/{link_id}")
def delete_link(link_id: int, p: Principal = Depends(require_manager)):
    with db.tx() as c:
        r = _own_link(c, link_id, p)
        c.execute("DELETE FROM links WHERE id=?", (link_id,))
        db.audit(c, p.name, "link.delete", r["label"])
    return {"ok": True}


async def _ping(client: httpx.AsyncClient, url: str) -> bool:
    hit = _ping_cache.get(url)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    try:
        r = await client.get(url, follow_redirects=False)
        ok = r.status_code < 500
    except httpx.HTTPError:
        ok = False
    _ping_cache[url] = (time.time(), ok)
    return ok


@router.get("/api/dashboard/ping")
async def ping(p: Principal = Depends(require_user)):
    """Is each link answering? Checked from the server, cached for 30 s."""
    links = list_links(None, p)
    async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
        results = await asyncio.gather(*(_ping(client, l["url"]) for l in links))
    return {str(l["id"]): ok for l, ok in zip(links, results)}
