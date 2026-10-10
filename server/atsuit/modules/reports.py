"""Show reports: what happened in a room on a show day, as a PDF to hand the client.

A show day runs from the day's reset hour (05:00 site time by default, the same
as the tech laptops' day) to the next morning, so a late show stays on its own
day. The report lists the timer runs (and how far each ran over), the help
calls and how long they waited, the messages put on the stage screens, a count
of the room's crew chat, when captions were live and the caption transcript.

Each morning the day before is saved for every room that had a show, so the
report survives chat clean-ups; any day can also be made again on demand."""
from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from .. import VERSION, config, db
from ..security import Principal, decrypt, require_manager
from .core import get_branding, room_or_404, site_ok

router = APIRouter()

STATIC = Path(__file__).resolve().parent.parent / "static"
MAX_TRANSCRIPT_LINES = 20000  # a whole day of captions is a few thousand lines; this keeps a runaway file in check
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ------------------------------------------------------------------ days --
def site_zone(c, site_id) -> ZoneInfo:
    row = c.execute("SELECT timezone FROM sites WHERE id=?", (site_id,)).fetchone() if site_id else None
    try:
        return ZoneInfo((row["timezone"] if row else None) or os.getenv("TZ") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def reset_hour(c) -> int:
    return int(db.get_setting(c, "node_room_reset_hour", 5) or 0)


def show_day(c, site_id, at: float | None = None) -> str:
    """The show day a moment belongs to (now if not given)."""
    t = datetime.fromtimestamp(at if at is not None else time.time(), site_zone(c, site_id))
    return (t - timedelta(hours=reset_hour(c))).date().isoformat()


def window(c, site_id, day: str) -> tuple[float, float]:
    """A show day's start and end, as times."""
    d = date.fromisoformat(day)
    start = datetime(d.year, d.month, d.day, reset_hour(c), tzinfo=site_zone(c, site_id))
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def ts(text: str | None) -> float | None:
    try:
        return datetime.fromisoformat(text).timestamp() if text else None
    except ValueError:
        return None


# ------------------------------------------------------------------ data --
def collect(c, room_id: int, day: str, chat: bool = False, now: float | None = None) -> dict:
    """Everything the report shows for one room and show day."""
    from .timers import remaining

    now = now or time.time()
    room = c.execute("SELECT r.*, s.name AS site_name FROM rooms r LEFT JOIN sites s ON s.id=r.site_id WHERE r.id=?",
                     (room_id,)).fetchone()
    start, end = window(c, room["site_id"], day)
    zone = site_zone(c, room["site_id"])

    # Timer runs that started this day. One still going is read from the live timer.
    live = c.execute("SELECT * FROM timers WHERE room_id=?", (room_id,)).fetchone()
    runs = []
    for r in c.execute("SELECT * FROM timer_runs WHERE room_id=? AND started_at>=? AND started_at<? ORDER BY started_at",
                       (room_id, start, end)):
        left, added, ended = r["remaining_ms"], r["added_ms"], r["ended_at"]
        if ended is None:
            if live and live["first_started_at"] == r["started_at"]:
                left, added = remaining(live, now), live["added_ms"]
            else:
                left = None  # left open by a restart: how it ended isn't known
        planned = r["duration_ms"] + (added or 0)
        timed = r["timer_type"] == "count-down" and r["duration_ms"] > 0
        runs.append({
            "started_at": r["started_at"], "ended_at": ended, "running": ended is None and left is not None,
            "cue": r["cue"], "title": r["title"], "timer_type": r["timer_type"], "duration_ms": r["duration_ms"],
            "added_ms": added or 0, "remaining_ms": left,
            "ran_ms": None if left is None else max(0, planned - left),
            "over_ms": max(0, -left) if timed and left is not None else 0,
            "early_ms": max(0, left) if timed and left is not None and ended is not None else 0,
        })

    calls = []
    for h in c.execute("SELECT * FROM help_requests WHERE room_id=? AND created_at>=? AND created_at<? ORDER BY id",
                       (room_id, iso(start), iso(end))):
        made, answered, resolved = ts(h["created_at"]), ts(h["acknowledged_at"]), ts(h["resolved_at"])
        calls.append({
            "id": h["id"], "created_at": made, "requested_by": h["requested_by"], "category": h["category"],
            "description": h["description"], "status": h["status"], "assigned_to": h["assigned_to"],
            "answer_s": int(answered - made) if made and answered else None,
            "resolved_s": int(resolved - made) if made and resolved else None,
            "escalations": h["escalations"],
        })

    events = c.execute("SELECT * FROM room_events WHERE room_id=? AND at>=? AND at<? ORDER BY at, id",
                       (room_id, start, end)).fetchall()
    stage = [{"at": e["at"], "text": e["detail"]} for e in events if e["kind"] == "stage_message"]
    live_caps, opened = [], None
    for e in events:
        if e["kind"] == "captions_on":
            if opened:
                live_caps.append({**opened, "to": e["at"]})
            opened = {"from": e["at"], "source": e["detail"]}
        elif e["kind"] == "captions_off" and opened:
            live_caps.append({**opened, "to": e["at"]})
            opened = None
    if opened:
        live_caps.append({**opened, "to": None})

    msgs = c.execute(
        "SELECT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE ch.kind='room' AND ch.room_id=? "
        "AND m.deleted_at IS NULL AND m.created_at>=? AND m.created_at<? ORDER BY m.id",
        (room_id, iso(start), iso(end))).fetchall()
    crew = {"count": len(msgs), "important": sum(1 for m in msgs if m["priority"] == "important"),
            "urgent": sum(1 for m in msgs if m["priority"] == "urgent"),
            "messages": [{"at": ts(m["created_at"]), "sender": m["sender_name"], "priority": m["priority"],
                          "body": decrypt(m["body_enc"])} for m in msgs] if chat else []}

    lines: list[tuple[str, str]] = []
    for t in c.execute("SELECT * FROM transcripts WHERE room_id=? AND started_at<? AND (ended_at IS NULL OR ended_at>=?) "
                       "ORDER BY started_at", (room_id, iso(end), iso(start))):
        f = config.cfg.transcripts / t["path"]
        if not f.is_file():
            continue
        # Each line carries the server's own clock time; put it on the site's clock, which the
        # report shows (they differ when the server's time zone isn't the venue's).
        began = ts(t["started_at"]) or start
        day_local = datetime.fromtimestamp(began).astimezone()
        prev = None
        with f.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                m = re.match(r"^\[(\d\d):(\d\d):(\d\d)\]\s?(.*)$", raw.rstrip("\n"))
                if not m or not m.group(4).strip():
                    continue
                at = day_local.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=int(m.group(3)), microsecond=0)
                if prev and at < prev - timedelta(hours=1):  # past midnight
                    at += timedelta(days=1)
                prev = at
                lines.append((at.astimezone(zone).strftime("%H:%M:%S"), m.group(4).strip()))
                if len(lines) >= MAX_TRANSCRIPT_LINES:
                    break

    branding = get_branding(c)
    return {
        "room_id": room_id, "room": room["name"], "site": room["site_name"] or "", "day": day,
        "organisation": branding.get("organisation") or "", "product": branding.get("product_name") or "AT-SUIT",
        "zone": zone.key, "start": start, "end": end, "generated_at": now,
        "runs": runs, "help": calls, "stage_messages": stage, "crew_chat": crew,
        "captions": live_caps, "transcript": lines, "transcript_cut": len(lines) >= MAX_TRANSCRIPT_LINES,
        "summary": {
            "runs": len(runs), "over": sum(1 for r in runs if r["over_ms"] > 0),
            "over_ms": sum(r["over_ms"] for r in runs),
            "help": len(calls),
            "answer_s": (sum(h["answer_s"] for h in calls if h["answer_s"] is not None) //
                         max(1, sum(1 for h in calls if h["answer_s"] is not None))) if any(h["answer_s"] is not None for h in calls) else None,
            "escalated": sum(1 for h in calls if h["escalations"]),
            "stage_messages": len(stage), "crew_messages": crew["count"],
            "captions_s": int(sum(((x["to"] or min(now, end)) - x["from"]) for x in live_caps)),
            "transcript_lines": len(lines),
        },
    }


