#!/usr/bin/env bash
# FluxSwarm — restore a backup tarball. Stops the app, restores files, restarts.
#   sudo bash deploy/restore.sh backups/fluxswarm-20260829-020411.tar.gz
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP="${1:?usage: restore.sh <backup.tar.gz>}"
[[ -f "$BACKUP" ]] || { echo "missing: $BACKUP"; exit 1; }

cd "$REPO_DIR"
if command -v docker >/dev/null 2>&1; then
  docker compose -f docker-compose.yml down || true
fi
tar xzf "$BACKUP" -C "$REPO_DIR"  # backend/data + .env
echo "restored $BACKUP"
echo "restart: docker compose -f docker-compose.yml --env-file .env up -d"