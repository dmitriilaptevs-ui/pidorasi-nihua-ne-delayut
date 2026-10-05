#!/usr/bin/env bash
# Consistent PostgreSQL backup for the compose stack.
# Usage: infra/scripts/backup.sh   (BACKUP_DIR=... KEEP=10 override the defaults)
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-$HOME/rubai-backups}"
KEEP="${KEEP:-10}"
STAMP="$(date +%Y%m%dT%H%M%S)"
umask 077
mkdir -p "$BACKUP_DIR"

set -a
# shellcheck disable=SC1091
. ./.env
set +a

docker compose exec -T db pg_dump \
  -U "${POSTGRES_USER:-rubai}" \
  -d "${POSTGRES_DB:-rubai}" \
  --format=custom > "$BACKUP_DIR/rubai-$STAMP.dump"

sha256sum "$BACKUP_DIR/rubai-$STAMP.dump" > "$BACKUP_DIR/rubai-$STAMP.dump.sha256"

# Keep only the newest $KEEP dump/sha pairs.
ls -1t "$BACKUP_DIR"/rubai-*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old" "$old.sha256"
done

echo "backup written: $BACKUP_DIR/rubai-$STAMP.dump"
