#!/usr/bin/env bash
# FluxSwarm — nightly backup of the SQLite DB, BYOK keystore and audit log,
# plus the Hermes kanban named volume. Keeps 7 rotations on the box; push the
# tarball off-site with your own tooling.
#
#   sudo bash deploy/backup.sh                # one backup now
#   crontab -e →  0 3 * * * sudo bash /opt/fluxswarm/deploy/backup.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${FLUXSWARM_BACKUP_DIR:-$REPO_DIR/backups}"
DATA_DIR="$REPO_DIR/backend/data"
STAMP="$(date +%Y%m%d-%H%M%S)"
NAME="fluxswarm-$STAMP.tar.gz"

mkdir -p "$BACKUP_DIR"
cd "$REPO_DIR"

# Hermes kanban (docker named volume) if the app container is running.
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker run --rm -v fluxswarm-kanban:/data -v "$BACKUP_DIR":/out alpine \
    tar czf "/out/kanban-$STAMP.tar.gz" -C /data . 2>/dev/null || true
fi

tar czf "$BACKUP_DIR/$NAME" \
  -C "$REPO_DIR" backend/data .env 2>/dev/null || true

# Rotate: keep the newest 7 of each pattern.
ls -1t "$BACKUP_DIR"/fluxswarm-*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm
ls -1t "$BACKUP_DIR"/kanban-*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm

echo "backup: $BACKUP_DIR/$NAME ($(du -h "$BACKUP_DIR/$NAME" | cut -f1))"
echo "rotations kept: $(ls -1 "$BACKUP_DIR"/fluxswarm-*.tar.gz 2>/dev/null | wc -l)"