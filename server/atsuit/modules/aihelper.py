"""AI helper: a chat in the techs' workspace that helps find and fix show-day faults.

The tech asks, the helper answers, and they carry on talking ("still nothing",
"what if the cable's fine?") until it's fixed or they close it. It answers from
what this venue knows first: the admins' venue notes, how past help calls were
fixed, and answers techs marked "This fixed it". Then from the built-in tech
guide (knowledge/*.md): show-day fault finding for sound, video, lighting,
staging, networks, presentation laptops, running the show and AT-SUITE itself,
shipped with the app so it works offline. Admins can edit the guide in the
console; their copies live in the data folder and replace the built-in ones.

With a local model (Ollama) the whole conversation, the matching venue notes
and guide entries go to it and it writes the replies. Without one the helper
still talks: it offers the best guide entry and walks the tech through that
entry's questions ("Does the channel's meter move? Yes / No"). The conversation
lives in the tech's window only; the server keeps nothing of it.

Nothing leaves the venue's network unless an admin turns on web quick search,
and then only the question goes out (to DuckDuckGo), never the venue's notes,
chat or names.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

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


def edits_dir() -> Path:
    """Where the console keeps the admins' own copies of guide topics."""
    return config.cfg.data / "knowledge"


def guide_sources() -> dict[str, tuple[Path, str]]:
    """Every guide topic by file name, and where it comes from. A later folder's file of the same
    name replaces an earlier one: AT-SUIT's own, then a provider's (ATSUIT_KB_DIR), then the
    venue's edits."""
    files = {}
    for d, src in [(GUIDE_DIR, "built-in"), *[(p, "provider") for p in config.cfg.kb_dirs], (edits_dir(), "edited")]:
        for f in sorted(d.glob("*.md")) if d.is_dir() else []:
            files[f.name] = (f, src)
    return files


def guide_files() -> list[Path]:
    return [f for _, (f, _) in sorted(guide_sources().items())]


def guide() -> tuple[dict, ...]:
    """The tech guide: one item per "## " heading, its "Keywords:" line only for finding it."""
    return _guide(tuple((str(f), f.stat().st_mtime) for f in guide_files()))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


ASK = re.compile(r"^### Ask ([A-Za-z0-9_-]{1,40}):\s*(.+?)\s*$")
OPTION = re.compile(r"^[-*]\s+([^:]{1,40}):\s*(.*?)\s*(?:(?:→|->)\s*([A-Za-z0-9_-]{1,40}))?\s*$")


def parse_entry(body: str) -> tuple[str, dict, str, list[str]]:
    """An entry's steps, its questions ({id: {"q", "options": [{"label", "text", "next"}]}}), the id of
    its first question, and anything wrong with how the questions are written.

        ### Ask meter: Does the channel's meter move when someone speaks?
        - Yes: The sound reaches the desk, so the fault is after it. → after
        - No: Nothing reaches the desk. → power

    An option without "→ id" is the end of that path."""
    steps, _, rest = body.partition("\n### ")
    tree, first, node, problems = {}, "", None, []
    for line in ("### " + rest).splitlines() if rest else []:
        m = ASK.match(line)
        if m:
            node = {"q": m.group(2), "options": []}
            if m.group(1) in tree:
                problems.append(f"the question id {m.group(1)} is used twice")
            tree[m.group(1)] = node
            first = first or m.group(1)
        elif line.startswith("### "):
            node = None
            problems.append(f"\"{line[:60]}\" should be \"### Ask <id>: <question>\"")
        elif node is not None and (o := OPTION.match(line.strip())):
            node["options"].append({"label": o.group(1).strip(), "text": o.group(2).strip(), "next": o.group(3) or ""})
    for qid, n in tree.items():
        if not n["options"]:
            problems.append(f"the question {qid} has no answers (lines like \"- Yes: what to do\")")
        for o in n["options"]:
            if o["next"] and o["next"] not in tree:
                problems.append(f"{qid} → {o['next']}: there's no question {o['next']}")
    return steps.strip(), tree, first, problems


