#!/usr/bin/env python3
"""Create local secrets once, without displaying or overwriting them.

Safe to re-run: existing values are kept and only missing keys are added.
"""
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent.parent
for name in ('consume', 'export', 'backups'):
    (root / name).mkdir(mode=0o700, exist_ok=True)

wanted = {
    'POSTGRES_PASSWORD': lambda: secrets.token_hex(32),
    'PAPERLESS_SECRET_KEY': lambda: secrets.token_hex(64),
    'USERMAP_UID': lambda: str(os.getuid()),
    'USERMAP_GID': lambda: str(os.getgid()),
    'OTTO_DB_PASSWORD': lambda: secrets.token_hex(32),
}
path = root / '.env'
existing = set()
if path.exists():
    existing = {line.split('=', 1)[0] for line in path.read_text().splitlines() if '=' in line}
missing = [key for key in wanted if key not in existing]

fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
with os.fdopen(fd, 'a') as stream:
    for key in missing:
        stream.write(f'{key}={wanted[key]()}\n')
os.chmod(path, 0o600)

if not existing:
    print('Created private .env. Next: docker compose up -d --wait')
elif missing:
    print(f'Added {", ".join(missing)} to .env; existing secrets unchanged.')
else:
    print('Existing .env retained; no secrets changed.')
