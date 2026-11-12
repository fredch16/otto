# OTTO — local document archive

Phase 1 uses the official Paperless-ngx Docker deployment pattern, with PostgreSQL
and Redis. Paperless is the authoritative archive; no AI services receive files.
The UI is bound to `127.0.0.1:8000`; neither database nor broker publishes a port.

## Layout and storage

```text
compose.yaml       three services, persistent volumes, loopback-only UI
.env.example       template without secrets
.env               generated local credentials (ignored, mode 600)
scripts/init.py    creates secrets once and maps host UID/GID
scripts/backup.sh  consistent encrypted export
consume/           incoming PDFs, JPEGs and PNGs (ignored)
export/            export/restore staging (ignored)
backups/           encrypted archives (ignored)
```

Docker volumes `otto_media` (originals, archive PDFs, thumbnails), `otto_data`
(indexes and application data), `otto_pgdata` (PostgreSQL) and `otto_redisdata`
(task broker) survive container recreation and `docker compose down`.
On macOS they live **inside the runtime's Linux VM**, not in this repository.
Inspect locations with `docker volume inspect otto_media otto_pgdata`.
Deleting Colima's VM or using `docker compose down -v` destroys those volumes.
Copying the repository alone is not a document backup.

## Install and start on macOS

Requirements: Apple Silicon or Intel Mac, Homebrew, Python 3 and a running Docker
runtime. Docker Desktop also works; use one runtime/context at a time.

```sh
brew install colima docker docker-compose age
colima start --cpu 4 --memory 6 --disk 60
```

Homebrew's Compose plugin may need registering. Merge this entry into your
`~/.docker/config.json`, retaining any existing settings:

```json
{"cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]}
```

On Intel, use `/usr/local/lib/docker/cli-plugins` instead. Confirm with
`docker compose version` and `docker info`.

```sh
cd /Users/fred/Projects/otto
python3 scripts/init.py
docker compose config --quiet
docker compose pull
docker compose up -d --wait --wait-timeout 600
docker compose exec webserver createsuperuser
```

Choose your administrator username/password interactively; no administrator
password is stored in Compose. Open <http://localhost:8000> and sign in.
For the initial installation, an administrator named `otto` has already been
created. Its generated password is in the private, ignored `.env.admin` file;
open it locally, sign in, and change the password in Paperless. You can skip
`createsuperuser` for this installation. `.env.admin` is only an initial password
note and is not updated when you change your password.
German and English OCR (`deu+eng`) are included in the official image; timezone
is Europe/Berlin. Polling checks `consume/` every ten seconds for macOS mounts.
Keep real `.env` values private; full `docker compose config` output contains them.

## Import and verify

Upload PDFs or scanned JPEG/PNG files through the web UI, or **copy** them into
`consume/`. Successfully imported files disappear from that folder; originals
remain in the media volume and can be downloaded from Paperless. Keep your source
files until you verify the archive. Transfer an entire file before placing it in
`consume/`; do not scan or sync partially written files directly there.

Acceptance check with non-sensitive samples:

1. Import a German scan, an English scan and a text PDF. Wait for processing.
2. Search distinctive words from their contents and inspect the recognized text.
3. Download originals and compare them with your source files.
4. Run `docker compose down`, then `docker compose up -d --wait`. Confirm the
   same documents, searches and originals remain available.
5. Create a backup and rehearse restoration into a separate Compose project.

Initial verification on 2026-11-10 passed with two synthetic samples: a German
scanned PDF and an English PNG. Both were recognized, found through full-text API
search, and downloaded byte-for-byte unchanged. Both survived container recreation.
An encrypted export was decrypted and imported into isolated volumes; restored
searches and original checksums passed. The temporary restoration environment was
removed, and the two `OTTO-TEST-*` samples were deleted on 2026-11-12. Synthetic
documents now live in the disposable test stack (`scripts/test-env.sh`).

## Daily operation

```sh
docker compose ps
docker compose logs --tail 100 webserver
docker compose stop                 # pause services, retain containers/data
docker compose up -d --wait         # resume or recreate
docker compose down                # remove containers/network, retain volumes
```

Colima must be running first. `colima stop` stops the VM; `colima start` resumes
it. This setup does not add automatic login startup. A sleeping/offline Mac does
not process documents. Future Linux hosting runs with Docker Engine and Compose;
`restart: unless-stopped` restarts services when the daemon starts.

## Encrypted backups

Install `age` (above). From an interactive terminal, while the stack is running:

```sh
bash scripts/backup.sh
```

