"""SQLite 持久化层。

存储邮件元数据以及重建后的线程图（每个节点的最终父节点）。
导入新邮件后调用 rebuild_threads() 重新计算整棵会话森林。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "emails.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    message_id   TEXT PRIMARY KEY,
    subject      TEXT,
    in_reply_to  TEXT,
    refs_json    TEXT NOT NULL DEFAULT '[]',
    date_raw     TEXT,
    date_ts      REAL,
    body_from    TEXT,
    placeholder  INTEGER NOT NULL DEFAULT 0,
    parent_id    TEXT,
    is_cycle     INTEGER NOT NULL DEFAULT 0,
    broken_edge  TEXT,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emails_parent ON emails(parent_id);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def reset_db() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM emails")


def upsert_email(conn: sqlite3.Connection, msg: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO emails
            (message_id, subject, in_reply_to, refs_json, date_raw, date_ts,
             body_from, placeholder, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            subject     = excluded.subject,
            in_reply_to = excluded.in_reply_to,
            refs_json   = excluded.refs_json,
            date_raw    = excluded.date_raw,
            date_ts     = excluded.date_ts,
            body_from   = excluded.body_from,
            updated_at  = excluded.updated_at
        """,
        (
            msg["message_id"],
            msg.get("subject"),
            msg.get("in_reply_to"),
            json.dumps(msg.get("references", []), ensure_ascii=False),
            msg.get("date_raw"),
            msg.get("date_ts"),
            msg.get("from"),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def insert_messages(messages: Iterable[dict[str, Any]]) -> int:
    n = 0
    with _connect() as conn:
        for msg in messages:
            upsert_email(conn, msg)
            n += 1
    return n


def fetch_all_rows() -> list[sqlite3.Row]:
    with _connect() as conn:
        return list(conn.execute("SELECT * FROM emails ORDER BY message_id"))


def _ensure_placeholder(conn: sqlite3.Connection, message_id: str) -> None:
    conn.execute(
        """
        INSERT INTO emails
            (message_id, subject, in_reply_to, refs_json, date_raw, date_ts,
             body_from, placeholder, updated_at)
        VALUES (?, NULL, NULL, '[]', NULL, NULL, NULL, 1, ?)
        ON CONFLICT(message_id) DO UPDATE SET placeholder = 1
        """,
        (message_id, datetime.now(timezone.utc).isoformat()),
    )


def save_graph(
    parents: dict[str, str | None],
    cycles: dict[str, str],
    missing_refs: set[str],
) -> None:
    """把重建结果写回数据库。

    parents[id] 为最终父节点（已断开环、已合并主题）；
    cycles[id] -> 被断开的父 Message-ID；
    missing_refs 为所有被引用但不存在的 Message-ID，需建占位根。
    """
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        for mid in missing_refs:
            _ensure_placeholder(conn, mid)
        for mid, parent in parents.items():
            broken = cycles.get(mid)
            conn.execute(
                """
                UPDATE emails
                SET parent_id = ?, is_cycle = ?, broken_edge = ?
                WHERE message_id = ?
                """,
                (parent, 1 if broken else 0, broken, mid),
            )
        # 占位节点自身也需要刷新 updated_at
        for mid in missing_refs:
            conn.execute(
                "UPDATE emails SET updated_at = ? WHERE message_id = ?",
                (now, mid),
            )
