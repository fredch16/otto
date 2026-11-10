#!/usr/bin/env python3
"""Create local secrets once, without displaying or overwriting them."""
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent.parent
for name in ('consume', 'export', 'backups'):
    (root / name).mkdir(mode=0o700, exist_ok=True)
try:
    fd = os.open(root / '.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print('Existing .env retained; no secrets changed.')
else:
    with os.fdopen(fd, 'w') as stream:
        stream.write(
            f'POSTGRES_PASSWORD={secrets.token_hex(32)}\n'
            f'PAPERLESS_SECRET_KEY={secrets.token_hex(64)}\n'
            f'USERMAP_UID={os.getuid()}\nUSERMAP_GID={os.getgid()}\n'
        )
    print('Created private .env. Next: docker compose up -d --wait')
