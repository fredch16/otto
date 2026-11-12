#!/usr/bin/env bash
# Run OTTO's background jobs with launchd (macOS), for the logged-in user:
# - Colima (Docker) at login, so Paperless and OTTO's database are always up
#   (the containers restart on their own);
# - the web UI at login, restarted if it stops (http://127.0.0.1:8100);
# - the encrypted, verified backup every night at 03:30 (run on wake if the Mac
#   was asleep).
# Logs: ~/Library/Logs/otto/. Safe to run repeatedly; `--remove` uninstalls.
set -euo pipefail
cd "$(dirname "$0")/.."
root=$(pwd)
agents="$HOME/Library/LaunchAgents"
logs="$HOME/Library/Logs/otto"
domain="gui/$(id -u)"
uv=$(command -v uv)

unload() { launchctl bootout "$domain/$1" 2>/dev/null || true; rm -f "$agents/$1.plist"; }

if [ "${1:-}" = "--remove" ]; then
  unload local.otto.ui
  unload local.otto.backup
  echo "Removed OTTO's launchd jobs (Colima stays: brew services stop colima)."
  exit 0
fi

[ -f .env.backup-key ] || { echo "Missing .env.backup-key (age identity for backups)." >&2; exit 1; }
mkdir -p "$agents" "$logs"
brew services start colima >/dev/null

write() {  # label, extra plist keys, program arguments...
  local label=$1 extra=$2; shift 2
  local args=""
  for arg in "$@"; do args+="    <string>$arg</string>"$'\n'; done
  unload "$label"
  cat > "$agents/$label.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>WorkingDirectory</key><string>$root</string>
  <key>ProgramArguments</key>
  <array>
$args  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StandardOutPath</key><string>$logs/${label#local.otto.}.log</string>
  <key>StandardErrorPath</key><string>$logs/${label#local.otto.}.log</string>
$extra
</dict>
</plist>
EOF
  launchctl bootstrap "$domain" "$agents/$label.plist"
}

write local.otto.ui "  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>" \
  "$uv" run --quiet --project mcp/paperless --env-file .env.agent --env-file .env.editor otto-ui

write local.otto.backup "  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>30</integer></dict>" \
  /bin/bash "$root/scripts/backup-nightly.sh"

echo "Installed: Colima at login, web UI at http://127.0.0.1:8100, nightly backup at 03:30."
echo "Logs in $logs. Run a backup now: launchctl kickstart $domain/local.otto.backup"
