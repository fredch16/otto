"""OTTO's local web UI: what OTTO knows (documents, cards, page texts) and what it
did (its change log, with undo).

It only reads, except for closing items on Fred's Pending list, undoing OTTO's own changes, which goes through
otto-tidy's undo as `otto-editor`, and for uploads, which it drops into Paperless's
consume folder (no Paperless rights needed). It listens on 127.0.0.1 only, and
accepts form posts only from its own pages, so other websites cannot trigger an
undo or upload.

Run: uv run --project mcp/paperless --env-file .env.agent --env-file .env.editor otto-ui
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlencode

import psycopg
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from . import server, settings
from .embedding import EmbeddingError
from .knowledge import KINDS, FactError
from .paperless import PaperlessError
from .pending import Pending, PendingError
from .tidy import PaperlessEditor, Tidy

log = logging.getLogger(__name__)
PORT = int(os.environ.get("OTTO_UI_PORT", "8100"))
UPLOAD_LIMIT = 200 * 1024 * 1024
# the formats Paperless imports (without Tika), recognised by their first bytes
SIGNATURES = {".pdf": (b"%PDF",), ".png": (b"\x89PNG",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
              ".tif": (b"II*\x00", b"MM\x00*"), ".tiff": (b"II*\x00", b"MM\x00*"), ".gif": (b"GIF8",),
              ".webp": (b"RIFF",)}
FIELD_NAMES = {"title": "Title", "created": "Date", "correspondent": "Correspondent", "document_type": "Type"}

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["value"] = lambda v: "—" if v is None else (v["name"] if isinstance(v, dict) else v)
templates.env.globals["field_name"] = lambda f: FIELD_NAMES.get(f, f)
templates.env.filters["mail_link"] = lambda message_id: "message://" + quote(message_id or "", safe="")
_tidy: Tidy | None = None
_pending: Pending | None = None


def tidy() -> Tidy | None:
    """For the change log and undo; None without the editor token."""
    global _tidy
    url, token, database = (os.environ.get(k) for k in ("PAPERLESS_URL", "PAPERLESS_EDITOR_TOKEN", "OTTO_DATABASE_URL"))
    if _tidy is None and url and token and database:
        _tidy = Tidy(database, PaperlessEditor(url, token), tidier=None)
    return _tidy


def use_tidy(value: Tidy | None) -> None:
    """Replace the change log (tests)."""
    global _tidy
    _tidy = value


def pending() -> Pending | None:
    """Fred's Pending list; None without OTTO's database."""
    global _pending
    if _pending is None and os.environ.get("OTTO_DATABASE_URL"):
        _pending = Pending(os.environ["OTTO_DATABASE_URL"])
    return _pending


def use_pending(value: Pending | None) -> None:
    """Replace the Pending list (tests)."""
    global _pending
    _pending = value


def open_pending() -> int:
    """For the count in the navigation; a database problem must not break every page."""
    try:
        return pending().count_open() if pending() else 0
    except psycopg.Error as error:
        log.warning("Pending list unavailable: %s", error)
        return 0


def render(request: Request, name: str, status_code: int = 200, **context) -> Response:
    return templates.TemplateResponse(request, name, {"message": request.query_params.get("msg"),
                                                      "open_pending": open_pending(), **context},
                                      status_code=status_code)


def overview(paperless, doc: dict, cards: dict[int, dict]) -> dict:
    card = cards.get(doc["id"])
    return {**server.summary(paperless, doc), "summary": card["summary"] if card else None}


async def documents(request: Request) -> Response:
    paperless, index = server.client(), server.index()
    query = request.query_params.get("q", "").strip()
    cards = index.cards()
    docs = {d["id"]: d for d in paperless.list_documents("id,title,created,correspondent,document_type,page_count")}
    results, note = None, None
    if query:
        try:
            vector = index.embedder.embed([query])[0]
        except EmbeddingError:
            vector, note = None, "Meaning-based search is unavailable (is Ollama running?); exact words only."
        grouped: OrderedDict[int, list[dict]] = OrderedDict()
        for hit in index.search(query, vector, 30):
            if hit["document_id"] in docs:
                grouped.setdefault(hit["document_id"], []).append(hit)
        results = [{"doc": overview(paperless, docs[i], cards), "hits": hits[:3]} for i, hits in grouped.items()]
    listing = sorted((overview(paperless, d, cards) for d in docs.values()),
                     key=lambda d: (d["created"] or "", d["id"]), reverse=True)
    return render(request, "documents.html", query=query, results=results, documents=listing, note=note,
                  waiting=waiting(), accept=",".join(SIGNATURES))


def consume_dir() -> Path:
    return Path(os.environ.get("OTTO_CONSUME_DIR", settings.ROOT / "consume"))


def waiting() -> list[str]:
    """Files in the consume folder: not imported yet, or refused by Paperless."""
    try:
        return sorted(f.name for f in consume_dir().iterdir() if f.is_file() and not f.name.startswith("."))
    except FileNotFoundError:
        return []


def safe_name(name: str) -> str:
    stem, suffix = os.path.splitext(Path(name.replace("\\", "/")).name)
    stem = re.sub(r"[^\w .,()+-]", "_", stem).strip(" .")[:120] or "upload"
    return stem + suffix.lower()


async def upload(request: Request) -> Response:
    """Hand files to Paperless via its consume folder; it imports them within a minute."""
    form = await request.form(max_files=50)
    target, done, refused = consume_dir(), [], []
    for item in form.getlist("files"):
        if isinstance(item, str) or not item.filename:
            continue
        name = safe_name(item.filename)
        head = await item.read(12)
        suffix = os.path.splitext(name)[1]
        signatures = SIGNATURES.get(suffix)
        if not signatures or not head.startswith(signatures) or (suffix == ".webp" and head[8:12] != b"WEBP"):
            refused.append(f"{item.filename} (not a PDF or image)")
            continue
        if item.size and item.size > UPLOAD_LIMIT:
            refused.append(f"{item.filename} (larger than 200 MB)")
            continue
        await item.seek(0)
        # write next to the folder, then move it in whole, so Paperless never sees half a file
        fd, staged = tempfile.mkstemp(prefix=".upload-", dir=target.parent)
        with os.fdopen(fd, "wb") as stream:
            while chunk := await item.read(1024 * 1024):
                stream.write(chunk)
        final, n = target / name, 1
        while final.exists():
            n += 1
            final = target / f"{os.path.splitext(name)[0]} ({n}){suffix}"
        os.chmod(staged, 0o644)
        os.replace(staged, final)
        done.append(final.name)
    parts = []
    if done:
        parts.append(f"Uploaded {len(done)} file{'s' if len(done) != 1 else ''}; Paperless imports "
                     "{} within a minute.".format("them" if len(done) != 1 else "it"))
    if refused:
        parts.append("Not uploaded: " + ", ".join(refused) + ".")
    return RedirectResponse(f"/?{urlencode({'msg': ' '.join(parts) or 'No file chosen.'})}", status_code=303)


async def document(request: Request) -> Response:
    doc_id = request.path_params["id"]
    paperless, index = server.client(), server.index()
    doc = paperless.document(doc_id)
    changelog = tidy()
    return render(request, "document.html", doc=server.summary(paperless, doc),
                  original_file_name=doc.get("original_file_name"), card=index.card(doc_id),
                  pages=index.stored_pages(doc_id),
                  changes=changelog.history(200, doc_id) if changelog else None)


async def document_file(request: Request) -> Response:
    if request.path_params["kind"] not in ("thumb", "preview"):
        return PlainTextResponse("Not found.", status_code=404)
    content, content_type = server.client().file(request.path_params["id"], request.path_params["kind"])
    return Response(content, media_type=content_type, headers={"Cache-Control": "private, max-age=300"})


async def activity(request: Request) -> Response:
    changelog = tidy()
    changes = changelog.history(500) if changelog else None
    titles = {d["id"]: d["title"] for d in server.client().list_documents("id,title")} if changes else {}
    return render(request, "activity.html", changes=changes, titles=titles)


async def undo(request: Request) -> Response:
    changelog = tidy()
    if changelog is None:
        return PlainTextResponse("Undo needs PAPERLESS_EDITOR_TOKEN (.env.editor).", status_code=503)
    form = await request.form()
    target = form.get("next", "/activity")
    target = target if target.startswith("/") and not target.startswith("//") else "/activity"
    document_id = int(form["document_id"]) if form.get("document_id") else None
    messages = changelog.undo([int(i) for i in form.getlist("change")], document_id)
    return RedirectResponse(f"{target}?{urlencode({'msg': '; '.join(messages) or 'Nothing to undo.'})}",
                            status_code=303)


async def pending_page(request: Request) -> Response:
    store = pending()
    if store is None:
        return PlainTextResponse("The Pending list needs OTTO_DATABASE_URL (.env.agent).", status_code=503)
    if request.method == "POST":
        form = await request.form()
        status = {"done": "done", "dismiss": "dismissed", "reopen": "open"}.get(form.get("action"), "")
        try:
            item = store.close(int(form.get("id", 0)), status)
            message = {"done": "Marked as done.", "dismissed": "Dismissed.", "open": "Back on the list."}[item["status"]]
        except (PendingError, ValueError) as error:
            message = str(error)
        return RedirectResponse(f"/pending?{urlencode({'msg': message})}", status_code=303)
    return render(request, "pending.html", items=store.items(), closed=store.items(open_only=False, limit=10))


async def knowledge_page(request: Request) -> Response:
    facts = server.knowledge().facts()
    groups: OrderedDict[str, list[dict]] = OrderedDict((kind, []) for kind in KINDS)
    for fact in facts:
        if fact["status"] == "confirmed" and fact["valid_until"] is None:
            groups[fact["kind"]].append(fact)
    return render(request, "knowledge.html", kinds=KINDS,
                  proposed=[f for f in facts if f["status"] == "proposed"],
                  current={KINDS[k]: v for k, v in groups.items() if v},
                  history=[f for f in facts if f["status"] == "confirmed" and f["valid_until"] is not None],
                  rejected=sum(1 for f in facts if f["status"] == "rejected"))


async def knowledge_action(request: Request) -> Response:
    form = await request.form()
    knowledge, action = server.knowledge(), form.get("action")
    fact_id = int(form["id"]) if form.get("id") else None
    try:
        if action == "confirm":
            fact = knowledge.confirm(fact_id, label=form.get("label"), value=form.get("value"))
            message = f"Confirmed: {fact['label']}."
        elif action == "reject":
            knowledge.reject(fact_id)
            message = "Rejected; it will not be proposed again."
        elif action == "end":
            knowledge.end(fact_id)
            message = "Marked as no longer valid; kept in the history."
        elif action == "correct":
            fact = knowledge.correct(fact_id, form.get("value", ""), form.get("label"))
            message = f"Corrected: {fact['label']}. The old value is kept in the history."
        elif action == "add":
            valid_from = date.fromisoformat(form["valid_from"]) if form.get("valid_from") else None
            fact = knowledge.add(form.get("kind", ""), form.get("label", ""), form.get("value", ""),
                                 form.get("notes", ""), valid_from)
            message = f"Added: {fact['label']}."
        else:
            message = "Unknown action."
    except (FactError, ValueError) as error:
        message = str(error)
    return RedirectResponse(f"/knowledge?{urlencode({'msg': message})}", status_code=303)


def backups() -> list[dict]:
    """The newest local backups, for the settings page."""
    files = sorted((settings.ROOT / "backups").glob("otto-*.tar.age"), key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"name": f.name, "at": datetime.fromtimestamp(f.stat().st_mtime), "mb": f.stat().st_size / 1e6}
            for f in files[:5]]


async def settings_page(request: Request) -> Response:
    if request.method == "POST":
        form = await request.form()
        settings.save({t.key: form.get(t.key) == "on" for t in settings.TOGGLES})
        return RedirectResponse("/settings?msg=Saved.", status_code=303)
    return render(request, "settings.html", toggles=settings.TOGGLES, values=settings.load(), backups=backups())


def same_origin_only(app):
    """Reject other hosts (DNS rebinding) and form posts from other sites (CSRF)."""
    hosts = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode(): v.decode() for k, v in scope["headers"]}
            host = headers.get("host", "")
            if host not in hosts:
                return await PlainTextResponse("Unknown host.", status_code=421)(scope, receive, send)
            if scope["method"] not in ("GET", "HEAD") and headers.get("origin") != f"http://{host}":
                return await PlainTextResponse("Cross-site request refused.", status_code=403)(scope, receive, send)
        return await app(scope, receive, send)

    return guarded


async def paperless_error(request: Request, error: PaperlessError) -> Response:
    return render(request, "error.html", status_code=502, error=str(error))


app = Starlette(
    routes=[
        Route("/", documents),
        Route("/upload", upload, methods=["POST"]),
        Route("/documents/{id:int}", document),
        Route("/documents/{id:int}/{kind:str}", document_file),
        Route("/activity", activity),
        Route("/pending", pending_page, methods=["GET", "POST"]),
        Route("/knowledge", knowledge_page),
        Route("/knowledge", knowledge_action, methods=["POST"]),
        Route("/undo", undo, methods=["POST"]),
        Route("/settings", settings_page, methods=["GET", "POST"]),
    ],
    exception_handlers={PaperlessError: paperless_error},
)


def main() -> None:
    import uvicorn

    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    print(f"OTTO: http://127.0.0.1:{PORT}")
    uvicorn.run(same_origin_only(app), host="127.0.0.1", port=PORT, log_level="warning")

