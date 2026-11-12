#!/usr/bin/env bash
# Unattended nightly backup, run by launchd (see scripts/install-launchd.sh):
# start Docker and the stack if needed, back up encrypted to the public key of
# .env.backup-key, verify the result and keep the newest backups locally.
# If switched on in the web UI's Settings (BACKUP_ICLOUD=on in .env.settings),
# also copy them to iCloud Drive/OTTO Backups (or OTTO_BACKUP_COPY_DIR). The
# backups are encrypted, so the copy reveals nothing without the private key.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
keep_local=${OTTO_BACKUP_KEEP:-14}
keep_copy=${OTTO_BACKUP_COPY_KEEP:-7}
copy_dir=${OTTO_BACKUP_COPY_DIR:-"$HOME/Library/Mobile Documents/com~apple~CloudDocs/OTTO Backups"}
# read, never sourced: the file is written by the web UI
icloud=$(sed -n 's/^BACKUP_ICLOUD=//p' .env.settings 2>/dev/null | tail -1 || true)

echo "== $(date '+%Y-%m-%d %H:%M:%S') nightly backup"
colima status >/dev/null 2>&1 || colima start
docker compose up -d --wait >/dev/null 2>&1

recipient=$(age-keygen -y .env.backup-key)
backup=$(bash scripts/backup.sh "$recipient" | sed -n 's/^Encrypted backup: //p')
[ -n "$backup" ] || { echo "Backup failed." >&2; exit 1; }
bash scripts/verify-backup.sh "$backup"

prune() {  # keep the newest $2 backups in folder $1
  ls -t "$1"/otto-*.tar.age 2>/dev/null | tail -n +"$(($2 + 1))" | while IFS= read -r old; do
    rm -f -- "$old" && echo "Removed old backup $old"
  done
}
prune backups "$keep_local"
if [ "$icloud" = on ]; then
  mkdir -p "$copy_dir"
  cp "$backup" "$copy_dir/"
  prune "$copy_dir" "$keep_copy"
  echo "Copied to $copy_dir"
fi