The script temporarily stops Paperless, exports with the official exporter,
includes `.env` and the exact Compose configuration, encrypts the archive using
an interactively entered passphrase, and restarts Paperless even on error.
Keep the passphrase in your password manager; losing it loses access to the backup.
Only successfully encrypted staging is removed. On failure, inspect `export/`
for sensitive plaintext before sharing/copying anything.

Copy encrypted backups to a separate disk and eventually off-site storage.
Backups on the same Mac do not protect against loss of the Mac. Pending files in
`consume/` are not exported: retain their originals or wait for processing first.
API tokens are not exported and must be regenerated after restoration.

For unattended backups, pass an age public recipient as the first argument:
`bash scripts/backup.sh age1...`. Keep its private identity separately. The initial
backup rehearsal uses a private identity in `.env.backup-key` (ignored, mode 600);
copy it to your password manager or other secure storage separately from backups.
Decrypt that backup with `age -d -i .env.backup-key path/to/backup.tar.age`.

## Restore, rehearse and migrate

Use the **same Paperless version** as the export (initially 3.4.2). Restore to a
fresh database/media set, never over an existing archive. Example rehearsal:

```sh
mkdir -m 700 /tmp/otto-restore
age -d backups/otto-YYYYMMDDTHHMMSSZ.tar.age | tar -xf - -C /tmp/otto-restore
```

Check that decryption and extraction both succeeded. The extracted
`backup-.../` directory contains the export manifest, documents, `.env` and
`compose.yaml`. Place its `.env` and `compose.yaml` in a new private project
directory under your home directory (Colima shares `/Users`, not `/private/tmp`),
copy the exported contents into that project's `export/`, and create
an empty `consume/`. Protect `.env` with `chmod 600 .env`.

Stop the original stack to free port 8000. From the new project directory:

```sh
docker compose -p otto-restore up -d --wait db broker
docker compose -p otto-restore run --rm --no-deps --entrypoint /bin/sh webserver -c 'mkdir -p /usr/src/paperless/data/index && python manage.py migrate --noinput && python manage.py document_importer /usr/src/paperless/export'
docker compose -p otto-restore up -d --wait --wait-timeout 600
```

The explicit project name creates isolated `otto-restore_*` volumes. Sign in
with the backed-up user credentials, verify search and original downloads, then
stop the rehearsal with `docker compose -p otto-restore down` and restart the
original from its directory. Remove private plaintext staging after verification.

For Linux migration, use the same export/import process on the new host and
choose its UID/GID in `.env` (`id -u`, `id -g`). Do not copy live PostgreSQL files
across machines or change PostgreSQL major versions against an existing volume.
The SSD can later host Docker storage or backups; it is not required now.
Remote access, if needed later, should use a private VPN or SSH tunnel.

## Updates and later phases

Paperless is pinned to 3.4.2; PostgreSQL 18 and Redis 8 track their major series.
Back up and verify restoration before updates. Review release notes, change the
Paperless version deliberately, then `docker compose pull` and
`docker compose up -d --wait`. Keep the old configuration with its backup.
Never enable unattended major database upgrades.

No banking connections, workflow engines, custom knowledge database or
consequential-action tools are installed. Document contents must be treated as
untrusted data by any agent.

## Read-only agent access and document index (Phase 2)

AI assistants reach the archive only through OTTO's own MCP server
(`mcp/paperless/`), which works with any MCP client (Claude Code, Codex, others).
It authenticates as the Paperless user `otto-agent`, which has view-only
permissions and no web login, so the archive cannot be changed through it even if
the server or the model misbehaves. Tools:

- `search_passages`: hybrid search over page-level passages, by meaning (local
  multilingual embeddings, so English questions find German text) and by exact
  words (names, numbers). The main entry point for questions.
- `search_documents`: Paperless full-text search with filters (correspondent,
  type, tags, dates), and documents similar to a given one.
- `get_document`, `get_document_text` (page-numbered text for citations), `list_metadata`.

### Document index

OTTO keeps its own index in the `otto-db` service (PostgreSQL with pgvector,
`127.0.0.1:5433`, volume `otto_ottodata`). For each document it stores the text
of every page and embedded passages of about 1,200 characters. Paperless stays
the source of truth: the index is derived, holds only documents visible to
`otto-agent`, and can be rebuilt at any time, so it needs no backup (rewriting lost document
cards costs a few dollars per 100 documents).

Page text: the exact digital text where it is readable. Paperless skips OCR for
any PDF that already contains some text, so scanned pages inside digital PDFs
and pages whose fonts lack a character map stay unreadable in Paperless (and
its search). OTTO detects such pages and OCRs only those, on a temporary copy;
originals are never changed. OCRing whole documents was rejected because it
altered about 11% of good digital words, numbers included. OCRed pages are
marked as such in search results and document text.