@lru_cache(maxsize=1)
def _guide(files: tuple) -> tuple[dict, ...]:
    items = []
    for name, _ in files:
        f = Path(name)
        for block in re.split(r"^## ", f.read_text(encoding="utf-8"), flags=re.M)[1:]:
            title, _, body = block.partition("\n")
            keys = re.search(r"^Keywords:(.*)$", body, flags=re.M)
            steps, tree, first, _ = parse_entry(re.sub(r"^Keywords:.*\n?", "", body, flags=re.M))
            items.append({"kind": GUIDE, "id": f"{f.stem}:{slug(title)}", "title": title.strip(), "text": steps,
                          "keys": keys.group(1) if keys else "", "asks": " ".join(n["q"] for n in tree.values()), "url": f"/guide/help#{f.stem}",
                          "topic": f.stem, "tree": tree, "first": first})
    return tuple(items)


def readable(markdown: str) -> str:
    """A guide file for people: no Keywords lines, and its questions written out plainly."""
    text = re.sub(r"^Keywords:.*\n?", "", markdown, flags=re.M)
    asks = {m.group(1): m.group(2) for m in map(ASK.match, text.splitlines()) if m}
    out, in_ask = [], False
    for line in text.splitlines():
        m = ASK.match(line)
        if m:
            in_ask = True
            out.append(f"#### {m.group(2)}")
            continue
        if line.startswith("#"):
            in_ask = False
        o = OPTION.match(line.strip()) if in_ask else None
        if o:
            nxt = f" (Next: {asks[o.group(3)]})" if o.group(3) in asks else ""
            out.append(f"- **{o.group(1).strip()}:** {o.group(2).strip()}{nxt}")
        else:
            out.append(line)
    return "\n".join(out)


@router.get("/api/guide/help")
def help_articles():
    """The tech guide as pages, for /guide/help. No sign-in: it's general show-day know-how,
    nothing about the venue (the console says so to admins who edit it)."""
    out = []
    for f in guide_files():
        text = readable(f.read_text(encoding="utf-8"))
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
            items.append({"kind": "venue notes", "id": f"venue:{slug(head)}", "title": head.strip()[:80], "text": rest.strip() or head.strip()})
    if cfg["learn_help"]:
        for h in c.execute("SELECT id,room_name,category,description,resolution,resolved_at FROM help_requests "
                           "WHERE resolution!='' AND (site_id IS ? OR ? IS NULL) ORDER BY id DESC LIMIT 500", (site_id, site_id)):
            items.append({"kind": "past help call", "id": f"help:{h['id']}", "title": f"{h['category']} in {h['room_name']}: {h['description']}"[:120],
                          "text": f"Problem: {h['description']} ({h['category']}, {h['room_name']}). Fixed by: {h['resolution']}"})
    for k in c.execute("SELECT id,question,answer,added_by FROM ai_knowledge WHERE site_id IS ? OR site_id IS NULL OR ? IS NULL "
                       "ORDER BY id DESC LIMIT 500", (site_id, site_id)):
        items.append({"kind": "worked before", "id": f"learned:{k['id']}", "title": k["question"][:120], "text": f"Q: {k['question']}\nA: {k['answer']}"})
    return items


def best(question: str, items: list[dict], top: int = 4, cover: float = 0) -> list[dict]:
    """The items sharing the most (and the rarest) words with the question; with `cover`, only
    items holding at least that share of the question's words."""
    q = set(words(question))
    if not q or not items:
        return []
    docs = [Counter(words(f"{i['title']} {i['title']} {i.get('keys', '')} {i.get('keys', '')} {i['text']} {i.get('asks', '')}")) for i in items]
    df = Counter(w for d in docs for w in set(d))
    n = len(docs)
    scored = []
    for item, d in zip(items, docs):
        hit = [w for w in q if d.get(w)]
        named = set(words(item["title"]))  # a word in the title says more than one in the text
        s = sum(math.log(1 + n / df[w]) * (1 + math.log(d[w])) * (1.5 if w in named else 1) for w in hit)
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


# -------------------------------------------------------------------- chat --
# A conversation: the tech's window keeps it and sends it all each time, with the `state` the last
# reply handed back (which guide entry it's on, which of its questions it asked, what it has offered).
YES = {"yes", "y", "yeah", "yep", "yup", "ya", "aye", "correct", "sure", "it does", "it is", "there is", "they do", "i do", "yes it does"}
NO = {"no", "n", "nope", "nah", "not really", "it doesnt", "it doesn't", "it isnt", "it isn't", "there isnt", "there isn't",
      "nothing", "none", "they dont", "they don't", "no it doesnt", "no it doesn't"}
