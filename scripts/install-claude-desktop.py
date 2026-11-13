"""Add OTTO's MCP servers to the Claude desktop app (or remove them with --remove).

Keeps everything else in the app's config. Quit and reopen Claude afterwards.
Usage: python3 scripts/install-claude-desktop.py [--remove]
"""
import json
import shutil
import sys
from pathlib import Path

CONFIG = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
ROOT = Path(__file__).resolve().parents[1]
SERVERS = {
    "otto-paperless": ("paperless", [".env.agent", ".env.models"], "otto-paperless-mcp"),
    "otto-mail": ("mail", [".env.agent", ".env.mail"], "otto-mail-mcp"),
}


def server(project: str, env_files: list[str], script: str) -> dict:
    # Claude starts servers outside this folder with a minimal PATH: use absolute paths
    args = ["run", "--quiet", "--project", str(ROOT / "mcp" / project)]
    for name in env_files:
        args += ["--env-file", str(ROOT / name)]
    return {"command": shutil.which("uv") or "/opt/homebrew/bin/uv", "args": args + [script]}


config = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
servers = config.setdefault("mcpServers", {})
for name, spec in SERVERS.items():
    if "--remove" in sys.argv:
        servers.pop(name, None)
    else:
        servers[name] = server(*spec)
CONFIG.parent.mkdir(parents=True, exist_ok=True)
CONFIG.write_text(json.dumps(config, indent=2) + "\n")
print(("Removed" if "--remove" in sys.argv else "Added") + f" {', '.join(SERVERS)} in {CONFIG}. Quit and reopen Claude.")
