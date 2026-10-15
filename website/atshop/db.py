"""SQLite, one file in SHOP_DATA. Small and plenty for a shop: every write is short."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS = [
    # 1: everything
    """
    CREATE TABLE accounts (
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL DEFAULT '',
        organisation TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        paddle_customer_id TEXT UNIQUE,
        totp_secret TEXT,
        marketing INTEGER NOT NULL DEFAULT 0,
        disabled INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        last_login_at INTEGER
    );
    CREATE TABLE login_tokens (
        token_hash TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        data TEXT NOT NULL DEFAULT '{}',
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        used_at INTEGER,
        ip TEXT
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        csrf TEXT NOT NULL,
        second_factor INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        ip TEXT, user_agent TEXT
    );
    CREATE TABLE orders (
        id INTEGER PRIMARY KEY,
        ref TEXT NOT NULL UNIQUE,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        kind TEXT NOT NULL,            -- subscription | event | hosting
        plan TEXT, cycle TEXT,
        hosting INTEGER NOT NULL DEFAULT 0,
        venue_slug TEXT, venue_name TEXT,
        event_start TEXT,
        paddle_transaction_id TEXT UNIQUE,
        status TEXT NOT NULL DEFAULT 'open',  -- open | paid | abandoned
        created_at INTEGER NOT NULL
    );
    CREATE TABLE subscriptions (
        id TEXT PRIMARY KEY,           -- Paddle sub_…
        account_id INTEGER REFERENCES accounts(id),
        paddle_customer_id TEXT,
        status TEXT NOT NULL,          -- active | trialing | past_due | paused | canceled
        plan TEXT, cycle TEXT,
        hosting INTEGER NOT NULL DEFAULT 0,
        items TEXT NOT NULL DEFAULT '[]',
        period_start TEXT, period_end TEXT, next_billed_at TEXT,
        scheduled_change TEXT,
        problem TEXT,                  -- set when a price we don't know turns up
        event_at TEXT NOT NULL,        -- occurred_at of the newest event applied (older ones are skipped)
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    CREATE TABLE transactions (
        id TEXT PRIMARY KEY,           -- Paddle txn_…
        account_id INTEGER REFERENCES accounts(id),
        subscription_id TEXT,
        status TEXT NOT NULL,
        origin TEXT,
        total TEXT, currency TEXT,
        period_start TEXT, period_end TEXT,
        billed_at TEXT,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE licences (
        id INTEGER PRIMARY KEY,
        serial TEXT NOT NULL UNIQUE,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        subscription_id TEXT,
        transaction_id TEXT,
        source TEXT NOT NULL,          -- subscription | trial | event | manual
        edition TEXT NOT NULL,
        licensee TEXT NOT NULL,
        max_nodes INTEGER NOT NULL,
        max_sites INTEGER NOT NULL,
        expires INTEGER NOT NULL,
        issued INTEGER NOT NULL,
        key TEXT NOT NULL,
        replaced_by TEXT,
        revoked_at INTEGER,
        note TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX licences_account ON licences(account_id);
    CREATE INDEX licences_sub ON licences(subscription_id);
    CREATE UNIQUE INDEX licences_txn ON licences(transaction_id, subscription_id) WHERE transaction_id IS NOT NULL;
    CREATE TABLE hosting (
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        subscription_id TEXT,
        slug TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',  -- pending | active | suspended | ended
        setup_link TEXT,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE hosting_jobs (
        id INTEGER PRIMARY KEY,
        hosting_id INTEGER NOT NULL REFERENCES hosting(id),
        action TEXT NOT NULL,          -- new | licence | suspend | resume
        licence_serial TEXT,
        status TEXT NOT NULL DEFAULT 'todo',  -- todo | done | failed
        result TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        done_at INTEGER
    );
    CREATE TABLE webhook_events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        occurred_at TEXT,
        received_at INTEGER NOT NULL,
        status TEXT NOT NULL,          -- processed | ignored | failed
        note TEXT NOT NULL DEFAULT '',
        payload TEXT NOT NULL
    );
    CREATE TABLE tickets (
        id INTEGER PRIMARY KEY,
        account_id INTEGER REFERENCES accounts(id),
        email TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        organisation TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL,            -- support | sales | privacy | billing
        subject TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',  -- open | answered | closed
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    CREATE TABLE ticket_messages (
        id INTEGER PRIMARY KEY,
        ticket_id INTEGER NOT NULL REFERENCES tickets(id),
        author TEXT NOT NULL,          -- customer | staff
        author_email TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE emails (
        id INTEGER PRIMARY KEY,
        to_addr TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'queued',  -- queued | sent | failed | kept (no SMTP)
        error TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        sent_at INTEGER
    );
    CREATE TABLE notices (
        account_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        ref TEXT NOT NULL,
        sent_at INTEGER NOT NULL,
        PRIMARY KEY (account_id, kind, ref)
    );
    CREATE TABLE audit (
        id INTEGER PRIMARY KEY,
        at INTEGER NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        target TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '{}',
        ip TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX audit_at ON audit(at);
    CREATE TABLE rate (
        key TEXT PRIMARY KEY,
        window INTEGER NOT NULL,
        count INTEGER NOT NULL
    );
    """,
]


class DB:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self.migrate()

    def migrate(self) -> None:
        with self._lock:
            v = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for i, sql in enumerate(MIGRATIONS[v:], start=v + 1):
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in sql.split(";"):
                        if stmt.strip():
                            self._conn.execute(stmt)
                    self._conn.execute(f"PRAGMA user_version={i}")
                    self._conn.execute("COMMIT")
                except BaseException:
                    self._conn.execute("ROLLBACK")
                    raise

    @contextmanager
    def tx(self):
        """One write transaction. Everything inside commits together or not at all."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def one(self, sql: str, *args) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, args).fetchone()

    def all(self, sql: str, *args) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def run(self, sql: str, *args) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, args)

    def close(self) -> None:
        self._conn.close()


def now() -> int:
    return int(time.time())


def audit(c, actor: str, action: str, target: str = "", detail: dict | None = None, ip: str = "") -> None:
    c.execute("INSERT INTO audit (at, actor, action, target, detail, ip) VALUES (?,?,?,?,?,?)",
              (now(), actor, action, target, json.dumps(detail or {}, default=str), ip or ""))
