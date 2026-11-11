# OTTO priorities

## Current focus: functionality and UI/UX

- [ ] Review the Paperless upload, inbox, document review and search workflow;
  choose the first usability improvement with Fred.
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
- [ ] Phase 2: evaluate read-only document search and Q&A integrations.
