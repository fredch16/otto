"""Read-only access to the Paperless-ngx REST API.

Only GET requests exist here by design. Paperless enforces the same boundary:
the `otto-agent` token has view-only permissions.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

import httpx

API_VERSION = 10
TAXONOMIES = {
    "correspondents": "correspondents",
    "document_types": "document_types",
    "tags": "tags",
    "storage_paths": "storage_paths",
    "custom_fields": "custom_fields",
}


class PaperlessError(Exception):
    """A failure the caller can explain to the model."""


@dataclass(frozen=True)
class Page:
    number: int  # 0 = no page structure available
    text: str
    source: str = "pdf"  # "pdf" (digital/Paperless text layer), "ocr" or "content"


def clean_highlight(value: str) -> str:
    """Turn a Paperless search highlight (HTML) into plain text."""
    text = re.sub(r"<[^>]+>", "", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


class PaperlessClient:
    def __init__(self, base_url: str, token: str, *, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Token {token}",
                "Accept": f"application/json; version={API_VERSION}",
            },
            timeout=60,
            transport=transport,
        )
        self._names: dict[str, dict[int, str]] = {}
        self._pages: dict[tuple[int, str], list[Page]] = {}

    def _get(self, path: str, **params) -> httpx.Response:
        params = {k: v for k, v in params.items() if v is not None}
        try:
            response = self._http.get(path, params=params)
        except httpx.HTTPError as error:
            raise PaperlessError(f"Paperless is unreachable: {error}") from error
        if response.status_code == 404:
            raise PaperlessError("Not found, or not visible to the OTTO agent.")
        if response.status_code in (401, 403):
            raise PaperlessError("Paperless rejected the OTTO agent token.")
        response.raise_for_status()
        return response

    def _json(self, path: str, **params):
        return self._get(path, **params).json()

    # Taxonomies (correspondents, tags, ...) -----------------------------

    def taxonomy(self, kind: str) -> list[dict]:
        if kind not in TAXONOMIES:
            raise PaperlessError(f"Unknown kind {kind!r}; use one of {', '.join(TAXONOMIES)}.")
        items, page = [], 1
        while True:
            data = self._json(f"/api/{TAXONOMIES[kind]}/", page=page, page_size=100)
            items.extend(data["results"])
            if not data.get("next"):
                break
            page += 1
        self._names[kind] = {item["id"]: item["name"] for item in items}
        return items

    def name(self, kind: str, item_id: int | None) -> str | None:
        if item_id is None:
            return None
        if item_id not in self._names.get(kind, {}):
            self.taxonomy(kind)
        return self._names[kind].get(item_id, f"#{item_id}")

    def resolve(self, kind: str, name: str) -> int:
        """Find a taxonomy id by case-insensitive name (exact, then partial)."""
        items = self.taxonomy(kind)
        wanted = name.casefold().strip()
        exact = [i for i in items if i["name"].casefold() == wanted]
        partial = [i for i in items if wanted in i["name"].casefold()]
        matches = exact or partial
        if len(matches) == 1:
            return matches[0]["id"]
        known = ", ".join(sorted(i["name"] for i in (matches or items))[:30]) or "none"
        problem = "is ambiguous" if matches else "matches nothing"
        raise PaperlessError(f"{kind[:-1].replace('_', ' ')} {name!r} {problem}. Known: {known}.")

    # Documents ------------------------------------------------------------

    def search(self, params: dict) -> dict:
        return self._json("/api/documents/", truncate_content="true", **params)

    def list_documents(self, fields: str = "id,title,modified") -> list[dict]:
        """These fields (default: id, title, modification time) of every document visible to the agent."""
        documents, page = [], 1
        while True:
            data = self._json("/api/documents/", fields=fields, page=page, page_size=100)
            documents.extend(data["results"])
            if not data.get("next"):
                return documents
            page += 1

    def document(self, document_id: int) -> dict:
        return self._json(f"/api/documents/{document_id}/")

    def file(self, document_id: int, kind: str) -> tuple[bytes, str]:
        """The document's "thumb" (image) or "preview" (archived PDF), with its content type."""
        if kind not in ("thumb", "preview"):
            raise PaperlessError(f"Unknown file kind {kind!r}.")
        response = self._get(f"/api/documents/{document_id}/{kind}/")
        return response.content, response.headers.get("content-type", "application/octet-stream")

    def notes(self, document_id: int) -> list[dict]:
        return self._json(f"/api/documents/{document_id}/notes/")

    def pages(self, document: dict) -> list[Page]:
        """Per-page text of the archived (OCR'd) PDF, or the original PDF.

        Unreadable pages are OCRed (see extraction.py). Falls back to Paperless'
        stored content as a single unnumbered page when there is no PDF.
        """
        key = (document["id"], document["modified"])
        if key not in self._pages:
            pages: list[Page] = []
            response = self._get(f"/api/documents/{document['id']}/download/")
            if response.headers.get("content-type", "").startswith("application/pdf"):
                from .extraction import extract_pdf_pages

                pages = extract_pdf_pages(response.content)
            if not any(page.text for page in pages):
                pages = [Page(0, document.get("content") or "", "content")]
            self._pages = {key: pages}  # keep only the latest document cached
        return self._pages[key]
