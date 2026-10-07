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
