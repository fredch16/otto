"""Offline tests against a fake Paperless API (no real documents involved)."""
import json

import httpx
import pytest
from mcp import Client

from otto_paperless_mcp import server
from otto_paperless_mcp.paperless import PaperlessClient

from conftest import make_pdf


DOCS = {
    6: {"id": 6, "title": "Creator Agreement", "created_date": "2026-08-31", "modified": "m1",
        "correspondent": 1, "document_type": None, "tags": [10], "page_count": 2,
        "content": "Term. Termination after two videos.", "notes": [], "custom_fields": [],
        "mime_type": "application/pdf", "original_file_name": "agreement.pdf"},
    7: {"id": 7, "title": "Plain note", "created_date": "2026-01-01", "modified": "m1",
        "correspondent": None, "document_type": None, "tags": [], "page_count": None,
        "content": "x" * 50_000, "notes": [], "custom_fields": [], "mime_type": "text/plain"},
}
TAXONOMY = {
    "correspondents": [{"id": 1, "name": "siliXon", "document_count": 1},
                       {"id": 2, "name": "Silicon Valley Bank", "document_count": 0}],
    "tags": [{"id": 10, "name": "contract", "document_count": 1}],
    "document_types": [], "storage_paths": [], "custom_fields": [],
}


@pytest.fixture
def requests(monkeypatch):
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path.strip("/").split("/")
        if path[1] in TAXONOMY:
            return httpx.Response(200, json={"count": len(TAXONOMY[path[1]]), "next": None, "results": TAXONOMY[path[1]]})
        if path == ["api", "documents"]:
            hit = {"score": 1.5, "highlights": "<span class=\"match\">Termination</span> after two&#x27;s"}
            return httpx.Response(200, json={"count": 1, "results": [{**DOCS[6], "__search_hit__": hit}]})
        doc = DOCS.get(int(path[2]))
        if doc is None:
            return httpx.Response(404)
        if path[3:] == ["download"]:
            if doc["mime_type"] == "application/pdf":
                return httpx.Response(200, content=make_pdf(["Page one text", "Termination clause"]),
                                      headers={"content-type": "application/pdf"})
            return httpx.Response(200, content=b"plain", headers={"content-type": "text/plain"})
        return httpx.Response(200, json=doc)

    monkeypatch.setenv("PAPERLESS_URL", "http://paperless.test")
    monkeypatch.delenv("OTTO_DATABASE_URL", raising=False)  # read pages live, no index
    server.use(PaperlessClient("http://paperless.test", "token", transport=httpx.MockTransport(handle)))
    yield seen
    server.use(None)


async def call(name: str, args: dict):
    async with Client(server.mcp) as client:
        result = await client.call_tool(name, args)
    text = result.content[0].text
    try:
        return result.is_error, json.loads(text)
    except ValueError:
        return result.is_error, text


async def test_search_resolves_names_and_cleans_excerpts(requests):
    error, data = await call("search_documents", {
        "query": "termination", "correspondent": "silix", "tags": ["Contract"],
        "created_from": "2026-01-01", "created_to": "2026-12-31", "limit": 5,
    })
    assert not error
    search = [r for r in requests if r.url.path == "/api/documents/"][0].url.params
    assert search["query"] == "termination"
    assert search["correspondent__id"] == "1"
    assert search["tags__id__all"] == "10"
    assert search["created__date__gte"] == "2026-01-01"
    assert search["created__date__lte"] == "2026-12-31"
    assert search["page_size"] == "5"
    result = data["results"][0]
    assert result["excerpt"] == "Termination after two's"
    assert result["correspondent"] == "siliXon"
    assert result["tags"] == ["contract"]
    assert result["link"] == "http://paperless.test/documents/6/details"


async def test_ambiguous_name_is_reported(requests):
    error, text = await call("search_documents", {"correspondent": "sil"})
    assert error and "ambiguous" in text and "siliXon" in text


async def test_document_text_has_page_markers(requests):
    error, text = await call("get_document_text", {"document_id": 6})
    assert not error
    assert "--- Page 1 of 2 ---\nPage one text" in text
    assert "--- Page 2 of 2 ---\nTermination clause" in text

    error, text = await call("get_document_text", {"document_id": 6, "first_page": 2})
    assert "Page one text" not in text and "Termination clause" in text

    error, text = await call("get_document_text", {"document_id": 6, "first_page": 3})
    assert error and "2 page(s)" in text


async def test_non_pdf_falls_back_to_truncated_content(requests):
    error, text = await call("get_document_text", {"document_id": 7})
    assert not error
    assert "No page boundaries" in text and "[Truncated" in text
    assert len(text) < server.MAX_TEXT_CHARS + 500


async def test_missing_document_is_a_readable_error(requests):
    error, text = await call("get_document", {"document_id": 99})
    assert error and "not visible to the OTTO agent" in text


async def test_only_get_requests_are_sent(requests):
    await call("search_documents", {"query": "x", "correspondent": "siliXon"})
    await call("get_document", {"document_id": 6})
    await call("get_document_text", {"document_id": 6})
    await call("list_metadata", {"kind": "tags"})
    assert requests and {r.method for r in requests} == {"GET"}


async def test_all_tools_are_marked_read_only():
    async with Client(server.mcp) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == {
        "search_passages", "search_documents", "get_document", "get_document_text", "list_metadata",
    }
    assert all(t.annotations.read_only_hint for t in tools)
