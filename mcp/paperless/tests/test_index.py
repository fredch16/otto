"""Chunking tests, and index tests against a throwaway PostgreSQL database.

The index tests need OTTO_DATABASE_URL (run with --env-file ../../.env.agent);
they create and drop their own database and use a word-hashing stand-in for
the embedding model, so Ollama is not required.
"""
import hashlib
import math
import os
import re
import uuid

import httpx
import psycopg
import pytest

from otto_paperless_mcp import server
from otto_paperless_mcp.cards import CardError, CardResult
from otto_paperless_mcp.chunking import MAX_CHARS, chunk_pages
from otto_paperless_mcp.embedding import DIMENSIONS, EmbeddingError
from otto_paperless_mcp.index import PassageIndex
from otto_paperless_mcp.paperless import Page, PaperlessClient

from conftest import make_pdf


def test_chunks_stay_small_and_keep_page_numbers():
    long_paragraph = " ".join(f"Satz {i} mit etwas Inhalt." for i in range(200))
    chunks = chunk_pages([Page(1, "Kurz.\n\nNoch kurz."), Page(2, long_paragraph)])
    assert chunks[0].page == 1 and chunks[0].text == "Kurz.\n\nNoch kurz."
    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    assert {c.page for c in chunks[1:]} == {2} and len(chunks) > 3
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert "".join(c.text.replace(" ", "") for c in chunks[1:]) == long_paragraph.replace(" ", "")


class WordEmbedder:
    """Bag-of-words vectors: similar wording gives similar vectors."""

    model = "test-words"

    def __init__(self):
        self.fail = False

    def embed(self, texts):
        if self.fail:
            raise EmbeddingError("embedding offline")
        vectors = []
        for text in texts:
            vector = [0.0] * DIMENSIONS
            for word in re.findall(r"\w+", text.lower()):
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIMENSIONS] += 1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append([v / norm for v in vector])
        return vectors


PAGES = {
    20: ["Arbeitsvertrag zwischen der Firma und dem Mitarbeiter. Die Kuendigungsfrist betraegt drei Monate zum Monatsende.",
         "Der Urlaub betraegt dreissig Arbeitstage im Kalenderjahr.",
         "Unterschrift"],
    21: ["Versicherungsschein Nummer KV-554433 fuer die private Haftpflichtversicherung."],
}


class FakePaperless:
    def __init__(self):
        self.visible = {20: "m1", 21: "m1"}
        self.pages = {doc_id: list(pages) for doc_id, pages in PAGES.items()}

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.strip("/").split("/")
        if path == ["api", "documents"]:
            results = [{"id": i, "title": f"Doc {i}", "modified": m} for i, m in self.visible.items()]
            return httpx.Response(200, json={"count": len(results), "next": None, "results": results})
        doc_id = int(path[2])
        if doc_id not in self.visible:
            return httpx.Response(404)
        if path[3:] == ["download"]:
            return httpx.Response(200, content=make_pdf(self.pages[doc_id]), headers={"content-type": "application/pdf"})
        return httpx.Response(200, json={"id": doc_id, "title": f"Doc {doc_id}", "modified": self.visible[doc_id],
                                         "content": " ".join(self.pages[doc_id])})


@pytest.fixture
def database_url():
    base = os.environ.get("OTTO_DATABASE_URL")
    if not base:
        pytest.skip("OTTO_DATABASE_URL not set")
    name = f"otto_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE {name}")
    yield base.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE {name} WITH (FORCE)")


@pytest.fixture
def setup(database_url):
    fake = FakePaperless()
    paperless = PaperlessClient("http://paperless.test", "token", transport=httpx.MockTransport(fake.handle))
    return fake, paperless, PassageIndex(database_url, WordEmbedder())


def ids(results):
    return [(r["document_id"], r["page"]) for r in results]


def test_sync_indexes_updates_and_removes(setup):
    fake, paperless, index = setup
    stats = index.sync(paperless)
    assert stats.indexed == [20, 21] and stats.unchanged == 0
    # every test page is short (OCR is stubbed out), so all are reported
    assert stats.unreadable_pages == {20: [1, 2, 3], 21: [1]}

    assert index.sync(paperless).unchanged == 2  # nothing changed

    fake.visible[20] = "m2"  # edited in Paperless
    assert index.sync(paperless).indexed == [20]

    del fake.visible[21]  # deleted or no longer shared with otto-agent
    stats = index.sync(paperless)
    assert stats.removed == [21]
    query = "KV-554433"
    assert 21 not in {r["document_id"] for r in index.search(query, index.embedder.embed([query])[0], 5)}


