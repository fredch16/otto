"""Fred's Pending list in OTTO's database: drafts waiting for him to review and send.

The mail server only adds entries; Fred marks them done or dismisses them in
OTTO's web UI (Pending). Keep SCHEMA in sync with otto_paperless_mcp.pending.
"""
from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

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


def add(database_url: str, kind: str, title: str, purpose: str, detail: dict, created_by: str) -> int:
    with psycopg.connect(database_url) as db:
        db.execute(SCHEMA)
        return db.execute("INSERT INTO pending (kind, title, purpose, detail, created_by) "
                          "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                          (kind, title, purpose, Jsonb(detail), created_by)).fetchone()[0]