def had_show(c, room_id: int, day: str) -> bool:
    """Anything worth a report: the timer ran, a help call, a stage message or captions."""
    room = c.execute("SELECT site_id FROM rooms WHERE id=?", (room_id,)).fetchone()
    start, end = window(c, room["site_id"], day)
    return bool(
        c.execute("SELECT 1 FROM timer_runs WHERE room_id=? AND started_at>=? AND started_at<? LIMIT 1", (room_id, start, end)).fetchone()
        or c.execute("SELECT 1 FROM room_events WHERE room_id=? AND at>=? AND at<? LIMIT 1", (room_id, start, end)).fetchone()
        or c.execute("SELECT 1 FROM help_requests WHERE room_id=? AND created_at>=? AND created_at<? LIMIT 1",
                     (room_id, iso(start), iso(end))).fetchone())


# ------------------------------------------------------------------- PDF --
def mmss(ms: int | None, up: bool = False) -> str:
    """m:ss (or h:mm:ss). up rounds a part second up, so a run that went over never reads "over by 0:00"."""
    if ms is None:
        return "-"
    s = -(-abs(ms) // 1000) if up else int(round(abs(ms) / 1000))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def span(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds} s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m} min {s} s" if s else f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m} min"


def short(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


# The PDF's built-in fonts only have Latin-1; swap the usual typographic characters and drop the rest.
_SWAPS = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-",
                        "\u2026": "...", "\u2022": "*", "\u00a0": " ", "\u2192": "->", "\u2190": "<-"})


