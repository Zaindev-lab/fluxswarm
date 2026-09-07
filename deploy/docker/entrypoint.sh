#!/usr/bin/env bash
# FluxSwarm self-contained container entrypoint.
# - Binds the FastAPI app to 0.0.0.0:$PORT (PORT is platform-provided).
# - Creates/ensures the persistent runtime dirs (SQLite data + Hermes runtime).
# - Launches uvicorn with the base image's Python (FluxSwarm toolchain).
set -eu

PORT="${PORT:-8787}"
DATA_DIR="${DATA_DIR:-/app/backend/data}"

if [ -z "${HERMES_HOME:-}" ] || [ "${HERMES_HOME}" = "/" ]; then
  echo "FATAL: HERMES_HOME is unset or points to /" >&2
  exit 1
fi

# Ensure FluxSwarm persistent data dir exists (users.db, .jwt_secret live here
# and are provided by a volume; created lazily in demo mode).
mkdir -p "$DATA_DIR"

# Clean stale server lock from a previous container instance (single-process
# container — a leftover lock from a stopped container would block restart).
rm -f "$DATA_DIR/.server.lock"

# Ensure Hermes runtime dirs that must exist before first `hermes kanban` call.
mkdir -p "$HERMES_HOME/kanban/boards" \
         "$HERMES_HOME/logs" \
         "$HERMES_HOME/memories" \
         "$HERMES_HOME/state" \
         "$HERMES_HOME/pairing"

echo "FluxSwarm starting on 0.0.0.0:${PORT} (HERMES_HOME=${HERMES_HOME} FLUXSWARM_DEMO_MODE=${FLUXSWARM_DEMO_MODE:-0})"

exec python -m uvicorn main:app --host 0.0.0.0 --port "$PORT" --log-level info --timeout-graceful-shutdown 30
