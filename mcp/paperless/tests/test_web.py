"""The web UI against fake Paperless, index and change log (no database needed)."""
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

from otto_paperless_mcp import server, web
from otto_paperless_mcp.paperless import Page

CARD = {"document_type": "employment contract", "language": "German", "date": "2026-10-27",
        "parties": [{"name": "Neckarwerk GmbH", "role": "employer"}], "summary": "Arbeitsvertrag <b>Manager</b>.",
        "key_facts": [{"fact": "Kündigungsfrist drei Monate", "page": 2}], "deadlines": []}


class FakePaperless:
    docs = {12: {"id": 12, "title": "Arbeitsvertrag <script>alert(1)</script>", "created": "2026-10-27",
                 "correspondent": 1, "document_type": None, "page_count": 2, "original_file_name": "scan.pdf"},
            7: {"id": 7, "title": "IMG_0001", "created": "2023-06-01", "correspondent": None,
                "document_type": None, "page_count": 1}}

    def list_documents(self, fields="id,title,modified"):
        return list(self.docs.values())

    def document(self, document_id):
        return self.docs[document_id]

    def name(self, kind, item_id):
        return None if item_id is None else "Neckarwerk"

    def file(self, document_id, kind):
        return b"RIFFwebp", "image/webp"


class FakeEmbedder:
    def embed(self, texts):
        return [[0.0]]


class FakeIndex:
    embedder = FakeEmbedder()

    def cards(self):
        return {12: CARD}

    def card(self, document_id):
        return {"written_by": "fake:cards", **CARD} if document_id == 12 else None

    def stored_pages(self, document_id):
        return [Page(1, "Zwischen"), Page(2, "Die Kündigungsfrist beträgt drei Monate.", "ocr")]

    def search(self, query, vector, limit):
        return [{"document_id": 12, "page": 2, "text": "Die Kündigungsfrist beträgt drei Monate."},
                {"document_id": 12, "page": None, "text": "Document card ..."}]


class FakeTidy:
    def __init__(self):
        self.undone = []
        self.changes = [{"id": 5, "at": datetime(2026, 11, 12, 18, 53, tzinfo=timezone.utc), "document_id": 12,
                         "field": "correspondent", "old": None, "new": {"id": 1, "name": "Neckarwerk"},
                         "reason": "was empty; new; fake:tidy", "undone_at": None}]

    def history(self, limit=50, document_id=None):
        return [c for c in self.changes if document_id in (None, c["document_id"])]

    def undo(self, change_ids=(), document_id=None):
        self.undone.append((list(change_ids), document_id))
        return [f"#{i} restored" for i in change_ids] or [f"document {document_id} restored"]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "client", lambda: FakePaperless())
    monkeypatch.setattr(server, "index", lambda: FakeIndex())
    monkeypatch.setenv("PAPERLESS_URL", "http://localhost:8000")
    web.use_tidy(changelog := FakeTidy())
    with TestClient(web.same_origin_only(web.app), base_url=f"http://127.0.0.1:{web.PORT}") as http:
        yield http, changelog
    web.use_tidy(None)


def test_documents_list_and_search(client):
    http, _ = client
    page = http.get("/").text
    assert page.index("Arbeitsvertrag") < page.index("IMG_0001")  # newest first
    assert "no card yet" in page and "Neckarwerk" in page
    page = http.get("/", params={"q": "Kündigungsfrist"}).text
    assert "1 document for" in page and "<b>p. 2</b>" in page and "<b>Card</b>" in page


def test_document_page_escapes_untrusted_text(client):
    http, _ = client
    page = http.get("/documents/12").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert "<b>Manager</b>" not in page
    assert 'href="#p2">p. 2' in page and 'id="p2"' in page and "OCR by OTTO" in page
    assert "Changed by OTTO" in page and 'name="change" value="5"' in page


def test_undo_redirects_back_with_a_message(client):
    http, changelog = client
    origin = {"Origin": f"http://127.0.0.1:{web.PORT}"}
    response = http.post("/undo", data={"change": "5", "next": "/documents/12"}, headers=origin,
                         follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/documents/12?msg=")
    assert changelog.undone == [([5], None)]
    response = http.post("/undo", data={"document_id": "12", "next": "https://evil.example"}, headers=origin,
                         follow_redirects=False)
    assert response.headers["location"].startswith("/activity?") and changelog.undone[-1] == ([], 12)
    assert '<div class="flash">document 12 restored</div>' in http.get(response.headers["location"]).text


def test_requests_from_elsewhere_are_refused(client):
    http, changelog = client
    assert http.get("/", headers={"Host": "evil.example"}).status_code == 421
    assert http.post("/undo", data={"change": "5"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert http.post("/undo", data={"change": "5"}).status_code == 403  # no Origin at all
    assert changelog.undone == []


def test_activity_without_editor_token(client, monkeypatch):
    http, _ = client
    web.use_tidy(None)
    monkeypatch.delenv("PAPERLESS_EDITOR_TOKEN", raising=False)
    assert "needs the editor token" in http.get("/activity").text


def test_settings_toggle_is_saved_for_the_backup_script(client, monkeypatch, tmp_path):
    http, _ = client
    target = tmp_path / ".env.settings"
    monkeypatch.setenv("OTTO_SETTINGS", str(target))
    assert 'name="BACKUP_ICLOUD" value="on" >' in http.get("/settings").text  # off by default
    origin = {"Origin": f"http://127.0.0.1:{web.PORT}"}
    http.post("/settings", data={"BACKUP_ICLOUD": "on", "EVIL": "x\nrm -rf /"}, headers=origin)
    assert "BACKUP_ICLOUD=on" in target.read_text() and "EVIL" not in target.read_text()
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    assert "checked" in http.get("/settings").text
    http.post("/settings", data={}, headers=origin)  # unchecked boxes are not sent
    assert "BACKUP_ICLOUD=off" in target.read_text()
