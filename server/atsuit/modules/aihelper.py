"""AI helper: answers techs' questions in the Help menu and its own window.

It answers from what this venue knows first: the admins' venue notes, how past
help calls were fixed, and answers techs marked "This fixed it". Then from the
built-in tech guide (knowledge/*.md): show-day fixes for audio, video,
presenters, hybrid events, lighting, safety, running the show and AT-SUITE
itself, shipped with the app so it works offline. With a local model (Ollama)
it writes those up as a short answer; without one it shows the best match. Nothing leaves the venue's network unless an admin turns on web
quick search, and then only the question goes out (to DuckDuckGo), never the
venue's notes, chat or names.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import config, db
from ..security import Principal, require_admin, require_tech
from .core import site_ok

router = APIRouter()

NAME = "AI helper"
DEFAULTS = {"enabled": True, "ai_url": "", "ai_model": "llama3.1:8b", "web_search": False, "learn_help": True,
            "builtin_guide": True, "venue_notes": ""}
GUIDE = "tech guide"
GUIDE_DIR = Path(__file__).resolve().parent.parent / "knowledge"
SEARCH = "https://api.duckduckgo.com/"
STOP = set("""a an and are as at be but by can do does for from has have how i if in into is it its me my no not of on or
our so that the their them then there these this to too up us was we what when where which who why will with you your
get got just need now any all some please help im ive dont cant wont isnt""".split())


def prefs(c) -> dict:
    out = {**DEFAULTS, **(db.get_setting(c, "ai_helper", {}) or {})}
    if not out["ai_url"]:  # the Presenters schedule AI, if that's set up
        out["model_from_presenters"] = True
        p = db.get_setting(c, "presenter", {}) or {}
        out["ai_url"], out["ai_model"] = p.get("ai_url", ""), p.get("ai_model") or out["ai_model"]
    if not out["ai_url"] and config.cfg.ai_url:  # then the provider's private model (hosted venues)
        out["ai_url"], out["ai_model"] = config.cfg.ai_url, config.cfg.ai_model or out["ai_model"]
    return out


def _stem(w: str) -> str:
    """Enough to match "mics" with "mic" and "flickering" with "flicker"."""
    for end in ("ing", "ed", "es", "s"):
        if w.endswith(end) and len(w) - len(end) >= 3 and not w.endswith("ss"):
            return w[: -len(end)]
    return w


def words(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 1]


def guide_files() -> list[Path]:
    """The tech guide's files: AT-SUIT's own, then a provider's (ATSUIT_KB_DIR), whose file of the
    same name replaces AT-SUIT's."""
    files = {}
    for d in [GUIDE_DIR, *config.cfg.kb_dirs]:
        for f in sorted(d.glob("*.md")) if d.is_dir() else []:
            files[f.name] = f
    return sorted(files.values(), key=lambda f: f.name)


def guide() -> tuple[dict, ...]:
    """The tech guide: one item per "## " heading, its "Keywords:" line only for finding it."""
    return _guide(tuple((str(f), f.stat().st_mtime) for f in guide_files()))


@lru_cache(maxsize=1)
def _guide(files: tuple) -> tuple[dict, ...]:
    items = []
    for name, _ in files:
        f = Path(name)
        for block in re.split(r"^## ", f.read_text(encoding="utf-8"), flags=re.M)[1:]:
            title, _, body = block.partition("\n")
            keys = re.search(r"^Keywords:(.*)$", body, flags=re.M)
            text = re.sub(r"^Keywords:.*\n?", "", body, flags=re.M).strip()
            items.append({"kind": GUIDE, "title": title.strip(), "text": text, "keys": keys.group(1) if keys else "",
                          "url": f"/guide/help#{f.stem}"})
    return tuple(items)


@router.get("/api/guide/help")
def help_articles():
    """The tech guide as pages, for /guide/help. No sign-in: it's general show-day know-how,
    nothing about the venue."""
    out = []
    for f in guide_files():
        text = re.sub(r"^Keywords:.*\n?", "", f.read_text(encoding="utf-8"), flags=re.M)
        title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), f.stem)
        out.append({"slug": f.stem, "title": title, "markdown": text})
    return {"articles": sorted(out, key=lambda a: a["title"].lower())}


