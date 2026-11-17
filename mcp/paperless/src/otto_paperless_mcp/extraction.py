"""Page text from original files: exact digital text where readable, OCR only where not.

Born-digital pages keep their text: OCRing them would only add errors (numbers
included). Scanned pages, photos and pages with broken font encodings are read
by OTTO's page reader (Claude, checked by Apple Vision; see ocr.py), or by
Tesseract on a temporary copy when no reader is configured.
"""
from __future__ import annotations

import io
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer

from .ocr import PageReader, render_pdf_page, to_jpeg
from .paperless import Page

log = logging.getLogger(__name__)
CID = re.compile(r"\(cid:\d+\)")
WORD = re.compile(r"[A-Za-zÄÖÜäöüß]{3,}")
MIN_WORDS = 15
SKIPPED = re.compile(r"\[OCR skipped on page\(s\) (\d+)(?:-(\d+))?\]")
OCR_LANGUAGES = "deu+eng"


def split_pdf_pages(pdf: bytes) -> list[Page]:
    pages = []
    for number, layout in enumerate(extract_pages(io.BytesIO(pdf)), start=1):
        text = "".join(item.get_text() for item in layout if isinstance(item, LTTextContainer))
        pages.append(Page(number, re.sub(r"[ \t]+\n", "\n", text).strip()))
    return pages


def unreadable(text: str) -> bool:
    """Too few words, or mostly undecodable glyphs (fonts without a character map)."""
    glyphs = len(CID.findall(text))
    words = len(WORD.findall(CID.sub(" ", text)))
    return words < MIN_WORDS or glyphs > words


def parse_sidecar(sidecar: str, numbers: list[int]) -> dict[int, str]:
    """Map OCRmyPDF sidecar text to page numbers.

    The sidecar holds one form-feed separated block per OCRed page and a single
    "[OCR skipped on page(s) a-b]" block for each run of skipped pages.
    """
    texts, page = {}, 1
    for block in sidecar.split("\f"):
        skipped = SKIPPED.fullmatch(block.strip())
        if skipped:
            page = int(skipped.group(2) or skipped.group(1)) + 1
            continue
        if page in numbers:
            texts[page] = block.strip()
        page += 1
    return texts


def ocr_pages(pdf: bytes, numbers: list[int]) -> dict[int, str]:
    """OCR the given pages of a temporary copy; the original is never changed."""
    ocrmypdf = shutil.which("ocrmypdf")
    if not ocrmypdf:
        log.warning("ocrmypdf not installed; pages %s stay unreadable", numbers)
        return {}
    with tempfile.TemporaryDirectory() as tmp:
        source, sidecar = Path(tmp) / "in.pdf", Path(tmp) / "text.txt"
        source.write_bytes(pdf)
        result = subprocess.run(
            [ocrmypdf, "-q", "-l", OCR_LANGUAGES, "--force-ocr", "--invalidate-digital-signatures",
             "--output-type", "pdf", "--pages", ",".join(map(str, numbers)),
             "--sidecar", str(sidecar), str(source), str(Path(tmp) / "out.pdf")],
            capture_output=True, text=True, timeout=900,
        )
        if result.returncode != 0:
            log.warning("OCR failed for pages %s: %s", numbers, result.stderr.strip()[-300:])
            return {}
        return parse_sidecar(sidecar.read_text(), numbers)


def read_pages(pdf: bytes, numbers: list[int], reader: PageReader) -> dict[int, tuple[str, str]]:
    """Page number -> (text, source) for the pages the reader could read."""
    found = {}
    for number in numbers:
        reading = reader.read(render_pdf_page(pdf, number))
        if reading is not None:
            found[number] = (reading.text, reading.source)
    return found


def extract_pdf_pages(pdf: bytes, reader: PageReader | None = None) -> list[Page]:
    pages = split_pdf_pages(pdf)
    bad = [p.number for p in pages if unreadable(p.text)]
    if not bad:
        recovered = {}
    elif reader is not None:
        recovered = read_pages(pdf, bad, reader)
    else:
        recovered = {number: (text, "ocr") for number, text in ocr_pages(pdf, bad).items()}
    result = []
    for page in pages:
        text, source = recovered.get(page.number, ("", page.source))
        if text and len(WORD.findall(text)) > len(WORD.findall(CID.sub(" ", page.text))):
            result.append(Page(page.number, text, source))
        else:
            result.append(Page(page.number, CID.sub("", page.text).strip(), page.source))
    return result


def extract_image(image: bytes, reader: PageReader | None) -> list[Page]:
    """A photo or scan stored as an image: one page, read by the page reader."""
    reading = reader.read(to_jpeg(image)) if reader is not None else None
    return [Page(1, reading.text, reading.source)] if reading else []
