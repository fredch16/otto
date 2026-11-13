"""Fred's Pending list against a throwaway database (needs OTTO_DATABASE_URL)."""
import pytest
from psycopg.types.json import Jsonb
from starlette.testclient import TestClient

from otto_paperless_mcp import web
from otto_paperless_mcp.pending import Pending

DETAIL = {"to": ["help@example.com"], "cc": [], "subject": "Re: Support <Request>", "body": "Hi,\n<b>details</b>",
          "folder": "Drafts", "message_id": "<123@icloud.com>", "in_reply_to": "",
          "attachments": [{"document_id": 14, "filename": "CampusCard.jpg"}]}


@pytest.fixture
def http(database_url):
    store = Pending(database_url)
    with store.connect() as db:  # as the mail server adds it
        db.execute("INSERT INTO pending (kind, title, purpose, detail, created_by) VALUES (%s, %s, %s, %s, %s)",
                   ("draft", "Review and send: Re: Support", "Answers the support email.", Jsonb(DETAIL), "otto-mail"))
        db.commit()
    web.use_pending(store)
    with TestClient(web.same_origin_only(web.app), base_url=f"http://127.0.0.1:{web.PORT}") as client:
        yield client
    web.use_pending(None)


def test_pending_drafts_are_shown_and_closed_by_fred(http):
    origin = {"Origin": f"http://127.0.0.1:{web.PORT}"}
    page = http.get("/pending").text
    assert 'Pending <span class="count">1</span>' in page and "Waiting for you (1)" in page
    assert "Answers the support email." in page and "&lt;b&gt;details&lt;/b&gt;" in page
    assert 'href="message://%3C123%40icloud.com%3E"' in page and '<a href="/documents/14">CampusCard.jpg</a>' in page
    assert http.post("/pending", data={"id": "1", "action": "done"}).status_code == 403  # no Origin
    page = http.post("/pending", data={"id": "1", "action": "done"}, headers=origin).text
    assert "Marked as done." in page and "Waiting for you (0)" in page and "Recently closed" in page
    assert 'class="count"' not in page
    page = http.post("/pending", data={"id": "1", "action": "reopen"}, headers=origin).text
    assert "Back on the list." in page and "Waiting for you (1)" in page
    assert "No pending item #9" in http.post("/pending", data={"id": "9", "action": "done"}, headers=origin).text
