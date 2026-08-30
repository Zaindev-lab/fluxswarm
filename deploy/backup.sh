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

# Optional age-encrypted off-site copy (D-2): when a recipient is configured, the
# tarball is re-encrypted and the plaintext is REMOVED, so the box never stores
# raw PII/credentials in the clear. Encrypt with your own keypair:
#   age-keygen -o backup.agekey      # keep this OFF the host
# then export FLUXSWARM_BACKUP_AGE_RECIPIENT=$(age-keygen -y backup.agekey)
# in the crontab/shell that runs backup.sh.
if [[ -n "${FLUXSWARM_BACKUP_AGE_RECIPIENT:-}" ]]; then
  if command -v age >/dev/null 2>&1; then
    age -r "$FLUXSWARM_BACKUP_AGE_RECIPIENT" -o "$BACKUP_DIR/$NAME.age" "$BACKUP_DIR/$NAME"
    rm -f "$BACKUP_DIR/$NAME"
    NAME="$NAME.age"
    echo "encrypted off-site copy: $BACKUP_DIR/$NAME"
  else
    echo "WARN: FLUXSWARM_BACKUP_AGE_RECIPIENT is set but 'age' is not installed — keeping plaintext." >&2
  fi
fi

# Rotate: keep the newest 7 of each pattern (nullglob-safe — no matches = no-op,
# so the FIRST backup on a fresh box succeeds where `ls` over an empty glob would
# return exit 2 and kill the script under set -euo pipefail).
shopt -s nullglob
for pat in 'fluxswarm-*.tar.gz' 'fluxswarm-*.tar.gz.age' 'kanban-*.tar.gz'; do
  files=("$BACKUP_DIR"/$pat)
  if ((${#files[@]} > 7)); then
    mapfile -t newest < <(printf '%s\n' "${files[@]}" | sort -rV)
    rm -f "${newest[@]:7}"
  fi
done
shopt -u nullglob

echo "backup: $BACKUP_DIR/$NAME ($(du -h "$BACKUP_DIR/$NAME" | cut -f1))"
echo "rotations kept: $(ls -1 "$BACKUP_DIR"/fluxswarm-*.tar.gz* 2>/dev/null | wc -l)"