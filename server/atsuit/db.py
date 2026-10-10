"""SQLite storage with forward-only migrations."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

MIGRATIONS: list[str] = [
    # 1: core, comms, fleet, timers, dashboard, captions, overlays
    """
    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE sites(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
        timezone TEXT NOT NULL DEFAULT 'Europe/London', enrol_code TEXT NOT NULL);
    CREATE TABLE rooms(id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
        name TEXT NOT NULL, short_name TEXT NOT NULL DEFAULT '', sort INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1, UNIQUE(site_id, name));
    CREATE TABLE accounts(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
        password_hash TEXT NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'tech',
        site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL, active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL);
    CREATE TABLE sessions(token_hash TEXT PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
    CREATE TABLE api_keys(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, key_hash TEXT UNIQUE NOT NULL,
        prefix TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL,
        action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');

    CREATE TABLE channels(id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER REFERENCES sites(id) ON DELETE CASCADE,
        room_id INTEGER REFERENCES rooms(id) ON DELETE CASCADE, kind TEXT NOT NULL, name TEXT NOT NULL,
        dm_key TEXT UNIQUE);
    CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
        sender_id INTEGER, sender_name TEXT NOT NULL, body_enc TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'normal',
        created_at TEXT NOT NULL, edited_at TEXT, deleted_at TEXT);
    CREATE INDEX messages_channel ON messages(channel_id, id);
    CREATE TABLE attachments(id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        original_name TEXT NOT NULL, stored_name TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL);
    CREATE TABLE message_reads(message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        account_id INTEGER NOT NULL, read_at TEXT NOT NULL, PRIMARY KEY(message_id, account_id));
    CREATE TABLE help_requests(id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER, room_id INTEGER REFERENCES rooms(id) ON DELETE SET NULL,
        room_name TEXT NOT NULL DEFAULT '', requested_by TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'general',
        description TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'open',
        assigned_to TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, acknowledged_at TEXT, resolved_at TEXT);

    CREATE TABLE timers(room_id INTEGER PRIMARY KEY REFERENCES rooms(id) ON DELETE CASCADE, title TEXT NOT NULL DEFAULT '',
        duration_ms INTEGER NOT NULL DEFAULT 0, started_at REAL, remaining_ms INTEGER NOT NULL DEFAULT 0,
        running INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', message_visible INTEGER NOT NULL DEFAULT 0,
        warn_ms INTEGER NOT NULL DEFAULT 300000, danger_ms INTEGER NOT NULL DEFAULT 60000, updated_at TEXT NOT NULL);

    CREATE TABLE nodes(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL COLLATE NOCASE,
        site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL, room_id INTEGER REFERENCES rooms(id) ON DELETE SET NULL,
        kind TEXT NOT NULL DEFAULT 'tech', token_hash TEXT, legacy INTEGER NOT NULL DEFAULT 0,
        ip TEXT NOT NULL DEFAULT '', mac TEXT NOT NULL DEFAULT '', version TEXT NOT NULL DEFAULT '',
        current_url TEXT NOT NULL DEFAULT '', last_seen REAL, info_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
    CREATE TABLE node_commands(id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        kind TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'queued',
        created_at TEXT NOT NULL, acked_at TEXT);

    CREATE TABLE links(id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER REFERENCES sites(id) ON DELETE CASCADE,
        room_id INTEGER REFERENCES rooms(id) ON DELETE CASCADE, board TEXT NOT NULL DEFAULT 'public',
        label TEXT NOT NULL, url TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'link', sort INTEGER NOT NULL DEFAULT 0);

    CREATE TABLE caption_rooms(room_id INTEGER PRIMARY KEY REFERENCES rooms(id) ON DELETE CASCADE,
        enabled INTEGER NOT NULL DEFAULT 1, vocabulary TEXT NOT NULL DEFAULT '', record INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE transcripts(id INTEGER PRIMARY KEY AUTOINCREMENT, room_id INTEGER, started_at TEXT NOT NULL,
        ended_at TEXT, path TEXT NOT NULL);

    CREATE TABLE overlay_targets(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        room_id INTEGER REFERENCES rooms(id) ON DELETE SET NULL, base_url TEXT NOT NULL, token_enc TEXT NOT NULL DEFAULT '');
    """,
    # 2: tech laptops pick their room each day
    """
    ALTER TABLE nodes ADD COLUMN room_day TEXT NOT NULL DEFAULT '';
    """,
    # 3: Ontime-style cue lists, custom timer views, screen routing
    """
    CREATE TABLE cues(id INTEGER PRIMARY KEY AUTOINCREMENT, room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
        sort INTEGER NOT NULL DEFAULT 0, cue TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '', duration_ms INTEGER NOT NULL DEFAULT 0, time_start TEXT NOT NULL DEFAULT '',
        timer_type TEXT NOT NULL DEFAULT 'count-down', end_action TEXT NOT NULL DEFAULT 'none',
        skip INTEGER NOT NULL DEFAULT 0, colour TEXT NOT NULL DEFAULT '', warn_ms INTEGER, danger_ms INTEGER,
        custom_json TEXT NOT NULL DEFAULT '{}');
    CREATE INDEX cues_room ON cues(room_id, sort);
    ALTER TABLE timers ADD COLUMN cue_id INTEGER;
    ALTER TABLE timers ADD COLUMN timer_type TEXT NOT NULL DEFAULT 'count-down';
    ALTER TABLE timers ADD COLUMN end_action TEXT NOT NULL DEFAULT 'none';
    ALTER TABLE timers ADD COLUMN first_started_at REAL;
    ALTER TABLE timers ADD COLUMN added_ms INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE timers ADD COLUMN message_blink INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE timers ADD COLUMN blackout INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE nodes ADD COLUMN screen_view TEXT NOT NULL DEFAULT '';
    CREATE TABLE timer_views(slug TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
    """,
    # 4: flash at danger (the venue's Ontime automation), presenter module
    """
    ALTER TABLE timers ADD COLUMN flash_danger INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE rooms ADD COLUMN sync_code TEXT;
    CREATE UNIQUE INDEX rooms_sync_code ON rooms(sync_code) WHERE sync_code IS NOT NULL;
    CREATE TABLE pr_events(id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
        name TEXT NOT NULL, client TEXT NOT NULL DEFAULT '', colour TEXT NOT NULL DEFAULT '#8b5cf6',
        starts_on TEXT NOT NULL DEFAULT '', ends_on TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'planning',
        archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
    CREATE TABLE pr_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL REFERENCES pr_events(id) ON DELETE CASCADE,
        room_id INTEGER REFERENCES rooms(id) ON DELETE SET NULL, title TEXT NOT NULL, starts_at TEXT NOT NULL DEFAULT '',
        ends_at TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', sort INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
    CREATE INDEX pr_sessions_event ON pr_sessions(event_id, starts_at);
    CREATE INDEX pr_sessions_room ON pr_sessions(room_id, starts_at);
    CREATE TABLE pr_presenters(id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL REFERENCES pr_events(id) ON DELETE CASCADE,
        session_id INTEGER REFERENCES pr_sessions(id) ON DELETE SET NULL, full_name TEXT NOT NULL,
        email_enc TEXT NOT NULL DEFAULT '', phone_enc TEXT NOT NULL DEFAULT '', token TEXT UNIQUE NOT NULL,
        checked_in_at TEXT, created_at TEXT NOT NULL);
    CREATE TABLE pr_files(id INTEGER PRIMARY KEY AUTOINCREMENT, presenter_id INTEGER NOT NULL REFERENCES pr_presenters(id) ON DELETE CASCADE,
        original_name TEXT NOT NULL, stored_path TEXT NOT NULL, mime TEXT NOT NULL DEFAULT '', size INTEGER NOT NULL,
        sha256 TEXT NOT NULL, review_status TEXT NOT NULL DEFAULT 'pending', review_note TEXT NOT NULL DEFAULT '',
        uploaded_at TEXT NOT NULL, uploaded_by TEXT NOT NULL DEFAULT '', reviewed_at TEXT, reviewed_by TEXT);
    CREATE TABLE pr_show_files(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL REFERENCES pr_sessions(id) ON DELETE CASCADE,
        kind TEXT NOT NULL DEFAULT 'presentation', label TEXT NOT NULL DEFAULT '', original_name TEXT NOT NULL,
        stored_path TEXT NOT NULL, mime TEXT NOT NULL DEFAULT '', size INTEGER NOT NULL, sha256 TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, created_by TEXT NOT NULL DEFAULT '');
    CREATE TABLE pr_imports(id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL REFERENCES pr_events(id) ON DELETE CASCADE,
        filename TEXT NOT NULL, method TEXT NOT NULL, rows_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'review',
        created_by TEXT NOT NULL, created_at TEXT NOT NULL, committed_at TEXT);
    """,
    # 5: a tech laptop's person and whether it's the main or backup PC
    """
    ALTER TABLE nodes ADD COLUMN operator TEXT NOT NULL DEFAULT '';
    ALTER TABLE nodes ADD COLUMN mode TEXT NOT NULL DEFAULT '';
    """,
    # 6: the time of day on the stage screens, timer views built in the console
    """
    ALTER TABLE timers ADD COLUMN show_clock INTEGER NOT NULL DEFAULT 0;
    CREATE TABLE timer_designs(slug TEXT PRIMARY KEY, name TEXT NOT NULL, config_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL);
    """,
    # 7: a second, smaller line under the stage timer: a countdown or a text
    """
    ALTER TABLE timers ADD COLUMN sec_mode TEXT NOT NULL DEFAULT 'text';
    ALTER TABLE timers ADD COLUMN sec_visible INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE timers ADD COLUMN sec_text TEXT NOT NULL DEFAULT '';
    ALTER TABLE timers ADD COLUMN sec_duration_ms INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE timers ADD COLUMN sec_remaining_ms INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE timers ADD COLUMN sec_started_at REAL;
    """,
    # 8: emoji reactions on chat messages
    """
    CREATE TABLE message_reactions(message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
        who TEXT NOT NULL, name TEXT NOT NULL, emoji TEXT NOT NULL, PRIMARY KEY(message_id, who, emoji));
    """,
    # 9: helper servers that take work off this one
    """
    CREATE TABLE helpers(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL COLLATE NOCASE,
        secret_hash TEXT UNIQUE NOT NULL, added_at TEXT NOT NULL, last_seen TEXT);
    """,
    # 10: handover notes, kept per room until a tech ticks them off
    """
    CREATE TABLE room_notes(id INTEGER PRIMARY KEY AUTOINCREMENT, room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
        body_enc TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0, author TEXT NOT NULL, created_at TEXT NOT NULL,
        edited_at TEXT, done_at TEXT, done_by TEXT);
    CREATE INDEX room_notes_room ON room_notes(room_id, done_at);
    """,
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    c = sqlite3.connect(config.cfg.db_path, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=5000")
    return c


@contextmanager
def tx():
    """One connection, one transaction. Commits on success."""
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise
    finally:
        c.close()


@contextmanager
def ro():
    c = connect()
    try:
        yield c
    finally:
        c.close()


def migrate() -> int:
    config.cfg.ensure_dirs()
    c = connect()
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL)")
        row = c.execute("SELECT version FROM schema_version").fetchone()
        current = row[0] if row else 0
        if not row:
            c.execute("INSERT INTO schema_version(version) VALUES(0)")
        for i, script in enumerate(MIGRATIONS[current:], start=current + 1):
            c.executescript("BEGIN;" + script + f";UPDATE schema_version SET version={i};COMMIT;")
        return len(MIGRATIONS)
    finally:
        c.close()


def get_setting(c: sqlite3.Connection, key: str, default=None):
    r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if not r:
        return default
    try:
        return json.loads(r["value"])
    except ValueError:
        return r["value"]


def set_setting(c: sqlite3.Connection, key: str, value) -> None:
    c.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


def audit(c: sqlite3.Connection, actor: str, action: str, detail: str = "") -> None:
    c.execute("INSERT INTO audit_log(at,actor,action,detail) VALUES(?,?,?,?)", (now_iso(), actor, action, detail))


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]
