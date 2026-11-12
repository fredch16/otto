"""Fred's settings, changed in the web UI and read by OTTO's scripts.

Stored as KEY=value lines in `.env.settings` at the project root (ignored by Git),
so shell scripts can read them too. Only the keys and values defined here are
ever written.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # src/otto_paperless_mcp -> project root


@dataclass(frozen=True)
class Toggle:
    key: str
    title: str
    description: str
    default: bool = False


TOGGLES = [
    Toggle("BACKUP_ICLOUD", "Copy nightly backups to iCloud Drive",
           "Keeps the newest 7 encrypted backups in iCloud Drive/OTTO Backups, so they survive the loss of "
           "this Mac. Only useful if .env.backup-key is also stored off this Mac."),
]


def path() -> Path:
    return Path(os.environ.get("OTTO_SETTINGS", ROOT / ".env.settings"))


def load() -> dict[str, bool]:
    values = {t.key: t.default for t in TOGGLES}
    try:
        lines = path().read_text().splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        key, _, value = line.partition("=")
        if key.strip() in values:
            values[key.strip()] = value.strip().lower() == "on"
    return values


def save(values: dict[str, bool]) -> None:
    known = {t.key for t in TOGGLES}
    lines = [f"{key}={'on' if values[key] else 'off'}" for key in sorted(known)]
    target = path()
    temporary = target.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("# Written by the OTTO web UI (Settings).\n" + "\n".join(lines) + "\n")
    temporary.replace(target)
