# OTTO priorities

## Current focus: OTTO agent architecture (see roadmap)

- [x] Phase 2a: read-only `otto-agent` Paperless user and OTTO's own Paperless
  MCP server, verified with Claude Code.
- [ ] Try Phase 2a on real questions (e.g. the siliXon termination clause) and
  collect them as an evaluation set for comparing models.
- [ ] Phase 2b: OTTO core database (facts with sources, cases, audit log) exposed
  as a second MCP server.
- [ ] Phase 3: OTTO service and web UI (chat, cases, approvals inbox, activity
  log), model gateway, scheduler; evaluate OpenClaw only as an optional front end.
- [ ] Back up before connecting email or banking (see Later).
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
