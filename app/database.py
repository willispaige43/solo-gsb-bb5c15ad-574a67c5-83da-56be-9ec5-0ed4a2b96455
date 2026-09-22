"""SQLite 存储层（标准库 sqlite3，按 Message-ID 幂等 upsert）。"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR = Path(os.environ.get("DATA_DIR", str(_DEFAULT_DATA_DIR)))
DB_PATH = DATA_DIR / "mail.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  TEXT UNIQUE NOT NULL,
    subject     TEXT NOT NULL DEFAULT '',
    sender      TEXT NOT NULL DEFAULT '',
    date        TEXT NOT NULL DEFAULT '',
    date_ts     INTEGER NOT NULL DEFAULT 0,
    in_reply_to TEXT NOT NULL DEFAULT '',
    "references" TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_messages_sort ON messages(date_ts, message_id);
"""


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(_SCHEMA)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def upsert_message(meta: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO messages (message_id, subject, sender, date, date_ts, in_reply_to, "references")
            VALUES (:message_id, :subject, :sender, :date, :date_ts, :in_reply_to, :references)
            ON CONFLICT(message_id) DO UPDATE SET
                subject=excluded.subject, sender=excluded.sender, date=excluded.date,
                date_ts=excluded.date_ts, in_reply_to=excluded.in_reply_to,
                "references"=excluded."references"
            """,
            meta,
        )


def fetch_all() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages ORDER BY date_ts, message_id"
        ).fetchall()
    return [dict(r) for r in rows]


def clear_all() -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM messages")