def latin(text) -> str:
    return str(text if text is not None else "").translate(_SWAPS).encode("latin-1", "replace").decode("latin-1")


def render_pdf(d: dict) -> bytes:
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    zone = ZoneInfo(d["zone"])
    hm = lambda t: datetime.fromtimestamp(t, zone).strftime("%H:%M") if t else "-"  # noqa: E731
    hms = lambda t: datetime.fromtimestamp(t, zone).strftime("%H:%M:%S") if t else "-"  # noqa: E731
    orange, navy, grey, red, green = (255, 122, 26), (11, 16, 32), (110, 117, 130), (200, 40, 40), (30, 130, 70)
    title_day = date.fromisoformat(d["day"]).strftime("%A %d %B %Y").replace(" 0", " ")
    footer = latin(f"{d['product']} show report  \u00b7  {d['room']}  \u00b7  {title_day}")
    made = latin(f"Made {datetime.fromtimestamp(d['generated_at'], zone).strftime('%d %b %Y %H:%M')} by {d['product']} {VERSION}")

    class Report(FPDF):
        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "", 7.5)
            self.set_text_color(*grey)
            self.cell(0, 5, footer, align="L")
            self.set_x(self.l_margin)
            self.cell(0, 5, f"Page {self.page_no()} of {{nb}}", align="R")

    pdf = Report(format="A4")
    pdf.set_margins(16, 16, 16)
    pdf.set_auto_page_break(True, margin=18)
    pdf.set_title(latin(f"Show report: {d['room']}, {title_day}"))
    pdf.set_author(latin(d["organisation"] or d["product"]))
    pdf.set_creator(latin(f"{d['product']} {VERSION}"))
    pdf.add_page()
    width = pdf.epw

    # Header: the orange bar, the product's mark, the room and the day.
    pdf.set_fill_color(*orange)
    pdf.rect(0, 0, pdf.w, 4, style="F")
    logo = STATIC / "brand" / "icon-96.png"
    top = 12
    if d["product"] == "AT-SUIT" and logo.is_file():
        pdf.image(str(logo), x=pdf.w - pdf.r_margin - 14, y=top, h=14)
    pdf.set_xy(pdf.l_margin, top)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*orange)
    pdf.cell(0, 5, latin(("SHOW REPORT" + (f"  \u00b7  {d['organisation'].upper()}" if d["organisation"] else ""))),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(*navy)
    pdf.cell(width - 18, 10, latin(d["room"]), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(*grey)
    where = f"{title_day}" + (f"  \u00b7  {d['site']}" if d["site"] else "")
    pdf.cell(0, 6, latin(where), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 5, latin(f"From {hm(d['start'])} to {hm(d['end'])} the next morning ({d['zone']}). {made}."),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Summary tiles.
    s = d["summary"]
    tiles = [
        ("Timed", str(s["runs"]), "runs of the timer"),
        ("Ran over", str(s["over"]), f"by {mmss(s['over_ms'], up=True)} in all" if s["over"] else "nothing ran over"),
        ("Help calls", str(s["help"]), f"avg answer {short(s['answer_s'])}" if s["answer_s"] is not None else "none answered" if s["help"] else "none"),
        ("Stage messages", str(s["stage_messages"]), "shown to the speaker"),
        ("Captions", span(s["captions_s"]) if s["captions_s"] else "Off", "live" if s["captions_s"] else "not used"),
    ]
    gap = 3
    tw = (width - gap * (len(tiles) - 1)) / len(tiles)
    y = pdf.get_y()
    for i, (label, value, note) in enumerate(tiles):
        x = pdf.l_margin + i * (tw + gap)
        pdf.set_fill_color(245, 246, 249)
        pdf.set_draw_color(225, 228, 235)
        pdf.rect(x, y, tw, 22, style="DF", round_corners=True, corner_radius=2)
        pdf.set_xy(x + 3, y + 2.5)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*grey)
        pdf.cell(tw - 6, 4, latin(label.upper()))
        pdf.set_xy(x + 3, y + 7)
        pdf.set_font("Helvetica", "B", 15 if len(value) < 8 else 11)
        pdf.set_text_color(*(red if label == "Ran over" and s["over"] else navy))
        pdf.cell(tw - 6, 7, latin(value))
        pdf.set_xy(x + 3, y + 15)
        pdf.set_font("Helvetica", "", 6.5)
        pdf.set_text_color(*grey)
        pdf.cell(tw - 6, 4, latin(note))
    pdf.set_y(y + 28)

    head = FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=navy, size_pt=8)

    def heading(text: str, note: str = "") -> None:
        if pdf.get_y() > pdf.h - 50:
            pdf.add_page()
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(*navy)
        pdf.cell(0, 8, latin(text), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(*orange)
        pdf.set_line_width(0.6)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + 18, pdf.get_y())
        pdf.set_line_width(0.2)
        pdf.ln(2)
        if note:
            para(note)

    def para(text: str, size: float = 9, colour=grey) -> None:
        pdf.set_font("Helvetica", "", size)
        pdf.set_text_color(*colour)
        pdf.multi_cell(0, 4.6, latin(text), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    def table(widths, header, rows, colours=None) -> None:
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*navy)
        pdf.set_draw_color(225, 228, 235)
        with pdf.table(col_widths=widths, headings_style=head, line_height=4.8, padding=1.4,
                       cell_fill_color=(248, 249, 251), cell_fill_mode="ROWS", borders_layout="HORIZONTAL_LINES",
                       first_row_as_headings=True) as t:
            t.row([latin(h) for h in header])
            for i, r in enumerate(rows):
                row = t.row()
                for j, x in enumerate(r):
                    # a colour is for the last column only (the result)
                    last = colours and colours[i] and j == len(r) - 1
                    row.cell(latin(x), style=FontFace(color=colours[i], emphasis="BOLD") if last else None)
        pdf.ln(2)

    # Timer.
    heading("Timer", "" if d["runs"] else "The timer wasn't run in this room on this day.")
    if d["runs"]:
        rows, colours = [], []
        for r in d["runs"]:
            if r["over_ms"]:
                result, colour = f"over by {mmss(r['over_ms'], up=True)}", red
            elif r["running"]:
                result, colour = "still running", None
            elif r["remaining_ms"] is None:
                result, colour = "not known", None
            elif r["early_ms"]:
                result, colour = f"{mmss(r['early_ms'])} to spare", green
            elif r["timer_type"] == "count-down" and r["duration_ms"]:
                result, colour = "on time", green
            else:
                result, colour = {"count-up": "counted up", "clock": "clock"}.get(r["timer_type"], ""), None
            planned = mmss(r["duration_ms"]) if r["duration_ms"] else "-"
            if r["added_ms"]:
                planned += f" {'+' if r['added_ms'] > 0 else '-'}{mmss(r['added_ms'])}"
            rows.append((hm(r["started_at"]), r["cue"] or "", r["title"] or "(quick timer)", planned, mmss(r["ran_ms"]), result))
            colours.append(colour)
        table((12, 12, 70, 26, 18, 32), ("Start", "Cue", "Session", "Planned", "Ran", "Result"), rows, colours)

    # Help calls.
    heading("Help calls", "" if d["help"] else "No help calls were made from this room.")
    if d["help"]:
        rows = []
        for h in d["help"]:
            what = h["category"] + (f": {h['description']}" if h["description"] else "")
            if h["escalations"]:
                what += " (no answer at first, sent out again)"
            answered = f"{h['assigned_to']} in {span(h['answer_s'])}" if h["answer_s"] is not None else (h["assigned_to"] or "nobody")
            done = f"after {span(h['resolved_s'])}" if h["resolved_s"] is not None else h["status"]
            rows.append((hm(h["created_at"]), h["requested_by"], what, answered, done))
        table((12, 26, 72, 38, 22), ("Time", "From", "What", "Answered", "Resolved"), rows)

    # Messages.
    heading("Messages")
    if d["stage_messages"]:
        para("Shown to the speaker on the stage screens:", 9, navy)
        table((14, 156), ("Time", "Message"), [(hm(m["at"]), m["text"]) for m in d["stage_messages"]])
    else:
        para("No messages were shown to the speaker on the stage screens.")
    cc = d["crew_chat"]
    para(f"The crew sent {cc['count']} message{'s' if cc['count'] != 1 else ''} in the room's chat"
         + (" (" + ", ".join(f"{n} {k}" for k, n in (("urgent", cc["urgent"]), ("important", cc["important"])) if n) + ")"
            if cc["urgent"] or cc["important"] else "") + "."
         + ("" if cc["messages"] or not cc["count"] else " Their content stays private to the crew."))
    if cc["messages"]:
        table((14, 30, 126), ("Time", "From", "Message"),
              [(hm(m["at"]), m["sender"], (f"[{m['priority']}] " if m["priority"] != "normal" else "") + (m["body"] or "(a file)"))
               for m in cc["messages"]])

    # Captions.
    heading("Captions")
    if d["captions"]:
        para("Live: " + "; ".join(f"{hm(x['from'])} to {hm(x['to']) if x['to'] else 'still live'}"
                                  + (f" (mic: {x['source']})" if x["source"] else "") for x in d["captions"]) + ".")
    if d["transcript"]:
        para("Transcript, as captioned live (not checked by a person):", 9, navy)
        pdf.set_font("Helvetica", "", 8.5)
        for at, text in d["transcript"]:
            pdf.set_text_color(*grey)
            pdf.cell(16, 4.4, at[:5])
            pdf.set_text_color(*navy)
            pdf.multi_cell(0, 4.4, latin(text), new_x="LMARGIN", new_y="NEXT")
        if d["transcript_cut"]:
            para(f"The transcript is cut at {MAX_TRANSCRIPT_LINES} lines.")
    elif d["captions"]:
        para("No transcript was saved. To keep one, turn on Record in the room's caption settings.")
    else:
        para("Captions weren't used in this room on this day.")

    return bytes(pdf.output())


# --------------------------------------------------------------- storage --
def reports_dir() -> Path:
    d = config.cfg.data / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(c, room_id: int, day: str) -> dict:
    """Make the report and keep it (replacing one already kept for that room and day)."""
    data = collect(c, room_id, day)
    pdf = render_pdf(data)
    name = f"{day}/{re.sub(r'[^A-Za-z0-9]+', '-', data['room']).strip('-') or 'room'}-{room_id}.pdf"
    path = reports_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
    c.execute("DELETE FROM show_reports WHERE room_id=? AND day=?", (room_id, day))
    rid = c.execute("INSERT INTO show_reports(room_id,room_name,day,path,created_at) VALUES(?,?,?,?,?)",
                    (room_id, data["room"], day, name, db.now_iso())).lastrowid
    return {"id": rid, "path": name, "summary": data["summary"]}


def save_due(now: float | None = None) -> list[dict]:
    """Keep the report of every room that had a show on the day that has just ended (and the
    few days before, in case the server was off that morning)."""
    now = now or time.time()
    saved = []
    with db.tx() as c:
        for room in c.execute("SELECT id, site_id FROM rooms").fetchall():
            today = date.fromisoformat(show_day(c, room["site_id"], now))
            for back in (1, 2, 3):
                day = (today - timedelta(days=back)).isoformat()
                if c.execute("SELECT 1 FROM show_reports WHERE room_id=? AND day=?", (room["id"], day)).fetchone():
                    continue
                if had_show(c, room["id"], day):
                    saved.append({"room_id": room["id"], "day": day, **save(c, room["id"], day)})
    return saved


async def save_loop() -> None:
    while True:
        await asyncio.sleep(600)
        try:
            await asyncio.to_thread(save_due)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # try again next time
            print("reports.save:", exc)


# ------------------------------------------------------------------- API --
def _day(c, room, day: str | None) -> str:
    day = day or show_day(c, room["site_id"])
    if not DAY.match(day):
        raise HTTPException(400, "Give the day as YYYY-MM-DD")
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "That isn't a date") from None
    return day


