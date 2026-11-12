"""Knowledge base against a throwaway database (needs OTTO_DATABASE_URL)."""
import json
from datetime import date

import pytest
from mcp import Client
from psycopg.types.json import Jsonb
from starlette.testclient import TestClient

from otto_paperless_mcp import server, web
from otto_paperless_mcp.cards import CardResult
from otto_paperless_mcp.index import PassageIndex
from otto_paperless_mcp.knowledge import FactError, Knowledge

CARD = {"document_type": "employment contract", "language": "German", "date": "2026-10-27",
        "parties": [{"name": "Fred Example", "role": "employee"}], "summary": "Arbeitsvertrag.",
        "key_facts": [{"fact": "Wohnhaft Hauptstr. 1, 74072 Heilbronn", "page": 1}], "deadlines": []}


@pytest.fixture
def knowledge(database_url):
    with PassageIndex(database_url, None).connect() as db:  # creates the cards table
        db.execute("INSERT INTO cards (document_id, modified, model, card, input_tokens, output_tokens) "
                   "VALUES (12, 'm1', 'fake:cards', %s, 0, 0)", (Jsonb(CARD),))
        db.commit()
    return Knowledge(database_url)


def propose(knowledge, value, kind="address", valid_from=None, label="Home address"):
    return knowledge.propose(kind, label, value, source="test", proposed_by="test", valid_from=valid_from)


def test_proposals_count_only_once_confirmed(knowledge):
    fact = propose(knowledge, "Hauptstr. 1, 74072 Heilbronn")
    assert knowledge.facts(status="confirmed") == []
    assert propose(knowledge, "hauptstr 1 74072 heilbronn") is None  # same value, other spelling
    knowledge.confirm(fact["id"], value="Hauptstraße 1, 74072 Heilbronn")  # corrected while confirming
    [confirmed] = knowledge.facts(status="confirmed")
    assert confirmed["value"] == "Hauptstraße 1, 74072 Heilbronn" and confirmed["valid_from"] == date.today()
    with pytest.raises(FactError):
        knowledge.confirm(fact["id"])

    rejected = propose(knowledge, "DE00 1234", kind="iban", label="Some account")
    knowledge.reject(rejected["id"])
    assert propose(knowledge, "DE001234", kind="iban") is None  # rejected facts are not proposed again


def test_new_address_supersedes_and_older_one_becomes_history(knowledge):
    knowledge.confirm(propose(knowledge, "Hauptstr. 1, Heilbronn", valid_from=date(2026, 10, 1))["id"])
    knowledge.confirm(propose(knowledge, "Ringstr. 5, Stuttgart", valid_from=date(2027, 3, 1))["id"])
    knowledge.confirm(propose(knowledge, "Old Road 2, London", valid_from=date(2023, 1, 1))["id"])
    current = knowledge.facts(status="confirmed", current_only=True)
    assert [f["value"] for f in current] == ["Ringstr. 5, Stuttgart"]
    history = {f["value"]: (f["valid_from"], f["valid_until"]) for f in knowledge.facts(status="confirmed")
               if f["valid_until"]}
    assert history == {"Hauptstr. 1, Heilbronn": (date(2026, 10, 1), date(2027, 3, 1)),
                       "Old Road 2, London": (date(2023, 1, 1), date(2027, 3, 1))}

    # accounts are not single: a second one does not end the first
    knowledge.add("iban", "Salary account (DKB)", "DE11 0000")
    knowledge.add("iban", "Savings (ING)", "DE22 0000")
    assert len(knowledge.facts(kind="iban", current_only=True)) == 2


def test_correct_and_end_keep_history(knowledge):
    phone = knowledge.add("phone", "Mobile", "+49 151 000")
    corrected = knowledge.correct(phone["id"], "+49 151 111")
    assert knowledge.fact(phone["id"])["valid_until"] == date.today()
    knowledge.end(corrected["id"])
    assert knowledge.facts(kind="phone", current_only=True) == []
    assert len(knowledge.facts(kind="phone")) == 2
    with pytest.raises(FactError):
        knowledge.end(corrected["id"])


