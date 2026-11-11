import shutil

import pytest

from otto_paperless_mcp import extraction
from otto_paperless_mcp.extraction import extract_pdf_pages, parse_sidecar, unreadable

from conftest import make_pdf

PROSE = ("This page contains plenty of ordinary readable words, enough for the readability check "
         "to accept it as real digital text without any OCR")


def test_unreadable_detects_short_and_undecodable_pages():
    assert unreadable("")
    assert unreadable("Seite 3")
    assert unreadable("(cid:12)(cid:7)" * 40 + " Vertrag Seite")
    assert not unreadable(PROSE)


def test_parse_sidecar_maps_blocks_to_page_numbers():
    sidecar = "[OCR skipped on page(s) 1-6]\fseven\fskip\f[OCR skipped on page(s) 9]\ften\f[OCR skipped on page(s) 11-13]"
    # an unexpected extra block between OCRed pages is still counted as a page
    assert parse_sidecar(sidecar, [7, 8, 10]) == {7: "seven", 8: "skip", 10: "ten"}
    assert parse_sidecar("first\fsecond", [1, 2]) == {1: "first", 2: "second"}


def test_only_unreadable_pages_use_ocr(monkeypatch):
    calls = []

    def fake_ocr(pdf, numbers):
        calls.append(numbers)
        return {2: "Recovered text from the scanned page with many more readable words than before"}

    monkeypatch.setattr(extraction, "ocr_pages", fake_ocr)
    pages = extract_pdf_pages(make_pdf([PROSE, "scan"]))
    assert calls == [[2]]
    assert [(p.number, p.source) for p in pages] == [(1, "pdf"), (2, "ocr")]
    assert pages[0].text == PROSE


def test_ocr_result_is_ignored_when_it_is_worse(monkeypatch):
    monkeypatch.setattr(extraction, "ocr_pages", lambda pdf, numbers: {1: ""})
    pages = extract_pdf_pages(make_pdf(["Short page"]))
    assert (pages[0].source, pages[0].text) == ("pdf", "Short page")


@pytest.mark.real_ocr
@pytest.mark.skipif(not shutil.which("ocrmypdf"), reason="ocrmypdf not installed")
def test_real_ocr_recovers_page_text():
    """Guards against OCR output we cannot read back (a silent failure we hit once)."""
    pdf = make_pdf([PROSE, "Kuendigungsfrist drei Monate zum Monatsende"])
    recovered = extraction.ocr_pages(pdf, [2])
    assert "Monate" in recovered.get(2, "")
