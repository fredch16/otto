#!/usr/bin/env bash
# Disposable Paperless + OTTO stack (http://localhost:8001) with synthetic
# documents, for experiments that must not touch the real archive.
#   up    start it and create the OTTO users (.env.agent-test, .env.editor-test)
#   seed  add synthetic documents and wait until Paperless has processed them
#   down  delete the stack, its volumes and its secrets
set -euo pipefail
cd "$(dirname "$0")/.."
export OTTO_COMPOSE="docker compose -p otto-test --env-file .env.test -f compose.yaml -f compose.test.yaml"
export OTTO_ENV=.env.test OTTO_AGENT_ENV=.env.agent-test OTTO_EDITOR_ENV=.env.editor-test OTTO_PAPERLESS_PORT=8001 OTTO_DB_PORT=5434
read -ra compose <<< "$OTTO_COMPOSE"

case "${1:-}" in
  up)
    mkdir -p -m 700 test/consume test/export
    python3 scripts/init.py .env.test
    "${compose[@]}" up -d --wait --wait-timeout 600
    bash scripts/setup-agent.sh
    echo "Test stack ready at http://localhost:8001. Index it with:"
    echo "  uv run --project mcp/paperless --env-file .env.agent-test otto-index"
    ;;
  seed)
    uv run --project mcp/paperless python scripts/seed_test_docs.py test/consume
    echo "Waiting for Paperless to consume the documents..."
    for _ in $(seq 1 120); do  # a freshly started Paperless can take minutes
      [ -z "$(ls test/consume)" ] && break
      sleep 5
    done
    ls test/consume
    # Documents from the consume folder have no owner, so otto-agent sees them;
    # refresh the agent's search index anyway (shared documents need it).
    bash scripts/setup-agent.sh
    ;;
  down)
    "${compose[@]}" down -v
    rm -rf test .env.test .env.agent-test .env.editor-test
    ;;
  *)
    echo "usage: $0 up|seed|down" >&2
    exit 2
    ;;
esac
