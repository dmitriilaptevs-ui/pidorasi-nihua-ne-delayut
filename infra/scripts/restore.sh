#!/usr/bin/env bash
# Restore a custom-format pg_dump into the compose database.
# Destructive: requires explicit confirmation.
#
#   CONFIRM_RESTORE=yes infra/scripts/restore.sh ~/rubai-backups/rubai-....dump
set -euo pipefail
cd "$(dirname "$0")/.."

DUMP="${1:-}"
if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
  echo "usage: $0 <path to .dump>" >&2
  exit 2
fi

if [ "${CONFIRM_RESTORE:-}" != "yes" ]; then
  echo "Refusing to overwrite the current database." >&2
  echo "Re-run as: CONFIRM_RESTORE=yes $0 $DUMP" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a

docker compose exec -T db pg_restore \
  -U "${POSTGRES_USER:-rubai}" \
  -d "${POSTGRES_DB:-rubai}" \
  --clean --if-exists --no-owner < "$DUMP"

echo "restore finished from $DUMP"