class FakeReader:
    model = "fake:facts"

    def __init__(self):
        self.prompts = []

    def read(self, prompt):
        self.prompts.append(prompt)
        return CardResult({"facts": [
            {"kind": "address", "label": "Home address", "value": "Hauptstr. 1, 74072 Heilbronn", "page": 1},
            {"kind": "employment", "label": "Employer", "value": "Neckarwerk GmbH", "page": 0}]}, 200, 30)


def test_cards_are_read_once_for_facts(knowledge):
    reader = FakeReader()
    result = knowledge.read_cards(reader, {12: "Arbeitsvertrag Manager", 13: "No card yet"})
    assert [f["label"] for f in result.proposed] == ["Home address", "Employer"]
    address = result.proposed[0]
    assert (address["document_id"], address["page"], address["valid_from"]) == (12, 1, date(2026, 10, 27))
    assert address["source"] == "Arbeitsvertrag Manager (#12), p. 1" and result.proposed[1]["page"] is None
    assert result.skipped_no_card == [13] and result.tokens == (200, 30)
    assert "Wohnhaft Hauptstr. 1" in reader.prompts[0]
    assert knowledge.read_cards(reader, {12: "Arbeitsvertrag Manager"}).proposed == [] and len(reader.prompts) == 1


async def test_chat_tools_read_confirmed_facts_and_only_propose(knowledge):
    knowledge.add("iban", "Salary account (DKB)", "DE11 0000", notes="rent")
    server.use_knowledge(knowledge)
    try:
        async with Client(server.mcp) as client:
            async def call(name, args):
                return json.loads((await client.call_tool(name, args)).content[0].text)

            result = await call("propose_fact", {"kind": "phone", "label": "Mobile", "value": "+49 151 000",
                                                 "source": "Fred said so"})
            assert result["status"] == "proposed"
            again = await call("propose_fact", {"kind": "phone", "label": "Mobile", "value": "+49151000",
                                                "source": "Fred"})
            assert again["status"].startswith("already known")
            facts = await call("get_facts", {})
    finally:
        server.use_knowledge(None)
    assert [f["value"] for f in facts["confirmed"]] == ["DE11 0000"]
    assert [f["value"] for f in facts["not_confirmed"]] == ["+49 151 000"] and "Fred's confirmation" in facts["note"]
    assert knowledge.facts(kind="phone")[0]["source"] == "chat: Fred said so"


def test_web_ui_confirms_corrects_and_adds(knowledge, monkeypatch):
    fact = propose(knowledge, "Hauptstr. 1, Heilbronn")
    monkeypatch.setattr(server, "knowledge", lambda: knowledge)
    origin = {"Origin": f"http://127.0.0.1:{web.PORT}"}
    with TestClient(web.same_origin_only(web.app), base_url=f"http://127.0.0.1:{web.PORT}") as http:
        assert "To confirm (1)" in http.get("/knowledge").text
        page = http.post("/knowledge", data={"action": "confirm", "id": fact["id"], "label": "Home",
                                             "value": "Hauptstraße 1, Heilbronn"}, headers=origin).text
        assert "Confirmed: Home." in page and "To confirm (0)" in page
        http.post("/knowledge", data={"action": "add", "kind": "tax_id", "label": "Steuer-ID",
                                      "value": "12 345 678 901", "valid_from": ""}, headers=origin)
        page = http.post("/knowledge", data={"action": "add", "kind": "nope", "label": "x", "value": "y"},
                         headers=origin).text
        assert "Unknown kind" in page
        assert http.post("/knowledge", data={"action": "reject", "id": fact["id"]}).status_code == 403  # no Origin
    assert {f["value"] for f in knowledge.facts(status="confirmed")} == {"Hauptstraße 1, Heilbronn", "12 345 678 901"}
