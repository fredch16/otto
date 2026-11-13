"""Fred's Pending list: things OTTO prepared that wait for him, such as drafts to
review and send. The mail server adds them (otto_mail_mcp.pending); Fred closes
them in the web UI. Keep SCHEMA in sync with otto_mail_mcp.pending.
"""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending (
    id bigserial PRIMARY KEY,
    kind text NOT NULL,  -- 'draft'
    title text NOT NULL,
    purpose text NOT NULL DEFAULT '',  -- why, and what Fred should check
    detail jsonb NOT NULL,  -- recipients, subject, body, attachments, the message it answers
    created_by text NOT NULL,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'dismissed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz
);
"""


class PendingError(Exception):
    """A request the caller can explain."""


class Pending:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def connect(self) -> psycopg.Connection:
        db = psycopg.connect(self.database_url, row_factory=dict_row)
        db.execute(SCHEMA)
        db.commit()
        return db

    def items(self, open_only: bool = True, limit: int = 20) -> list[dict]:
        """Open items oldest first, or closed ones newest first."""
        with self.connect() as db:
            if open_only:
                return db.execute("SELECT * FROM pending WHERE status = 'open' ORDER BY created_at").fetchall()
            return db.execute("SELECT * FROM pending WHERE status <> 'open' ORDER BY closed_at DESC LIMIT %s",
                              (limit,)).fetchall()

    def count_open(self) -> int:
        with self.connect() as db:
            return db.execute("SELECT count(*) AS n FROM pending WHERE status = 'open'").fetchone()["n"]

    def close(self, item_id: int, status: str) -> dict:
        if status not in ("done", "dismissed", "open"):
            raise PendingError(f"Unknown status {status!r}.")
        with self.connect() as db:
            row = db.execute("UPDATE pending SET status = %s, closed_at = CASE WHEN %s = 'open' THEN NULL ELSE now() END "
                             "WHERE id = %s RETURNING *", (status, status, item_id)).fetchone()
            db.commit()
        if row is None:
            raise PendingError(f"No pending item #{item_id}.")
        return row
