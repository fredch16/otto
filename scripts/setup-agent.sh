#!/usr/bin/env bash
# Create OTTO's Paperless users (read-only otto-agent, and otto-editor for
# otto-tidy) and store their API tokens in the private, ignored .env.agent and
# .env.editor files. Safe to run repeatedly.
# The OTTO_* variables below target another stack (see scripts/test-env.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
read -ra compose <<< "${OTTO_COMPOSE:-docker compose}"
env_file=${OTTO_ENV:-.env}
agent_env=${OTTO_AGENT_ENV:-.env.agent}
editor_env=${OTTO_EDITOR_ENV:-.env.editor}

output=$("${compose[@]}" exec -T -u paperless webserver python3 manage.py shell < scripts/agent_user.py)
token=$(printf '%s\n' "$output" | sed -n 's/^OTTO_AGENT_TOKEN=//p')
editor_token=$(printf '%s\n' "$output" | sed -n 's/^OTTO_EDITOR_TOKEN=//p')
if [ -z "$token" ] || [ -z "$editor_token" ]; then
  echo 'Could not create the otto-agent and otto-editor tokens.' >&2
  exit 1
fi

# Search permissions are stored in the index; refresh it so newly granted
# documents become searchable for otto-agent.
granted=$(printf '%s\n' "$output" | sed -n 's/^OTTO_AGENT_GRANTED=//p')
if [ "${granted:-0}" -gt 0 ]; then
  echo "Granted otto-agent access to $granted existing document(s); refreshing search index."
  "${compose[@]}" exec -T -u paperless webserver python3 manage.py document_index reindex > /dev/null
fi

db_password=$(sed -n 's/^OTTO_DB_PASSWORD=//p' "$env_file")
if [ -z "$db_password" ]; then
  echo 'OTTO_DB_PASSWORD missing; run python3 scripts/init.py first.' >&2
  exit 1
fi

umask 077
printf 'PAPERLESS_URL=http://localhost:%s\nPAPERLESS_TOKEN=%s\nOTTO_DATABASE_URL=postgresql://otto:%s@127.0.0.1:%s/otto\n' \
  "${OTTO_PAPERLESS_PORT:-8000}" "$token" "$db_password" "${OTTO_DB_PORT:-5433}" > "$agent_env"
printf 'PAPERLESS_EDITOR_TOKEN=%s\n' "$editor_token" > "$editor_env"
echo "otto-agent and otto-editor are ready; tokens stored in $agent_env and $editor_env."
