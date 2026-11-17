"""The web UI against fake Paperless, index and change log (no database needed)."""
from datetime import datetime, timedelta, timezone

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

    def import_tasks(self, limit=200):
        return self.tasks


def task(name, status, result=None, minutes_ago=1):
    at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    done = (at + timedelta(seconds=8)).isoformat() if status in ("success", "failure") else None
    return {"status": status, "input_data": {"filename": name}, "result_data": result,
            "date_created": at.isoformat(), "date_done": done}


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
    FakePaperless.tasks = []
    monkeypatch.setattr(server, "client", lambda: FakePaperless())
    monkeypatch.setattr(server, "index", lambda: FakeIndex())
    monkeypatch.setenv("PAPERLESS_URL", "http://localhost:8000")
    monkeypatch.delenv("OTTO_DATABASE_URL", raising=False)  # no Pending list
    web.use_pending(None)
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


def test_upload_drops_files_into_the_consume_folder(client, monkeypatch, tmp_path):
    http, _ = client
    consume = tmp_path / "consume"
    consume.mkdir()
    monkeypatch.setenv("OTTO_CONSUME_DIR", str(consume))
    (consume / "Vertrag.pdf").write_bytes(b"%PDF-1.7 older")
    origin = {"Origin": f"http://127.0.0.1:{web.PORT}"}
    files = [("files", ("../../Vertrag.PDF", b"%PDF-1.7 new", "application/pdf")),
             ("files", ("Scan 1.jpg", b"\xff\xd8\xff\xe0 jpeg", "image/jpeg")),
             ("files", ("evil.pdf", b"<html>", "application/pdf")),
             ("files", ("script.sh", b"%PDF but not", "text/plain"))]
    response = http.post("/upload", files=files, headers=origin, follow_redirects=False)
    assert response.status_code == 303
    page = http.get(response.headers["location"]).text
    assert "Uploaded 2 files" in page and "evil.pdf (not a PDF or image)" in page and "script.sh" in page
    assert sorted(f.name for f in consume.iterdir()) == ["Scan 1.jpg", "Vertrag (2).pdf", "Vertrag.pdf"]
    assert (consume / "Vertrag (2).pdf").read_bytes() == b"%PDF-1.7 new"
    assert not list(tmp_path.glob(".upload-*"))
    assert "Importing 3 files" in page and page.count("Waiting for Paperless to pick it up") == 3
    assert http.post("/upload", files=files[:1]).status_code == 403  # no Origin
    # the page's own upload gets JSON back, to show progress without leaving the page
    result = http.post("/upload", files=files[2:3], headers={**origin, "Accept": "application/json"}).json()
    assert result == {"uploaded": [], "refused": ["evil.pdf (not a PDF or image)"]}


def test_imports_show_each_file_until_paperless_has_dealt_with_it(client, monkeypatch, tmp_path):
    http, _ = client
    monkeypatch.setenv("OTTO_CONSUME_DIR", str(tmp_path))
    (tmp_path / "queued.pdf").write_bytes(b"%PDF")
    (tmp_path / "new.pdf").write_bytes(b"%PDF")
    FakePaperless.tasks = [
        task("queued.pdf", "pending", minutes_ago=-1),
        task("reading.pdf", "started"),
        task("Vertrag.pdf", "success", {"document_id": 12}),
        task("again.pdf", "failure", {"duplicate_of": 12, "duplicate_in_trash": False}),
        task("broken.pdf", "failure", {"error_type": "ParseError", "error_message": "PDF is damaged"}),
        task("old.pdf", "success", {"document_id": 7}, minutes_ago=60 * 25),  # finished long ago
        task("ancient.pdf", "success", {"document_id": 7}, minutes_ago=60 * 24 * 40),  # beyond the history
    ]
    panel = http.get("/imports").text
    assert "Importing 6 files" in panel and "3 of 6 finished" in panel and 'aria-valuenow="50"' in panel
    assert 'data-active="3"' in panel and "old.pdf" not in panel and 'href="/?history=1#imports"' in panel
    assert "new.pdf</span>" in panel and panel.count("Waiting for Paperless") == 1  # queued.pdf is in line
    assert "In line" in panel and "Being read" in panel
    assert 'Imported as <a href="/documents/12">Arbeitsvertrag &lt;script&gt;' in panel
    assert "Already in your archive: <a href=\"/documents/12\">" in panel and "Not imported: PDF is damaged" in panel

    FakePaperless.tasks = FakePaperless.tasks[2:5]
    (tmp_path / "queued.pdf").unlink()
    (tmp_path / "new.pdf").unlink()
    panel = http.get("/imports").text
    assert "1 imported, 1 already in your archive, 1 not imported" in panel and "just now" in panel

    # finished imports leave the view after a few minutes and stay in the history
    FakePaperless.tasks = [task("Vertrag.pdf", "success", {"document_id": 12}, minutes_ago=30),
                           task("old.pdf", "success", {"document_id": 7}, minutes_ago=60 * 25)]
    panel = http.get("/imports").text
    assert "<li" not in panel and ">Import history</a>" in panel
    history = http.get("/imports", params={"history": "1"}).text
    assert "last 30 days · 2 files" in history and "old.pdf" in history and "Hide history" in history
    assert "Import history" in http.get("/", params={"history": "1"}).text
