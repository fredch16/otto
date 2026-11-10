#!/usr/bin/env bash
# Run from a terminal: age asks for an encryption passphrase.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v age >/dev/null
docker compose ps --status running --services | grep -qx webserver
umask 077
stamp=$(date -u +%Y%m%dT%H%M%SZ)
stage="export/backup-${stamp}"
mkdir "$stage"
# Stop consumers and the UI so nothing changes during the export.
docker compose stop webserver
trap 'docker compose start webserver >/dev/null' EXIT
# Bypass the normal entrypoint: it also starts consumers and workers, which
# would allow writes during the export even though the main service is stopped.
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" \
  --entrypoint /usr/local/bin/python webserver manage.py document_exporter \
  "/usr/src/paperless/$stage"
cp .env compose.yaml "$stage/"
if [ "$#" -gt 0 ]; then
  tar -C export -cf - "backup-${stamp}" | age -r "$1" -o "backups/otto-${stamp}.tar.age"
else
  tar -C export -cf - "backup-${stamp}" | age -p -o "backups/otto-${stamp}.tar.age"
fi
# Only discard this script's plaintext staging directory after encryption succeeds.
rm -rf -- "$stage"
printf 'Encrypted backup: backups/otto-%s.tar.age\n' "$stamp"
