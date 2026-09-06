#!/usr/bin/env bash
# One-shot provisioning for the FluxSwarm dev container (Linux parity).
set -euo pipefail

cd /workspace/backend

pip install -r requirements.txt pytest >/dev/null

echo "== Running the backend test suite (SQLite layer + live PostgreSQL) =="
python -m pytest -q

echo
echo "FluxSwarm dev container ready."
echo "  * Start the server:  bash backend/run.sh   (or run.ps1 on Windows)"
echo "  * Endpoints:          http://127.0.0.1:8787"