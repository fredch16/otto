"""OTTO's document index: page texts and passage embeddings in PostgreSQL/pgvector.

Paperless stays the source of truth. The index only holds documents visible to
`otto-agent` and can be rebuilt from Paperless at any time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector

from .chunking import chunk_pages
from .embedding import DIMENSIONS
from .extraction import unreadable
from .paperless import Page, PaperlessClient

log = logging.getLogger(__name__)
PIPELINE_VERSION = 3  # bump when extraction or chunking changes; forces re-indexing
RRF_K = 60  # reciprocal rank fusion constant
CANDIDATES = 50  # per signal, before fusion

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS indexed_documents (
    id integer PRIMARY KEY,
    title text NOT NULL,
    modified text NOT NULL,
    pipeline text NOT NULL,
    indexed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS pages (
    document_id integer NOT NULL REFERENCES indexed_documents ON DELETE CASCADE,
    page integer NOT NULL,
    text text NOT NULL,
    source text NOT NULL,
    PRIMARY KEY (document_id, page)
);
CREATE TABLE IF NOT EXISTS chunks (
    id bigserial PRIMARY KEY,
    document_id integer NOT NULL REFERENCES indexed_documents ON DELETE CASCADE,
    page integer NOT NULL,
    ordinal integer NOT NULL,
    text text NOT NULL,
    embedding vector({DIMENSIONS}) NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('german', text) || to_tsvector('english', text)
    ) STORED
);
CREATE INDEX IF NOT EXISTS chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_document ON chunks (document_id);
"""

# Exact words: every (stemmed, non-stopword) term of the query must occur, read
# as German or as English. Natural-language questions then rely on meaning, while
# names, numbers and specific terms get an extra boost when they match exactly.
SEARCH = f"""
WITH kwq AS (
    SELECT plainto_tsquery('german', %(text)s) || plainto_tsquery('english', %(text)s) AS query
), semantic AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> %(vector)s) AS rank
    FROM chunks
    WHERE %(vector)s::vector IS NOT NULL
    ORDER BY embedding <=> %(vector)s
    LIMIT {CANDIDATES}
), keyword AS (
    SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, kwq.query) DESC) AS rank
    FROM chunks c, kwq
    WHERE numnode(kwq.query) > 0 AND c.tsv @@ kwq.query
    ORDER BY ts_rank_cd(c.tsv, kwq.query) DESC
    LIMIT {CANDIDATES}
), fused AS (
    SELECT id,
           sum(1.0 / ({RRF_K} + rank)) AS score,
           array_agg(signal ORDER BY signal) AS signals
    FROM (
        SELECT id, rank, 'meaning' AS signal FROM semantic
        UNION ALL
        SELECT id, rank, 'words' AS signal FROM keyword
    ) ranked
    GROUP BY id
)
SELECT c.document_id, d.title, c.page, c.text, f.score, f.signals, p.source
FROM fused f
JOIN chunks c ON c.id = f.id
JOIN indexed_documents d ON d.id = c.document_id
LEFT JOIN pages p ON p.document_id = c.document_id AND p.page = c.page
ORDER BY f.score DESC
LIMIT %(limit)s
"""


@dataclass
class SyncStats:
    indexed: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    unchanged: int = 0
    chunks: int = 0
    ocr_pages: dict[int, list[int]] = field(default_factory=dict)
    unreadable_pages: dict[int, list[int]] = field(default_factory=dict)


class PassageIndex:
    def __init__(self, database_url: str, embedder):
        self.database_url = database_url
        self.embedder = embedder

    @property
    def pipeline(self) -> str:
        return f"v{PIPELINE_VERSION}/{self.embedder.model}"

    def connect(self) -> psycopg.Connection:
        connection = psycopg.connect(self.database_url)
        connection.execute(SCHEMA)
        connection.commit()
        register_vector(connection)
        return connection

    def _index_document(
        self, db: psycopg.Connection, paperless: PaperlessClient, document: dict
    ) -> tuple[int, list[int], list[int]]:
        pages = paperless.pages(document)
        chunks = chunk_pages(pages)
        vectors = self.embedder.embed([f"{document['title']}\n\n{c.text}" for c in chunks]) if chunks else []
        with db.transaction():
            db.execute("DELETE FROM indexed_documents WHERE id = %s", (document["id"],))
            db.execute(
                "INSERT INTO indexed_documents (id, title, modified, pipeline) VALUES (%s, %s, %s, %s)",
                (document["id"], document["title"], document["modified"], self.pipeline),
            )
            with db.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO pages (document_id, page, text, source) VALUES (%s, %s, %s, %s)",
                    [(document["id"], p.number, p.text, p.source) for p in pages],
                )
                cursor.executemany(
                    "INSERT INTO chunks (document_id, page, ordinal, text, embedding) VALUES (%s, %s, %s, %s, %s)",
                    [(document["id"], c.page, c.ordinal, c.text, Vector(v)) for c, v in zip(chunks, vectors)],
                )
        ocr = [p.number for p in pages if p.source == "ocr"]
        # Even after OCR: blank pages, signatures only, or illegible scans.
        empty = [p.number for p in pages if unreadable(p.text)]
        log.info("Indexed document %s (%s passages%s)", document["id"], len(chunks),
                 f", OCR on pages {ocr}" if ocr else "")
        if empty:
            log.warning("Document %s: little or no readable text on pages %s", document["id"], empty)
        return len(chunks), ocr, empty

    def sync(self, paperless: PaperlessClient, *, rebuild: bool = False) -> SyncStats:
        """Bring the index in line with what otto-agent can see in Paperless."""
        stats = SyncStats()
        visible = {doc["id"]: doc for doc in paperless.list_documents()}
        with self.connect() as db:
            if rebuild:
                db.execute("DELETE FROM indexed_documents")
            known = {row[0]: (row[1], row[2]) for row in db.execute("SELECT id, modified, pipeline FROM indexed_documents")}
            stats.removed = sorted(set(known) - set(visible))
            if stats.removed:
                db.execute("DELETE FROM indexed_documents WHERE id = ANY(%s)", (stats.removed,))
            db.commit()
            for doc_id, summary in sorted(visible.items()):
                if known.get(doc_id) == (summary["modified"], self.pipeline):
                    stats.unchanged += 1
                    continue
                chunks, ocr, empty = self._index_document(db, paperless, paperless.document(doc_id))
                stats.indexed.append(doc_id)
                stats.chunks += chunks
                if ocr:
                    stats.ocr_pages[doc_id] = ocr
                if empty:
                    stats.unreadable_pages[doc_id] = empty
        return stats

    def document_pages(self, paperless: PaperlessClient, document: dict) -> list[Page]:
        """Stored page texts, indexing the document first if missing or outdated."""
        with self.connect() as db:
            row = db.execute("SELECT modified, pipeline FROM indexed_documents WHERE id = %s", (document["id"],)).fetchone()
            if row != (document["modified"], self.pipeline):
                self._index_document(db, paperless, document)
            rows = db.execute(
                "SELECT page, text, source FROM pages WHERE document_id = %s ORDER BY page", (document["id"],)
            ).fetchall()
        return [Page(*r) for r in rows]

    def search(self, query: str, vector: list[float] | None, limit: int) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                SEARCH,
                {"text": query, "vector": Vector(vector) if vector is not None else None, "limit": limit},
            ).fetchall()
        return [
            {"document_id": r[0], "title": r[1], "page": r[2], "text": r[3], "score": round(float(r[4]), 4),
             "matched_by": list(r[5]), **({"page_text_from": "OCR (may contain recognition errors)"} if r[6] == "ocr" else {})}
            for r in rows
        ]