Embeddings come from Ollama (`bge-m3`, local, nothing leaves the Mac). Searches
refresh the index at most once a minute, picking up new, changed, deleted and
unshared documents. To update or rebuild it by hand (it reports OCRed pages and
pages without readable text):

```sh
uv run --project mcp/paperless --env-file .env.agent --env-file .env.models otto-index [--rebuild]
```

### Document cards

A card is a structured summary of a whole document, written once per document
version by a language model: document type, date, parties and their roles, a
short summary, key facts (amounts, reference numbers, notice periods...) and
deadlines, each with its page. Cards are indexed as passages, so they answer
questions about the document as a whole ("who is my employer?") that no single
page answers, and `get_document` shows the full card. Search results mark card
passages; facts should be confirmed on the cited pages.

Cards need a model provider key in the private, ignored `.env.models`
(`ANTHROPIC_API_KEY=...`; `scripts/init.py` creates the file empty). Without a
key, cards are off and everything else works. `OTTO_CARD_MODEL` chooses the
model as `provider:model` (default `anthropic:claude-sonnet-5-5`; `off`
disables cards). Each card records its model. Cards survive `--rebuild` and are
rewritten automatically only when a document's text changes (not when its title
or other metadata is edited); choosing another model applies to new cards only. To rewrite existing cards with the
current model (e.g. to compare models with `otto-eval`), ask for it explicitly:

```sh
uv run --project mcp/paperless --env-file .env.agent --env-file .env.models otto-index --rewrite-cards        # all
uv run --project mcp/paperless --env-file .env.agent --env-file .env.models otto-index --rewrite-cards 6 12   # some
```

`otto-index` prints tokens and the estimated cost.

On 2026-11-12, Sonnet 5.5 wrote cards for the 5 documents in about 43 seconds
for $0.12 (35k input, 5k output tokens): about $2-3 per 100 documents of
similar length. Writing a card sends that document's full text to the provider.

### Tidying: titles, correspondents, types

`otto-tidy` lets OTTO file documents on its own, from their cards. Fred decided
that such internal, reversible changes need no approval, as long as every change
is logged and can be undone. The rules:

- A document is renamed only while its title is still its original filename
  (`IMG_4821`, a photo UUID, `Scan_0001`...). Titles Fred set are never touched.
- On those same documents, the date is corrected from the card (Paperless
  guesses it from the text and often picks the wrong one).
- Correspondent and document type are filled in only when empty. Existing ones
  are reused (also when they differ only in case or legal form, e.g. "GmbH");
  document types stay broad (Contract, Invoice, Certificate, Official notice...).
- Each document is considered once per card, so repeated runs cost nothing.

Titles also name the files OTTO attaches to mail drafts: Paperless names a
download `{date} {correspondent} {title}`, e.g. `2026-10-27 Die Wissensstadt
Heilbronn Arbeitsvertrag Manager.pdf`.

Changes are made as the Paperless user `otto-editor`, which may change documents
and add correspondents and document types but cannot delete anything. Its token
lives in the private, ignored `.env.editor` and is never given to an MCP server,
so models in chat still have read-only access. The change log, with old values,
is in OTTO's database (`changes` table).

```sh
tidy() { uv run --project mcp/paperless --env-file .env.agent --env-file .env.editor --env-file .env.models otto-tidy "$@"; }
tidy --dry-run           # show what would change
tidy                     # update index and cards, then tidy
tidy --log               # recent changes with their ids
tidy --undo 14 15        # undo changes (skipped if the field was edited since)
tidy --undo-document 12  # undo everything OTTO changed on a document
tidy --retidy 12         # consider a document again, e.g. after an undo
```

Undone changes are not made again unless asked with `--retidy`. On 2026-11-12,
tidying the 5 documents with Sonnet 5.5 cost about $0.02.

### Web UI

`otto-ui` is a small local page for seeing what OTTO knows and what it did,
without Paperless' clutter:

- **Documents**: every document with its date, correspondent, type and card
  summary, newest first; search by meaning or exact words (OTTO's index).
- **Document**: the card (summary, parties, key facts and deadlines linked to
  their pages), the page texts as indexed (OTTO's OCR marked), a PDF preview,
  and OTTO's changes to it with undo.
- **Activity**: everything OTTO changed, with undo.

```sh
uv run --project mcp/paperless --env-file .env.agent --env-file .env.editor otto-ui   # http://127.0.0.1:8100
```

It only reads, except for undoing OTTO's changes (as `otto-editor`; without
`.env.editor` the activity log is hidden). It listens on 127.0.0.1 only and
refuses requests for other host names and form posts from other sites. Server-
rendered pages, no JavaScript; `OTTO_UI_PORT` changes the port.

### Setup

Requirements:

```sh
brew install uv ollama ocrmypdf tesseract-lang
uv python install 3.13
brew services start ollama     # serves on 127.0.0.1:11434
ollama pull bge-m3
python3 scripts/init.py        # adds OTTO_DB_PASSWORD to .env if missing
docker compose up -d --wait
```

Set up or refresh the agent users (idempotent; grants access to existing
documents and writes the tokens and index database address to the private,
ignored `.env.agent` and `.env.editor`):

```sh
bash scripts/setup-agent.sh
```

A Paperless workflow, *OTTO agent: grant read access*, shares every newly added
document with `otto-agent` (view) and `otto-editor` (view and change). Documents you deliberately want hidden from OTTO can
have that permission removed in Paperless.

Claude Code picks up the server from `.mcp.json` when started in this directory
(approve `otto-paperless` on first use). For other clients, run from this
directory: `uv run --project mcp/paperless --env-file .env.agent otto-paperless-mcp`
(stdio; add `--env-file .env.models` for cards). Tests: `cd mcp/paperless && uv run --env-file ../../.env.agent pytest`
(fake Paperless, no real documents; the index tests create and drop a throwaway
database and are skipped without `--env-file`).

### Mail

The `otto-mail` server (`mcp/mail`) searches and reads Fred's mailbox and saves
drafts, with documents from Paperless attached as original files. It cannot
send: there is no sending code, the drafts go to the mailbox's *Drafts* folder,
and Fred reviews and sends them from his own mail app. Reading never marks mail
as read. Searches run on the mail server (iCloud: about 4 seconds across all
folders except junk, trash and drafts).

The login lives in the private, ignored `.env.mail` (`MAIL_PROVIDER=icloud`,
`MAIL_USER`, `MAIL_PASSWORD`). For iCloud this is an app-specific password from
account.apple.com (Sign-In and Security); it also grants access to contacts and
calendars, OTTO only uses mail, and it can be revoked there at any time.
`MAIL_PROVIDER=gmail` works the same way with a Google app password. Claude
Code starts the server from `.mcp.json`; tests: `cd mcp/mail && uv run pytest`
(fake IMAP server, no real mail).

### Test stack

Experiments (uploads, deletions, reindexing, new pipeline steps) run against a
disposable second stack, never the real archive. It uses the same Compose file
with its own project name, secrets (`.env.test`), volumes, folders (`test/`) and
loopback ports (Paperless 8001, OTTO database 5434), seeded with invented
documents (rental contract, insurance policy, invoice, tax assessment, gym
membership):

```sh
bash scripts/test-env.sh up      # start it, create otto-agent (.env.agent-test)
bash scripts/test-env.sh seed    # add synthetic documents; first run can take minutes
uv run --project mcp/paperless --env-file .env.agent-test otto-index
bash scripts/test-env.sh down    # delete it completely, including its volumes
```

After documents are deleted, Paperless 3.4.2 may return no "similar documents"
(the log shows *More Like This scoring panicked*) until the index is rebuilt:
`docker compose exec -u paperless webserver python3 manage.py document_index reindex`.

Questions with known answers live in `evals/` (private, ignored; only
`evals/README.md` is tracked). `evals/retrieval.yaml` lists questions with the
document pages that answer them; score search quality and speed after changes
to extraction, chunking, embeddings or ranking:

```sh
uv run --project mcp/paperless --env-file .env.agent --env-file .env.models otto-eval evals/retrieval.yaml
```

Page 0 in an expected page list stands for the document's card. The eval reports
scores with cards and for pages only, so cards cannot hide worse page ranking.

Document text returned by the tools goes to whichever model provider the client
uses. Paperless management commands must run as the `paperless` user
(`docker compose exec -u paperless webserver ...`); files created as root break
search-index reloads and later processing.

References checked 2026-11-10:

- [Official setup](https://docs.paperless-ngx.com/setup/)
- [Official Compose layout](https://github.com/paperless-ngx/paperless-ngx/blob/main/docker/compose/docker-compose.postgres.yml)
- [Configuration](https://docs.paperless-ngx.com/configuration/)
- [Exporter/importer and backups](https://docs.paperless-ngx.com/administration/)
- [Pinned release](https://github.com/paperless-ngx/paperless-ngx/releases/tag/v3.4.2)