@router.get("/api/reports")
def list_reports(p: Principal = Depends(require_manager)):
    """The kept reports, newest first, and today's show day for each site."""
    with db.ro() as c:
        rows = db.rows(c.execute("SELECT sr.*, r.site_id FROM show_reports sr LEFT JOIN rooms r ON r.id=sr.room_id "
                                 "ORDER BY sr.day DESC, sr.room_name LIMIT 500"))
        sites = {s["id"]: show_day(c, s["id"]) for s in c.execute("SELECT id FROM sites")}
    # A report whose room has since been deleted has no site any more: only an all-sites manager sees it.
    shown = [r for r in rows if (site_ok(p, r["site_id"]) if r["site_id"] is not None else p.site_id is None)]
    return {"reports": shown, "today": sites}


@router.get("/api/reports/{room_id}")
def report_data(room_id: int, day: str | None = None, p: Principal = Depends(require_manager)):
    """The report's figures, for the console to show before the PDF is opened."""
    with db.ro() as c:
        room = room_or_404(c, room_id, p)
        d = collect(c, room_id, _day(c, room, day))
    d.pop("transcript")
    return d


@router.get("/api/reports/{room_id}/pdf")
def report_pdf(room_id: int, day: str | None = None, chat: bool = False, p: Principal = Depends(require_manager)):
    """The report as it stands now (a day still going shows what has happened so far)."""
    with db.ro() as c:
        room = room_or_404(c, room_id, p)
        day = _day(c, room, day)
        d = collect(c, room_id, day, chat=chat)
    name = f"Show report {d['room']} {day}.pdf".replace('"', "")
    return Response(render_pdf(d), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{latin(name)}"', "Cache-Control": "no-store"})


@router.post("/api/reports/{room_id}/save")
def report_save(room_id: int, day: str | None = None, p: Principal = Depends(require_manager)):
    with db.tx() as c:
        room = room_or_404(c, room_id, p)
        day = _day(c, room, day)
        out = save(c, room_id, day)
        db.audit(c, p.name, "report.save", f"{room['name']} {day}")
    return out


def _kept(c, report_id: int, p: Principal):
    r = c.execute("SELECT sr.*, r.site_id FROM show_reports sr LEFT JOIN rooms r ON r.id=sr.room_id WHERE sr.id=?",
                  (report_id,)).fetchone()
    if not r or (r["site_id"] is not None and not site_ok(p, r["site_id"])) or (r["site_id"] is None and p.site_id is not None):
        raise HTTPException(404, "Report not found")
    return r


@router.get("/api/reports/saved/{report_id}.pdf")
def saved_pdf(report_id: int, p: Principal = Depends(require_manager)):
    with db.ro() as c:
        r = _kept(c, report_id, p)
    path = (reports_dir() / r["path"]).resolve()
    if reports_dir().resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "The report's file is missing")
    return FileResponse(path, media_type="application/pdf", filename=f"Show report {r['room_name']} {r['day']}.pdf",
                        content_disposition_type="inline")


@router.delete("/api/reports/saved/{report_id}")
def delete_saved(report_id: int, p: Principal = Depends(require_manager)):
    with db.tx() as c:
        r = _kept(c, report_id, p)
        (reports_dir() / r["path"]).unlink(missing_ok=True)
        c.execute("DELETE FROM show_reports WHERE id=?", (report_id,))
        db.audit(c, p.name, "report.delete", f"{r['room_name']} {r['day']}")
    return {"ok": True}