def test_search_finds_pages_by_meaning_and_exact_words(setup):
    _, paperless, index = setup
    index.sync(paperless)

    query = "Urlaub Arbeitstage"
    results = index.search(query, index.embedder.embed([query])[0], 3)
    assert ids(results)[0] == (20, 2)
    assert set(results[0]["matched_by"]) == {"meaning", "words"}

    query = "KV-554433"
    assert ids(index.search(query, index.embedder.embed([query])[0], 3))[0] == (21, 1)
    # without embeddings, exact words still work
    assert ids(index.search(query, None, 3))[0] == (21, 1)


def test_document_pages_are_stored_and_indexed_on_demand(setup):
    _, paperless, index = setup
    document = paperless.document(21)
    pages = index.document_pages(paperless, document)  # not yet indexed: indexes it
    assert [(p.number, p.source) for p in pages] == [(1, "pdf")]
    assert "KV-554433" in pages[0].text


def test_search_tool_falls_back_to_exact_words(setup, monkeypatch):
    _, paperless, index = setup
    index.sync(paperless)
    monkeypatch.setenv("PAPERLESS_URL", "http://paperless.test")
    server.use(paperless)
    server.use_index(index)
    try:
        index.embedder.fail = True
        result = server.search_passages("KV-554433")
        assert ids(result["results"])[0] == (21, 1)
        assert any("exact words only" in note for note in result["notes"])
    finally:
        server.use(None)
        server.use_index(None)


class FakeCardWriter:
    model = "fake:cards"

    def __init__(self):
        self.calls, self.fail = [], False

    def write(self, title, pages):
        if self.fail:
            raise CardError("model unavailable")
        self.calls.append(title)
        card = {"document_type": "employment contract", "language": "German", "date": "2026-10-27",
                "parties": [{"name": "Neckarwerk GmbH", "role": "employer"}],
                "summary": f"{title}: Arbeitsvertrag mit {len(pages)} Seiten.",
                "key_facts": [{"fact": "Kuendigungsfrist drei Monate", "page": 1}], "deadlines": []}
        return CardResult(card, input_tokens=100, output_tokens=20)


def test_cards_are_written_once_indexed_and_kept_across_rebuilds(setup):
    fake, paperless, index = setup
    index.card_writer = writer = FakeCardWriter()
    stats = index.sync(paperless)
    assert stats.cards == [20, 21] and stats.card_tokens == (200, 40)

    query = "Neckarwerk employer"
    top = index.search(query, index.embedder.embed([query])[0], 3)[0]
    assert top["page"] is None and "Neckarwerk GmbH (employer)" in top["text"] and "passage_from" in top
    assert index.card(20)["written_by"] == "fake:cards"

    assert index.sync(paperless).cards == []  # nothing changed: no new model calls
    assert index.sync(paperless, rebuild=True).cards == []  # re-indexing keeps cards...
    assert any(r["page"] is None for r in index.search(query, None, 5))  # ...and their passages

    fake.visible[20] = "m2"  # metadata edited in Paperless (e.g. a new title): card kept
    assert index.sync(paperless).cards == []
    assert any(r["document_id"] == 20 and r["page"] is None for r in index.search(query, None, 5))
    fake.pages[20][1] += " Neu: Homeoffice an zwei Tagen."  # text changed: card rewritten
    fake.visible[20] = "m3"
    assert index.sync(paperless).cards == [20]
    writer.model = "fake:other"  # another model chosen: existing cards are kept...
    assert index.sync(paperless).cards == []
    assert index.sync(paperless, rewrite_cards=[21]).cards == [21]  # ...until rewritten on request
    assert index.card(21)["written_by"] == "fake:other" and index.card(20)["written_by"] == "fake:cards"
    assert index.sync(paperless, rewrite_cards="all").cards == [20, 21]
    assert len(writer.calls) == 6


def test_card_failures_do_not_stop_indexing(setup):
    fake, paperless, index = setup
    index.card_writer = writer = FakeCardWriter()
    writer.fail = True
    stats = index.sync(paperless)
    assert stats.indexed == [20, 21] and stats.cards == [] and set(stats.card_errors) == {20, 21}
    assert index.card(20) is None
    writer.fail = False
    assert index.sync(paperless).cards == [20, 21]  # retried on the next sync


def test_search_returns_one_passage_per_card(setup):
    _, paperless, index = setup

    class LongCards(FakeCardWriter):
        def write(self, title, pages):
            result = super().write(title, pages)
            result.card["key_facts"] = [{"fact": f"Neckarwerk Regel {i} " + "Inhalt " * 20, "page": 1} for i in range(30)]
            return result

    index.card_writer = LongCards()
    index.sync(paperless)
    query = "Neckarwerk Regel"
    results = index.search(query, index.embedder.embed([query])[0], 5)
    cards = [r["document_id"] for r in results if r["page"] is None]
    assert len(cards) == len(set(cards)) == 2
