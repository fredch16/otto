# OTTO priorities

## Current focus: OTTO agent architecture (see roadmap)

- [x] Phase 2a: read-only `otto-agent` Paperless user and OTTO's own Paperless
  MCP server, verified with Claude Code.
- [x] Document index: hybrid passage search (local bge-m3 embeddings + exact
  words), page-level OCR for unreadable pages, retrieval eval (`otto-eval`),
  disposable test stack.
- [ ] Import the real backlog (50-100 documents, quality scans), add real
  questions to `evals/retrieval.yaml`, measure and tune.
- [ ] Document cards: structured extraction per document (type, parties, dates,
  amounts, reference numbers, deadlines, summary, page references). Needs an
  Anthropic API key with a spending limit; start with Sonnet, compare Haiku.
- [ ] Phase 2b: facts with sources, cases, audit log as a second MCP server.
- [ ] Phase 3: OTTO service and web UI (chat, cases, approvals inbox, activity
  log), model gateway, scheduler; evaluate OpenClaw only as an optional front end.
- [ ] Back up before connecting email or banking (see Later).
- [ ] Review the Paperless upload, inbox, document review and search workflow;
  choose the first usability improvement with Fred.
- [ ] Paperless' own search cannot see pages it never OCRed (scanned pages in
  digital PDFs, fonts without character maps); OTTO's index can. Consider
  writing recovered text back once write access with approval exists.
- Keep Phase 1 within Paperless-ngx, PostgreSQL, Redis and supporting local
  operations, with localhost-only access and persistent storage.

## Later

- [ ] Back up the real document archive and verify restoration. The existing
  encrypted backup script and synthetic-document restore rehearsal are complete.
- [ ] Set up automatic encrypted backups, retain recovery keys separately, and
  copy backups to external or off-site storage.
- [ ] Evaluate improved OCR for phone photos, including local Apple Vision OCR
  and searchable PDF output. Digital PDFs and properly scanned documents are
  sufficient for the current workflow.
