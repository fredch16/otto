"""Split page text into passages small enough to retrieve precisely."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .paperless import Page

MAX_CHARS = 1200


@dataclass(frozen=True)
class Chunk:
    page: int
    ordinal: int
    text: str


def _pieces(text: str) -> list[str]:
    """Paragraphs, with over-long ones split at sentence (or word) boundaries."""
    pieces = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        while len(paragraph) > MAX_CHARS:
            cut = max(paragraph.rfind(". ", 0, MAX_CHARS), paragraph.rfind("\n", 0, MAX_CHARS))
            if cut < MAX_CHARS // 2:
                cut = paragraph.rfind(" ", 0, MAX_CHARS)
            if cut <= 0:
                cut = MAX_CHARS
            pieces.append(paragraph[: cut + 1].strip())
            paragraph = paragraph[cut + 1 :].strip()
        if paragraph:
            pieces.append(paragraph)
    return pieces


def chunk_pages(pages: list[Page]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in pages:
        current = ""
        for piece in _pieces(page.text):
            if current and len(current) + len(piece) + 2 > MAX_CHARS:
                chunks.append(Chunk(page.number, len(chunks), current))
                current = ""
            current = f"{current}\n\n{piece}" if current else piece
        if current:
            chunks.append(Chunk(page.number, len(chunks), current))
    return chunks