FIXED = re.compile(r"\b(fixed|sorted|solved|resolved|working now|works now|that worked|that did it|it worked|all good|back now|it'?s back|good now)\b")
STILL = re.compile(r"\b(still|didn'?t (work|help|fix)|did not (work|help|fix)|doesn'?t help|no luck|same (thing|problem)|no change|"
                   r"nothing changed|no difference|tried (that|it|all|everything)|already (did|tried|checked|done))\b")
ENDED = "_end"  # the state's `ask` after a path's last step: "Did that sort it?"
CHECK = ["Fixed", "Still not working"]
CALL = "Call for help"
HELP_CATEGORY = {"audio": "Audio", "video": "Video", "presenters": "Presenter", "lighting": "Lighting", "network": "Network"}

CHAT_PROMPT = """You are {name}, chatting with a technician at a live event venue{venue}{room}. Their name is {who}. They are busy, maybe mid-show.
Help them find and fix the fault one step at a time, like an experienced crew chief on the radio:
- Reply in at most 6 short lines, plain words, practical steps first, no preamble.
- Ask one clear question when the answer decides the next step (for example "Is the channel's meter moving?").
- Use what they already told you; don't repeat steps they've tried.
- The venue information comes first: where it disagrees with the tech guide, the venue is right.
- Never make up venue details like input numbers, room names or where kit is kept.
- If it could be unsafe (power, rigging, height, smoke), say to stop and call the crew.
- If you run out of ideas, say so and tell them to press Call for help.

Venue information:
{venue_info}

Tech guide:
{guide_info}"""


def norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def pick(text: str, options: list[dict]) -> dict | None:
    """Which of a question's answers the tech's reply means, if any: "yes", "Nope", "HDMI it is"."""
    t = norm(text)
    if not t:
        return None
    labels = [(o, norm(o["label"])) for o in options]
    for o, lab in labels:
        if t == lab:
            return o
    first = t.split()[0]
    yes, no = t in YES or first in YES, t in NO or first in NO
    if yes != no:
        want = YES if yes else NO
        for o, lab in labels:
            if lab in want:
                return o
    starts = [o for o, lab in labels if t.startswith(lab + " ")]
    if len(starts) == 1:
        return starts[0]
    inside = [o for o, lab in labels if len(lab) > 2 and re.search(rf"\b{re.escape(lab)}\b", t)]
    return inside[0] if len(inside) == 1 else None


def _fixed(text: str) -> bool:
    return bool(FIXED.search(text.lower())) and "?" not in text and len(text.split()) <= 8


def _still(text: str) -> bool:
    """"Still not working", "tried that": the last suggestion didn't help, and nothing new to go on."""
    t = text.lower()
    if not STILL.search(t) and norm(t) not in ("not working", "still not working", "no good", "nope still"):
        return False
    rest = set(words(STILL.sub(" ", t))) - {"work", "working", "not", "nope", "still", "anything"}
    return len(rest) <= 2


def _gist(text: str) -> list[str]:
    """The words of a message that say what's wrong, without "yes", "nope", "still" and the like."""
    noise = {w for phrase in YES | NO for w in words(phrase)} | {"still", "work", "working", "tried", "thank", "thanks", "ok", "okay"}
    return [w for w in words(text) if w not in noise]


def _question(entry: dict, qid: str) -> tuple[str, list[str]]:
    node = entry["tree"][qid]
    return node["q"], [o["label"] for o in node["options"]]


def _brief(entry: dict) -> str:
    """A guide entry for the model, its questions included."""
    lines = [f"[tech guide] {entry['title']}", entry["text"]]
    for node in entry["tree"].values():
        lines.append(f"Ask: {node['q']}")
        lines += [f"  {o['label']}: {o['text']}" for o in node["options"]]
    return "\n".join(lines)


def chat_model(url: str, model: str, messages: list[dict]) -> str:
    r = httpx.post(f"{url.rstrip('/')}/api/chat", json={"model": model, "messages": messages, "stream": False,
                                                        "options": {"temperature": 0.2, "num_predict": 350}}, timeout=60)
    r.raise_for_status()
    return ((r.json().get("message") or {}).get("content") or "").strip()


