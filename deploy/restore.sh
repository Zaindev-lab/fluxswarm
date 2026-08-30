#!/usr/bin/env bash
# FluxSwarm — restore a backup tarball. Stops the app, restores files, restarts.
#   sudo bash deploy/restore.sh backups/fluxswarm-20260829-020411.tar.gz
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP="${1:?usage: restore.sh <backup.tar.gz[.age]>}"
[[ -f "$BACKUP" ]] || { echo "missing: $BACKUP"; exit 1; }

# Encrypted backup: decrypt first with the age identity key.
if [[ "$BACKUP" == *.age ]]; then
  IDENTITY="${FLUXSWARM_BACKUP_AGE_IDENTITY:?set FLUXSWARM_BACKUP_AGE_IDENTITY to restore a .age backup}"
  command -v age >/dev/null 2>&1 || { echo "age not installed"; exit 1; }
  DEC="$(mktemp --suffix=.tar.gz)"
  age -d -i "$IDENTITY" -o "$DEC" "$BACKUP" || { rm -f "$DEC"; exit 1; }
  trap 'rm -f "$DEC"' EXIT
  BACKUP="$DEC"
fi

cd "$REPO_DIR"
if command -v docker >/dev/null 2>&1; then
  docker compose -f docker-compose.yml down || true
fi
tar xzf "$BACKUP" -C "$REPO_DIR"  # backend/data + .env
echo "restored $BACKUP"
echo "restart: docker compose -f docker-compose.yml --env-file .env up -d"