"""OTTO's local web UI: what OTTO knows (documents, cards, page texts) and what it
did (its change log, with undo).

It only reads, except for undoing OTTO's own changes, which goes through
otto-tidy's undo as `otto-editor`. It listens on 127.0.0.1 only, and accepts form
posts only from its own pages, so other websites cannot trigger an undo.

Run: uv run --project mcp/paperless --env-file .env.agent --env-file .env.editor otto-ui
"""
from __future__ import annotations

import logging
import os
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from . import server
from .embedding import EmbeddingError
from .paperless import PaperlessError
from .tidy import PaperlessEditor, Tidy

log = logging.getLogger(__name__)
PORT = int(os.environ.get("OTTO_UI_PORT", "8100"))
FIELD_NAMES = {"title": "Title", "created": "Date", "correspondent": "Correspondent", "document_type": "Type"}

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["value"] = lambda v: "—" if v is None else (v["name"] if isinstance(v, dict) else v)
templates.env.globals["field_name"] = lambda f: FIELD_NAMES.get(f, f)
_tidy: Tidy | None = None


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


def render(request: Request, name: str, status_code: int = 200, **context) -> Response:
    return templates.TemplateResponse(request, name, {"message": request.query_params.get("msg"), **context},
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
    return render(request, "documents.html", query=query, results=results, documents=listing, note=note)


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
        Route("/documents/{id:int}", document),
        Route("/documents/{id:int}/{kind:str}", document_file),
        Route("/activity", activity),
        Route("/undo", undo, methods=["POST"]),
    ],
    exception_handlers={PaperlessError: paperless_error},
)


def main() -> None:
    import uvicorn

    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    print(f"OTTO: http://127.0.0.1:{PORT}")
    uvicorn.run(same_origin_only(app), host="127.0.0.1", port=PORT, log_level="warning")