def knowledge(c, site_id: int | None) -> list[dict]:
    """Everything the helper can answer from, for one site."""
    cfg = prefs(c)
    items = []
    for block in re.split(r"\n\s*\n", cfg["venue_notes"] or ""):
        block = block.strip()
        if block:
            head, _, rest = block.partition("\n")
            items.append({"kind": "venue notes", "title": head.strip()[:80], "text": rest.strip() or head.strip()})
    if cfg["learn_help"]:
        for h in c.execute("SELECT room_name,category,description,resolution,resolved_at FROM help_requests "
                           "WHERE resolution!='' AND (site_id IS ? OR ? IS NULL) ORDER BY id DESC LIMIT 500", (site_id, site_id)):
            items.append({"kind": "past help call", "title": f"{h['category']} in {h['room_name']}: {h['description']}"[:120],
                          "text": f"Problem: {h['description']} ({h['category']}, {h['room_name']}). Fixed by: {h['resolution']}"})
    for k in c.execute("SELECT question,answer,added_by FROM ai_knowledge WHERE site_id IS ? OR site_id IS NULL OR ? IS NULL "
                       "ORDER BY id DESC LIMIT 500", (site_id, site_id)):
        items.append({"kind": "worked before", "title": k["question"][:120], "text": f"Q: {k['question']}\nA: {k['answer']}"})
    return items


def best(question: str, items: list[dict], top: int = 4, cover: float = 0) -> list[dict]:
    """The items sharing the most (and the rarest) words with the question; with `cover`, only
    items holding at least that share of the question's words."""
    q = set(words(question))
    if not q or not items:
        return []
    docs = [Counter(words(f"{i['title']} {i.get('keys', '')} {i.get('keys', '')} {i['text']}")) for i in items]
    df = Counter(w for d in docs for w in set(d))
    n = len(docs)
    scored = []
    for item, d in zip(items, docs):
        hit = [w for w in q if d.get(w)]
        s = sum(math.log(1 + n / df[w]) * (1 + math.log(d[w])) for w in hit)
        if s > 0 and len(hit) >= cover * len(q):
            scored.append((s / math.sqrt(1 + sum(d.values()) / 40), item))
    scored.sort(key=lambda x: -x[0])
    return [i for _, i in scored[:top]]


def web_search(question: str) -> dict | None:
    """DuckDuckGo's instant answer for the question alone. None if nothing useful."""
    r = httpx.get(SEARCH, params={"q": question[:200], "format": "json", "no_html": 1, "skip_disambig": 1}, timeout=6)
    r.raise_for_status()
    d = r.json()
    text = d.get("AbstractText") or next((t.get("Text") for t in d.get("RelatedTopics", []) if isinstance(t, dict) and t.get("Text")), "")
    if not text:
        return None
    return {"kind": "web", "title": d.get("Heading") or "Web", "text": text[:1200], "url": d.get("AbstractURL") or ""}


PROMPT = """You are {name}, helping a technician at a live event venue{venue}. They are busy, maybe mid-show.
Answer in at most 6 short lines: practical steps first, plain words, no preamble.
Use the venue information below first, then the tech guide. If neither covers the question, give general live-events advice and say so.
Never make up venue details like input numbers or room names. If it could be unsafe, say to call the crew.

Venue information:
{context}

Question from {who}{room}: {question}

Answer:"""


def generate(url: str, model: str, prompt: str) -> str:
    r = httpx.post(f"{url.rstrip('/')}/api/generate", json={"model": model, "prompt": prompt, "stream": False,
                                                            "options": {"temperature": 0.2, "num_predict": 300}}, timeout=45)
    r.raise_for_status()
    return (r.json().get("response") or "").strip()


def answer(c_items: list[dict], cfg: dict, question: str, who: str, room: str, venue: str) -> dict:
    # What this venue knows comes first; the built-in guide fills in.
    found = best(question, c_items, top=3)
    from_guide = best(question, list(guide()), top=2, cover=0.5) if cfg.get("builtin_guide", True) else []
    venue_found, found = found, found + from_guide
    web = None
    if cfg["web_search"] and len(found) < 2:
        try:
            web = web_search(question)
        except (httpx.HTTPError, ValueError):
            web = None
    sources = found + ([web] if web else [])
    if cfg["ai_url"]:
        context = "\n\n".join(f"[{s['kind']}] {s['text']}" for s in sources) or "(nothing on this yet)"
        try:
            text = generate(cfg["ai_url"], cfg["ai_model"], PROMPT.format(
                name=NAME, venue=f" ({venue})" if venue else "", context=context, who=who, room=f" in {room}" if room else "", question=question))
            if text:
                return {"answer": text, "sources": _out(sources), "model": True}
        except (httpx.HTTPError, ValueError):
            pass  # no model reachable: fall back to what worked before
    if found:
        parts = []
        if venue_found:
            lines = [f"• {s['title']}: {s['text'].split('Fixed by: ')[-1].split('A: ')[-1]}" for s in venue_found[:3]]
            parts.append("Here's what has worked here before:\n" + "\n".join(lines))
        if from_guide:
            g = from_guide[0]
            parts.append(f"From the tech guide, {g['title']}:\n{g['text']}")
        text = "\n\n".join(parts)
    elif web:
        text = f"From the web ({web['title']}): {web['text']}"
    else:
        text = "I don't know this one yet. Call for help and the crew will come; once it's fixed, add how, so I can help next time."
    return {"answer": text, "sources": _out(sources), "model": False}


