#!/usr/bin/env bash
# FluxSwarm backend supervisor (Linux / Docker).
# Starts uvicorn and restarts it if /health fails. Single-instance lock holds
# inside the app (serverlock), so duplicates refuse to start.
set -u

PORT="${PORT:-8787}"
PY="${PY:-python3}"
HEALTH="http://127.0.0.1:${PORT}/health"
LOG="fluxswarm.log"; ERR="fluxswarm_err.log"

healthy() { curl -fsS --max-time 3 "$HEALTH" >/dev/null 2>&1; }

dead=0
while true; do
  if ! healthy; then
    if [ "$dead" -ge 3 ]; then pkill -f "uvicorn main:app.*$PORT" 2>/dev/null; sleep 2; fi
    nohup "$PY" -m uvicorn main:app --host 127.0.0.1 --port "$PORT" >>"$LOG" 2>>"$ERR" &
    dead=0
    echo "Started FluxSwarm on :$PORT"
  fi
  sleep 15
  if healthy; then dead=0; else dead=$((dead+1)); fi
done