def converse(c_items: list[dict], cfg: dict, turns: list[dict], state: dict, who: str, room: str, venue: str) -> dict:
    """The helper's next reply in a conversation. `turns` are {"role": "tech"|"helper", "text"}, oldest first."""
    last = turns[-1]["text"].strip()
    asked = [t["text"] for t in turns if t["role"] == "tech"]
    guide_items = list(guide()) if cfg.get("builtin_guide", True) else []
    by_id = {g["id"]: g for g in guide_items}
    topic = by_id.get(state.get("topic") or "")
    ask = state.get("ask") or ""
    node = topic["tree"].get(ask) if topic else None
    shown = list(dict.fromkeys(state.get("shown") or []))
    sources: list[dict] = []

    def reply(answer: str, choices: list[str] | None = None, *, done: bool = False, call: bool = False, model: bool = False,
              again: bool = False) -> dict:
        cat = HELP_CATEGORY.get(topic["topic"], "Other") if topic else "Other"
        return {"answer": answer, "choices": choices or [], "sources": _out(sources), "model": model, "done": done,
                "help": {"category": cat, "description": asked[0][:500]} if call else None,
                "state": {"topic": topic["id"] if topic else "", "ask": ask, "shown": shown[-60:], "again": again}}

    # "That's fixed it": nothing more to do but say so.
    if _fixed(last) or (ask == ENDED and norm(last) in YES):
        ask = ""
        return reply("Great, glad it's working. Press \"This fixed it\" so I can offer the same fix next time.", done=True)

    # An answer to the question just asked: follow that path.
    if node and (o := pick(last, node["options"])):
        sources.append(topic)
        if o["next"] in topic["tree"]:
            ask = o["next"]
            q, choices = _question(topic, ask)
            return reply(f"{o['text']}\n\n{q}" if o["text"] else q, choices)
        ask = ENDED
        return reply(f"{o['text']}\n\nDid that sort it?", CHECK)

    still = _still(last) or (ask == ENDED and norm(last) in NO)
    if still and node and not state.get("again"):  # they didn't answer the question: ask it once more
        q, choices = _question(topic, ask)
        sources.append(topic)
        return reply(f"OK. To narrow it down: {q}", choices, again=True)

    # What the tech has said about the fault, without their yes/no answers.
    context = " ".join(t for t in asked[-8:] if len(_gist(t)) >= 2)

    # With a model: the whole conversation, and what the venue and the guide say about it.
    if cfg["ai_url"]:
        found = best(f"{last} {context}", c_items, top=4)
        g = best(f"{last} {context}", guide_items, top=3, cover=0.3)
        if topic and topic not in g:
            g.insert(0, topic)
        sources += found + g
        system = CHAT_PROMPT.format(name=NAME, venue=f" ({venue})" if venue else "", room=f", in {room}" if room else "", who=who,
                                    venue_info="\n\n".join(f"[{s['kind']}] {s['text']}" for s in found) or "(nothing on this yet)",
                                    guide_info="\n\n".join(_brief(e) for e in g) or "(nothing on this)")
        msgs = [{"role": "system", "content": system}] + [
            {"role": "user" if t["role"] == "tech" else "assistant", "content": t["text"]} for t in turns[-20:]]
        try:
            text = chat_model(cfg["ai_url"], cfg["ai_model"], msgs)
            if text:
                topic, ask = (g[0] if g else topic), ""
                shown += [s["id"] for s in sources if s.get("id")]
                return reply(text, model=True)
        except (httpx.HTTPError, ValueError):
            pass  # no model reachable: carry on without one
        sources = []

    # Without one: what the venue knows, then the best guide entry not offered yet, and its questions.
    # A follow-up ("what if the batteries are new?") is read with what came before, unless it plainly
    # says something new on its own ("and now the projector has no signal").
    fresh = lambda q, n, cover: [x for x in best(q, n, top=4, cover=cover) if x["id"] not in shown]
    if still:  # something else on the same subject, twice at most, then the crew
        tried = sum(1 for i in shown if i in by_id)
        venue_found = fresh(context, c_items, 0)[:3]
        g = [x for x in fresh(context, guide_items, 0.3) if not topic or x["topic"] == topic["topic"]] if tried < 3 else []
        # An entry the last reply pointed to (see "Laptop doesn't detect the screen") comes first.
        said = turns[-2]["text"] if len(turns) > 1 and turns[-2]["role"] == "helper" else ""
        named = {x["title"]: x for x in guide_items if x["id"] not in shown}
        g = [named[t] for t in re.findall(r'"([^"]{3,80})"', said) if t in named] + g
    elif node and not fresh(last, guide_items, 0.6) and best(last, [topic], cover=0.5):
        # About the entry it's on ("what if the batteries are new?"): carry on with its question.
        q, choices = _question(topic, ask)
        sources.append(topic)
        return reply(f"OK, then let's narrow it down. {q}", choices)
    elif len(asked) > 1 and not fresh(last, guide_items, 0.6):
        venue_found, g = fresh(f"{last} {context}", c_items, 0)[:3], fresh(f"{last} {context}", guide_items, 0.3)
    else:
        venue_found, g = fresh(last, c_items, 0)[:3], fresh(last, guide_items, 0.5)
    parts = []
    if venue_found:
        lines = [f"• {s['title']}: {s['text'].split('Fixed by: ')[-1].split('A: ')[-1]}" for s in venue_found]
        parts.append("Here's what has worked here before:\n" + "\n".join(lines))
        sources += venue_found
        shown += [s["id"] for s in venue_found]
    if g:
        topic = g[0]
        sources.append(topic)
        shown.append(topic["id"])
        lead = "Let's try something else. " if still else ""
        parts.append(f"{lead}From the tech guide, {topic['title']}:\n{topic['text']}")
        if topic["first"]:
            ask = topic["first"]
            q, choices = _question(topic, ask)
            parts.append(q)
            return reply("\n\n".join(parts), choices)
    if parts:
        ask = ENDED
        return reply("\n\n".join(parts) + "\n\nDid that sort it?", CHECK)
    if cfg["web_search"] and not still:
        try:
            web = web_search(last)
        except (httpx.HTTPError, ValueError):
            web = None
        if web:
            sources.append(web)
            ask = ENDED
            return reply(f"From the web ({web['title']}): {web['text']}\n\nDid that sort it?", CHECK)
    ask = ""
    if still or len(asked) > 1:
        return reply("I've run out of ideas for this one. Call for help and the crew will come; tell them what you've tried. "
                     "Once it's fixed, add how, so I can help next time.", [CALL], call=True)
    return reply("I don't know this one yet. Tell me a bit more (what you see or hear, and what you've tried), "
                 "or call for help and the crew will come.", [CALL], call=True)


