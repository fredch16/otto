#!/usr/bin/env bash
# Check that an encrypted backup can be restored: decrypt it, compare every
# exported file with the checksum Paperless recorded, and load OTTO's database
# dump into a scratch database. Plaintext only ever exists in a private temporary
# directory that is removed afterwards.
# Usage: bash scripts/verify-backup.sh backups/otto-....tar.age [identity-file]
set -euo pipefail
cd "$(dirname "$0")/.."
backup=$1
identity=${2:-.env.backup-key}

umask 077
work=$(mktemp -d)
scratch="otto_verify_$$"
cleanup() {
  rm -rf -- "$work"
  docker compose exec -T otto-db dropdb -U otto --if-exists "$scratch" 2>/dev/null || true
}
trap cleanup EXIT

age -d -i "$identity" "$backup" | tar -xf - -C "$work"
export_dir=$(find "$work" -mindepth 1 -maxdepth 1 -type d -name 'backup-*')
[ -f "$export_dir/manifest.json" ] || { echo "No manifest.json in $backup" >&2; exit 1; }

python3 - "$export_dir" <<'EOF'
import hashlib, json, sys
from pathlib import Path

root = Path(sys.argv[1])
documents = [e for e in json.loads((root / "manifest.json").read_text()) if e["model"] == "documents.document"]
problems = []
for entry in documents:
    for name_key, sum_key in (("__exported_file_name__", "checksum"), ("__exported_archive_name__", "archive_checksum")):
        name, expected = entry.get(name_key), entry["fields"].get(sum_key)
        if not name or not expected:
            continue
        path = root / name
        if not path.is_file():
            problems.append(f"missing {name}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            problems.append(f"checksum mismatch {name}")
trashed = sum(1 for e in documents if e["fields"].get("deleted_at"))
if problems:
    sys.exit("Paperless export damaged: " + "; ".join(problems))
print(f"Paperless: {len(documents) - trashed} documents (+{trashed} in trash), all files match their checksums")
EOF

[ -f "$export_dir/otto-db.sql" ] || { echo "No otto-db.sql in $backup" >&2; exit 1; }
docker compose exec -T otto-db createdb -U otto "$scratch"
docker compose exec -T otto-db psql -q -v ON_ERROR_STOP=1 -U otto -d "$scratch" < "$export_dir/otto-db.sql" > /dev/null
counts=$(docker compose exec -T otto-db psql -At -U otto -d "$scratch" -c \
  "SELECT (SELECT count(*) FROM cards) || ' cards, ' || (SELECT count(*) FROM changes) || ' logged changes'")
echo "OTTO database: restores cleanly ($counts)"
echo "Backup verified: $backup"
