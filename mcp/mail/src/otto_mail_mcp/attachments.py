"""Mail attachments: their text for the model, and filing them into Paperless.

Filing only places a file in Paperless's consume folder, with the same checks as
the web UI's upload: PDFs and images only (recognised by their first bytes), a
safe filename, never overwriting, written whole so Paperless never sees half a file.
"""
from __future__ import annotations

import io
import os
import re
import tempfile
from pathlib import Path

from pdfminer.high_level import extract_text

from .mailbox import MailError, html_to_text

ROOT = Path(__file__).resolve().parents[4]  # src/otto_mail_mcp -> project root
UPLOAD_LIMIT = 200 * 1024 * 1024
# the formats Paperless imports (without Tika), recognised by their first bytes
SIGNATURES = {".pdf": (b"%PDF",), ".png": (b"\x89PNG",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
              ".tif": (b"II*\x00", b"MM\x00*"), ".tiff": (b"II*\x00", b"MM\x00*"), ".gif": (b"GIF8",),
              ".webp": (b"RIFF",)}
NO_TEXT = ("It has no text layer (a scan or photo), so there is no text to read. "
           "If it is worth keeping, file_attachment adds it to Fred's archive, where Paperless OCRs it.")


def text_of(filename: str, content_type: str, content: bytes, from_page: int = 1) -> str:
    """The attachment's text, page by page for PDFs; a clear note when it has none."""
    kind = file_kind(content)
    if kind == ".pdf":
        return pdf_text(content, from_page)
    if kind or content_type.startswith("image/"):
        return f"[{filename} is an image. {NO_TEXT}]"
    if content_type.startswith("text/"):
        decoded = content.decode("utf-8", errors="replace")
        return html_to_text(decoded) if content_type == "text/html" else decoded.strip()
    raise MailError(f"Cannot read {filename!r} ({content_type}): only PDFs and text attachments can be read.")


def pdf_text(content: bytes, from_page: int = 1) -> str:
    try:
        pages = extract_text(io.BytesIO(content)).split("\f")
    except Exception as error:  # pdfminer raises many kinds, encrypted PDFs among them
        raise MailError(f"Could not read the PDF: {error or type(error).__name__}") from error
    if pages and not pages[-1].strip():
        pages.pop()  # pdfminer ends every page with a form feed
    blank = [n for n, page in enumerate(pages, 1) if len(re.findall(r"\w", page)) < 5]
    if len(blank) == len(pages):
        return f"[The PDF has {len(pages)} page(s). {NO_TEXT}]"
    if from_page > len(pages):
        raise MailError(f"The PDF has only {len(pages)} page(s).")
    parts = [f"[Page {n}]\n" + (page.strip() if n not in blank else "[no text layer on this page]")
             for n, page in enumerate(pages, 1) if n >= from_page]
    return "\n\n".join(parts)


def file_kind(content: bytes) -> str | None:
    """The file's suffix by its first bytes, if Paperless can import it."""
    head = content[:12]
    for suffix, signatures in SIGNATURES.items():
        if head.startswith(signatures) and (suffix != ".webp" or head[8:12] == b"WEBP"):
            return suffix
    return None


def consume_dir() -> Path:
    return Path(os.environ.get("OTTO_CONSUME_DIR", ROOT / "consume"))


def safe_name(name: str) -> str:
    stem, suffix = os.path.splitext(Path(name.replace("\\", "/")).name)
    stem = re.sub(r"[^\w .,()+-]", "_", stem).strip(" .")[:120] or "upload"
    return stem + suffix.lower()


def file_into_paperless(filename: str, content: bytes) -> str:
    """Place the file in the consume folder; returns the name it was given there."""
    kind = file_kind(content)
    if not kind:
        raise MailError(f"{filename!r} is not a PDF or image (PNG, JPEG, TIFF, GIF, WebP), so Paperless cannot import it.")
    if len(content) > UPLOAD_LIMIT:
        raise MailError(f"{filename!r} is larger than 200 MB.")
    name = safe_name(filename)
    stem, suffix = os.path.splitext(name)
    if SIGNATURES.get(suffix) != SIGNATURES[kind]:
        stem, suffix = name, kind  # e.g. a PDF named without .pdf
    target = consume_dir()
    if not target.is_dir():
        raise MailError(f"The consume folder {target} does not exist.")
    # write next to the folder, then move it in whole, so Paperless never sees half a file
    fd, staged = tempfile.mkstemp(prefix=".upload-", dir=target.parent)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
    final, n = target / f"{stem}{suffix}", 1
    while final.exists():
        n += 1
        final = target / f"{stem} ({n}){suffix}"
    os.chmod(staged, 0o644)
    os.replace(staged, final)
    return final.name
