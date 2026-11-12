"""otto-tidy against a fake Paperless and a throwaway database (needs OTTO_DATABASE_URL)."""
import json

import httpx
import pytest
from psycopg.types.json import Jsonb

from otto_paperless_mcp.cards import CardResult
from otto_paperless_mcp.index import PassageIndex
from otto_paperless_mcp.tidy import PaperlessEditor, Tidy, has_filename_title, same_name, valid_date

CARD = {"document_type": "employment contract", "language": "German", "date": "2026-10-27",
        "parties": [{"name": "Neckarwerk GmbH", "role": "employer"}], "summary": "Arbeitsvertrag.",
        "key_facts": [], "deadlines": []}


class FakePaperless:
    def __init__(self):
        self.documents = {
            # untouched upload: title is the filename, date misread, nothing filed
            7: {"id": 7, "title": "440BC65B-8922_1_201_a", "original_file_name": "440BC65B-8922_1_201_a.heic",
                "correspondent": None, "document_type": None, "created": "2005-04-16"},
            # titled and filed by Fred, only the type is missing
            8: {"id": 8, "title": "Mein Mietvertrag", "original_file_name": "scan0001.pdf",
                "correspondent": 1, "document_type": None, "created": "2024-01-01"},
            # filed completely
            9: {"id": 9, "title": "Steuerbescheid 2025", "original_file_name": "x.pdf",
                "correspondent": 1, "document_type": 1, "created": "2026-05-01"},
        }
        self.taxonomies = {"correspondents": {1: "Neckarwerk Energie"}, "document_types": {1: "Tax notice"}}
        self.requests = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.method != "DELETE"
        self.requests.append((request.method, request.url.path))
        parts = request.url.path.strip("/").split("/")
        if parts[1] in self.taxonomies:
            items = self.taxonomies[parts[1]]
            if request.method == "POST":
                body = json.loads(request.content)
                assert body["owner"] is None
                items[new_id := max(items, default=0) + 1] = body["name"]
                return httpx.Response(201, json={"id": new_id, "name": body["name"]})
            return httpx.Response(200, json={"next": None, "results": [{"id": i, "name": n} for i, n in items.items()]})
        if len(parts) == 2:
            return httpx.Response(200, json={"next": None, "results": list(self.documents.values())})
        doc = self.documents[int(parts[2])]
        if request.method == "PATCH":
            doc.update(json.loads(request.content))
        return httpx.Response(200, json=doc)


class FakeTidier:
    model = "fake:tidy"

    def __init__(self):
        self.prompts = []
        self.answer = {"title": "Arbeitsvertrag Projektmanager", "correspondent": "Neckarwerk GmbH",
                       "document_type": "Contract", "date": "2026-10-27"}

    def propose(self, prompt):
        self.prompts.append(prompt)
        return CardResult(dict(self.answer), 300, 40)


@pytest.fixture
def setup(database_url):
    with PassageIndex(database_url, None).connect() as db:  # creates the cards table
        for doc_id in (7, 8, 9):
            db.execute("INSERT INTO cards (document_id, modified, model, card, input_tokens, output_tokens) "
                       "VALUES (%s, 'm1', 'fake:cards', %s, 0, 0)", (doc_id, Jsonb(CARD)))
        db.commit()
    fake, tidier = FakePaperless(), FakeTidier()
    editor = PaperlessEditor("http://paperless.test", "token", transport=httpx.MockTransport(fake.handle))
    return fake, tidier, Tidy(database_url, editor, tidier)


def test_untitled_documents_are_renamed_and_filed(setup):
    fake, tidier, tidy = setup
    tidier.answer["correspondent"] = "Neckarwerk Energie GmbH"  # an existing correspondent, with legal form
    result = tidy.run()
    assert fake.documents[7] == {**fake.documents[7], "title": "Arbeitsvertrag Projektmanager",
                                      "correspondent": 1, "document_type": 2, "created": "2026-10-27"}
    assert fake.taxonomies["document_types"][2] == "Contract"  # created once, reused for #8
    assert fake.taxonomies["correspondents"] == {1: "Neckarwerk Energie"}  # no duplicate
    # Fred's own title and date stay; only the empty type is filled
    assert fake.documents[8] == {**fake.documents[8], "title": "Mein Mietvertrag", "created": "2024-01-01",
                                 "document_type": 2}
    assert len(tidier.prompts) == 2  # #9 is filed completely: no model call
    assert "Existing correspondents: Neckarwerk Energie" in tidier.prompts[0]
    assert {(c.document_id, c.field) for c in result.changes} == {
        (7, "title"), (7, "created"), (7, "correspondent"), (7, "document_type"), (8, "document_type")}
    assert len(tidy.history()) == 5 and result.tokens == (600, 80)

    assert tidy.run().changes == [] and len(tidier.prompts) == 2  # tidied once per card


def test_dry_run_changes_nothing(setup):
    fake, tidier, tidy = setup
    result = tidy.run(dry_run=True)
    assert len(result.changes) == 5 and tidy.history() == []
    assert all(method == "GET" for method, _ in fake.requests)
    assert len(tidy.run().changes) == 5  # proposals were not marked as done


def test_undo_restores_old_values_unless_changed_since(setup):
    fake, _, tidy = setup
    tidy.run()
    log = {(c["document_id"], c["field"]): c["id"] for c in tidy.history()}
    fake.documents[7]["title"] = "Fred's own title"  # Fred edits after OTTO
    messages = tidy.undo(document_id=7)
    assert fake.documents[7]["title"] == "Fred's own title"
    assert fake.documents[7]["created"] == "2005-04-16" and fake.documents[7]["correspondent"] is None
    assert any("title: changed since" in m for m in messages)
    assert tidy.undo([log[(7, "created")]]) == [f"#{log[(7, 'created')]}: no such change, or already undone"]
    assert tidy.undo([log[(8, "document_type")]]) and fake.documents[8]["document_type"] is None
    assert tidy.run().changes == []  # undone changes are not made again


def test_documents_without_cards_wait(setup, database_url):
    fake, _, tidy = setup
    with tidy.connect() as db:
        db.execute("DELETE FROM cards WHERE document_id = 7")
        db.commit()
    result = tidy.run()
    assert result.skipped_no_card == [7] and fake.documents[7]["title"] == "440BC65B-8922_1_201_a"


def test_rules():
    assert has_filename_title({"title": "IMG_0001", "original_file_name": "IMG_0001.heic"})
    assert not has_filename_title({"title": "Arbeitsvertrag", "original_file_name": "IMG_0001.heic"})
    assert not has_filename_title({"title": "IMG_0001", "original_file_name": None})
    assert same_name("Deutsche Kreditbank AG", "deutsche kreditbank") and same_name("Verein e.V.", "Verein")
    assert not same_name("DKB", "Deutsche Kreditbank AG") and not same_name("GmbH", "AG")
    assert valid_date("2026-10-27") == "2026-10-27"
    assert valid_date("") is None and valid_date("2999-01-01") is None and valid_date("27.10.2026") is None