class Turn(BaseModel):
    role: Literal["tech", "helper"]
    text: str = Field(min_length=1, max_length=4000)


class ChatState(BaseModel):
    topic: str = Field(default="", max_length=120)
    ask: str = Field(default="", max_length=60)
    shown: list[Annotated[str, Field(max_length=120)]] = Field(default_factory=list, max_length=100)
    again: bool = False  # the question was asked a second time already


class ChatIn(BaseModel):
    messages: list[Turn] = Field(min_length=1, max_length=60)
    state: ChatState = Field(default_factory=ChatState)
    room_id: int | None = None


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    room_id: int | None = None


@router.get("/api/ai")
def status(p: Principal = Depends(require_tech)):
    with db.ro() as c:
        cfg = prefs(c)
    return {"enabled": cfg["enabled"], "name": NAME, "model": bool(cfg["ai_url"]), "web_search": cfg["web_search"]}


def _where(c, p: Principal, room_id: int | None) -> tuple[int | None, str, str]:
    """The site, room name and venue name a tech is asking from."""
    room = c.execute("SELECT name,site_id FROM rooms WHERE id=?", (room_id,)).fetchone() if room_id else None
    if room and not site_ok(p, room["site_id"]):
        room = None
    site_id = room["site_id"] if room else p.site_id
    site = c.execute("SELECT name FROM sites WHERE id=?", (site_id,)).fetchone() if site_id else None
    return site_id, room["name"] if room else "", site["name"] if site else ""


