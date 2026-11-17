"""OTTO's page reader with fake models and a fake Apple Vision (no API calls)."""
import io

import pytest
from PIL import Image

from otto_paperless_mcp import extraction
from otto_paperless_mcp.cards import CardError
from otto_paperless_mcp.extraction import extract_image, extract_pdf_pages
from otto_paperless_mcp.ocr import UNCONFIRMED, PageReader, agree, numbers

from conftest import make_pdf

BILL = "IBAN: DE12 3456 7890 1234 5678 90\nBetrag | 813,16 €\nZiffer 1 | 7 | 75\nHeilbronn, 30.10.2026"


class FakeModel:
    def __init__(self, model, text, fail=False):
        self.model, self.text, self.fail, self.calls = model, text, fail, 0

    def transcribe(self, jpeg):
        self.calls += 1
        if self.fail:
            raise CardError("overloaded")
        return self.text, 1000, 200


def reader(haiku_text, vision_text, sonnet_text="", database_url=None, fail=False):
    haiku = FakeModel("anthropic:claude-haiku-5-5", haiku_text, fail)
    sonnet = FakeModel("anthropic:claude-sonnet-5-5", sonnet_text)
    return PageReader(haiku, sonnet, lambda jpeg: vision_text, database_url), haiku, sonnet


def test_numbers_ignore_layout_but_not_digits():
    assert numbers(BILL) >= {"12345678901234567890", "81316", "30102026"}
    other_layout = "Heilbronn, 30.10.2026\nZiffer 1 7 75 Betrag 813,16 €\nIBAN DE12 3456 7890 1234 5678 90"
    assert agree(BILL, other_layout)
    assert not agree(BILL, other_layout.replace("5678 90", "5678 98"))  # one wrong digit in the IBAN
    assert not agree(BILL, "IBAN: DE98 6206 2643 0021")  # cut off


def test_agreeing_readings_cost_one_cheap_call():
    page_reader, haiku, sonnet = reader(BILL, BILL.replace("\n", "   "))
    reading = page_reader.read(b"jpeg")
    assert (reading.text, reading.source) == (BILL, "ocr:claude-haiku-5-5")
    assert (haiku.calls, sonnet.calls) == (1, 0)
    assert page_reader.stats.tokens == {"anthropic:claude-haiku-5-5": [1000, 200]}
    assert page_reader.stats.cost() == pytest.approx(0.0002)


def test_disagreement_is_settled_by_the_stronger_model():
    vision = BILL.replace("5678 90", "")  # Apple Vision missed the end of the IBAN
    page_reader, _, sonnet = reader(BILL, vision, sonnet_text=BILL)
    reading = page_reader.read(b"jpeg")
    assert reading.source == "ocr:claude-sonnet-5-5" and sonnet.calls == 1
    assert page_reader.stats.rechecked == 1 and page_reader.stats.unconfirmed == 0

    page_reader, _, _ = reader(BILL, vision, sonnet_text=BILL.replace("5678 90", "5678 99"))
    reading = page_reader.read(b"jpeg")
    assert reading.source == "ocr:claude-sonnet-5-5" + UNCONFIRMED and page_reader.stats.unconfirmed == 1


def test_apple_vision_steps_in_when_claude_fails():
    page_reader, _, _ = reader("", "Text von Apple", fail=True)
    reading = page_reader.read(b"jpeg")
    assert (reading.text, reading.source) == ("Text von Apple", "ocr:apple-vision")
    assert PageReader(None, None, lambda jpeg: None).read(b"jpeg") is None


def test_readings_are_cached(database_url):
    page_reader, haiku, _ = reader(BILL, BILL, database_url=database_url)
    page_reader.read(b"same image")
    again, haiku_again, _ = reader("never asked", BILL, database_url=database_url)
    assert again.read(b"same image").text == BILL and haiku_again.calls == 0 and again.stats.cached == 1


def test_scanned_pages_and_photos_go_to_the_reader(monkeypatch):
    monkeypatch.setattr(extraction, "ocr_pages", lambda pdf, numbers: pytest.fail("Tesseract used"))
    prose = " ".join(["Der Mieter zahlt die Miete monatlich im Voraus auf das Konto des Vermieters."] * 3)
    page_reader, haiku, _ = reader(BILL + " Rechnung über die Behandlung des Patienten", BILL)
    pages = extract_pdf_pages(make_pdf([prose, "scan"]), page_reader)
    assert [p.source for p in pages] == ["pdf", "ocr:claude-haiku-5-5"] and haiku.calls == 1  # digital page kept

    photo = io.BytesIO()
    Image.new("RGB", (4000, 3000), "white").save(photo, "JPEG")
    seen = []
    page_reader = PageReader(None, None, lambda jpeg: seen.append(Image.open(io.BytesIO(jpeg)).size) or "Karte")
    assert extract_image(photo.getvalue(), page_reader)[0].text == "Karte"
    assert seen == [(2576, 1932)]  # scaled down to what the models read
