# OTTO priorities

## Current focus: OTTO agent architecture (see roadmap)

- [x] Phase 2a: read-only `otto-agent` Paperless user and OTTO's own Paperless
  MCP server, verified with Claude Code.
- [x] Document index: hybrid passage search (local bge-m3 embeddings + exact
  words), page-level OCR for unreadable pages, retrieval eval (`otto-eval`),
  disposable test stack.
- [ ] Import the real backlog (50-100 documents, quality scans), add real
  questions to `evals/retrieval.yaml`, measure and tune.
- [x] Document cards with Sonnet 5.5 (type, parties, dates, key facts,
  deadlines, summary, page references), indexed as passages.
- [ ] Compare card models once the backlog is in: Haiku 5.5 (20x cheaper), a
  local model through Ollama, optionally OpenAI. Add adapters as needed.
- [ ] Use the Message Batches API for large imports (half price, not urgent).
- [x] Mail (iCloud): search, read, drafts with Paperless attachments; no sending.
- [ ] Better document titles (and correspondent, type, date) from cards for
  documents with meaningless names (e.g. photo UUIDs): OTTO proposes, Fred
  approves, applied through a separate write-enabled Paperless user, old values
  logged so every change can be undone.
- [ ] Knowledge base for facts that are in no document or need a decision
  (current address, phone, IBANs and their use, preferences), each with source
  and date, confirmed by Fred.
- [ ] OTTO worker: background tasks started from chat, approvals Fred gives
  outside the model (e.g. phone push), audit log. Then PDF form filling, then
  sending mail after approval.
- [ ] Mail attachments into Paperless (after approval).
- [ ] Gmail as a second mailbox (low priority; IMAP with app password).
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