@router.post("/api/ai/ask")
def ask(body: AskIn, p: Principal = Depends(require_tech)):
    """One question, one answer (laptop apps from before the chat)."""
    with db.ro() as c:
        cfg = prefs(c)
        if not cfg["enabled"]:
            raise HTTPException(404, f"The {NAME} is turned off")
        site_id, room, venue = _where(c, p, body.room_id)
        items = knowledge(c, site_id)
    return answer(items, cfg, body.question.strip(), p.name, room, venue)


@router.post("/api/ai/chat")
def chat(body: ChatIn, p: Principal = Depends(require_tech)):
    """The helper's next reply in a conversation the tech's window keeps. Nothing of it is stored here."""
    if body.messages[-1].role != "tech":
        raise HTTPException(400, "The last message must be the tech's")
    with db.ro() as c:
        cfg = prefs(c)
        if not cfg["enabled"]:
            raise HTTPException(404, f"The {NAME} is turned off")
        site_id, room, venue = _where(c, p, body.room_id)
        items = knowledge(c, site_id)
    turns = [{"role": t.role, "text": t.text.strip() or "…"} for t in body.messages]
    return converse(items, cfg, turns, body.state.model_dump(), p.name, room, venue)


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


# -------------------------------------------------------------- the guide --
# Admins read and change the tech guide in the console. Their copy of a topic is kept in the data
# folder and replaces the built-in one (Reset brings that back); a new name adds a topic.
TOPIC = r"^[a-z0-9][a-z0-9-]{0,39}$"


def _topic_file(name: str) -> Path:
    if not re.match(TOPIC, name):
        raise HTTPException(400, "A topic's name is lower-case letters, numbers and dashes")
    return edits_dir() / f"{name}.md"


def _built_in(name: str) -> Path | None:
    for d in [*reversed(config.cfg.kb_dirs), GUIDE_DIR]:
        if (d / f"{name}.md").is_file():
            return d / f"{name}.md"
    return None


def check(markdown: str) -> list[str]:
    """What's wrong with how a topic is written, in words an admin can act on."""
    problems = []
    blocks = re.split(r"^## ", markdown, flags=re.M)[1:]
    if not blocks:
        problems.append("there are no entries: each one starts with a line \"## Title\"")
    for block in blocks:
        title, _, body = block.partition("\n")
        _, _, _, bad = parse_entry(re.sub(r"^Keywords:.*\n?", "", body, flags=re.M))
        problems += [f"{title.strip()}: {b}" for b in bad]
    return problems


@router.get("/api/admin/ai/guide")
def guide_topics(p: Principal = Depends(require_admin)):
    out = []
    for name, (f, src) in sorted(guide_sources().items()):
        text = f.read_text(encoding="utf-8")
        title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), f.stem)
        out.append({"name": f.stem, "title": title, "source": src if src != "edited" or _built_in(f.stem) else "added",
                    "entries": len(re.findall(r"^## ", text, flags=re.M)),
                    "questions": len(re.findall(r"^### Ask ", text, flags=re.M))})
    return {"topics": out}


@router.get("/api/admin/ai/guide/{name}")
def guide_topic(name: str, p: Principal = Depends(require_admin)):
    own, base = _topic_file(name), _built_in(name)
    f = own if own.is_file() else base
    if not f:
        raise HTTPException(404, "No such topic")
    text = f.read_text(encoding="utf-8")
    return {"name": name, "markdown": text, "edited": own.is_file(), "built_in": bool(base), "problems": check(text)}


class TopicIn(BaseModel):
    markdown: str = Field(min_length=1, max_length=200_000)


@router.put("/api/admin/ai/guide/{name}")
def save_topic(name: str, body: TopicIn, p: Principal = Depends(require_admin)):
    f = _topic_file(name)
    text = body.markdown.replace("\r\n", "\n").rstrip() + "\n"
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(f)
    with db.tx() as c:
        db.audit(c, p.name, "ai_helper.guide", f"saved {name}")
    return {"ok": True, "problems": check(text)}


@router.delete("/api/admin/ai/guide/{name}")
def reset_topic(name: str, p: Principal = Depends(require_admin)):
    """Back to the built-in topic, or gone if it's one the venue added."""
    f = _topic_file(name)
    if f.is_file():
        f.unlink()
        with db.tx() as c:
            db.audit(c, p.name, "ai_helper.guide", f"reset {name}")
    return {"ok": True}