def _out(sources: list[dict]) -> list[dict]:
    return [{"kind": s["kind"], "title": s["title"], **({"url": s["url"]} if s.get("url") else {})} for s in sources]


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    room_id: int | None = None


@router.get("/api/ai")
def status(p: Principal = Depends(require_tech)):
    with db.ro() as c:
        cfg = prefs(c)
    return {"enabled": cfg["enabled"], "name": NAME, "model": bool(cfg["ai_url"]), "web_search": cfg["web_search"]}


@router.post("/api/ai/ask")
def ask(body: AskIn, p: Principal = Depends(require_tech)):
    with db.ro() as c:
        cfg = prefs(c)
        if not cfg["enabled"]:
            raise HTTPException(404, f"The {NAME} is turned off")
        room = c.execute("SELECT name,site_id FROM rooms WHERE id=?", (body.room_id,)).fetchone() if body.room_id else None
        if room and not site_ok(p, room["site_id"]):
            room = None
        site_id = room["site_id"] if room else p.site_id
        site = c.execute("SELECT name FROM sites WHERE id=?", (site_id,)).fetchone() if site_id else None
        items = knowledge(c, site_id)
    return answer(items, cfg, body.question.strip(), p.name, room["name"] if room else "", site["name"] if site else "")


class LearnIn(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    answer: str = Field(min_length=2, max_length=4000)
    room_id: int | None = None


@router.post("/api/ai/learn")
def learn(body: LearnIn, p: Principal = Depends(require_tech)):
    """A tech says an answer fixed it: the helper offers it next time."""
    with db.tx() as c:
        room = c.execute("SELECT site_id FROM rooms WHERE id=?", (body.room_id,)).fetchone() if body.room_id else None
        site_id = room["site_id"] if room and site_ok(p, room["site_id"]) else p.site_id
        c.execute("INSERT INTO ai_knowledge(site_id,question,answer,added_by,created_at) VALUES(?,?,?,?,?)",
                  (site_id, body.question.strip(), body.answer.strip(), p.name, db.now_iso()))
    return {"ok": True}


# ---------------------------------------------------------------- settings --
class SettingsIn(BaseModel):
    enabled: bool = True
    ai_url: str = Field(default="", max_length=300, pattern=r"^$|^https?://\S+$")
    ai_model: str = Field(default="llama3.1:8b", max_length=100)
    web_search: bool = False
    learn_help: bool = True
    builtin_guide: bool = True
    venue_notes: str = Field(default="", max_length=50000)


@router.get("/api/admin/ai")
def get_settings(p: Principal = Depends(require_admin)):
    with db.ro() as c:
        own = {**DEFAULTS, **(db.get_setting(c, "ai_helper", {}) or {})}
        learned = db.rows(c.execute("SELECT k.id,k.question,k.answer,k.added_by,k.created_at,s.name AS site FROM ai_knowledge k "
                                    "LEFT JOIN sites s ON s.id=k.site_id ORDER BY k.id DESC LIMIT 300"))
        fixes = c.execute("SELECT COUNT(*) FROM help_requests WHERE resolution!=''").fetchone()[0]
        presenter = db.get_setting(c, "presenter", {}) or {}
    return {"settings": own, "learned": learned, "help_fixes": fixes, "presenter_ai_url": presenter.get("ai_url", "")}


@router.put("/api/admin/ai")
def put_settings(body: SettingsIn, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        db.set_setting(c, "ai_helper", body.model_dump())
        db.audit(c, p.name, "ai_helper.settings", f"enabled={body.enabled} web_search={body.web_search}")
    return {"ok": True}


@router.delete("/api/admin/ai/learned/{kid}")
def forget(kid: int, p: Principal = Depends(require_admin)):
    with db.tx() as c:
        c.execute("DELETE FROM ai_knowledge WHERE id=?", (kid,))
        db.audit(c, p.name, "ai_helper.forget", str(kid))
    return {"ok": True}
