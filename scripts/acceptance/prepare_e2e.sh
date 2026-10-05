#!/usr/bin/env bash
# Prepare Playwright storage state for the acceptance user and admin without
# touching the login endpoint (which is throttled per email/IP).
#
#   scripts/acceptance/prepare_e2e.sh
#
# Requires: docker compose stack running, infra/.env with PUBLIC_ORIGIN.
set -euo pipefail
cd "$(dirname "$0")/../.."

PUBLIC_ORIGIN="$(grep '^PUBLIC_ORIGIN=' infra/.env | cut -d= -f2-)"
HOST="$(printf '%s' "$PUBLIC_ORIGIN" | sed -E 's#^https?://##')"
DOMAIN="$(printf '%s' "$HOST" | cut -d: -f1)"
SECURE=True
case "$PUBLIC_ORIGIN" in
  http://*) SECURE=False ;;
esac
mkdir -p apps/web/e2e/.auth

make_state() {
  local email="$1" out="$2"
  local token
  token="$(sg docker -c "docker compose -f infra/docker-compose.yml exec -T -e E2E_EMAIL='$email' api python -c \"
import asyncio, os
from app.db import session_factory
from app.services import identity as identity_service
async def main():
    async with session_factory()() as db:
        user = await identity_service.get_user_by_email(db, os.environ['E2E_EMAIL'])
        if user is None or user.email_verified_at is None:
            raise SystemExit('user missing or unverified: ' + os.environ['E2E_EMAIL'])
        _, raw = await identity_service.create_session(db, user=user, method='password')
        await db.commit()
        print(raw)
asyncio.run(main())\"" | tr -d '\r\n')"
  if [ -z "$token" ]; then
    echo "failed to create a session for $email" >&2
    exit 1
  fi
  python3 - "$DOMAIN" "$SECURE" "$token" "$out" <<'PY'
import json, sys, time
host, secure, token, out = sys.argv[1], sys.argv[2] == "True", sys.argv[3], sys.argv[4]
state = {
    "cookies": [
        {
            "name": "rb_platform_session",
            "value": token,
            "domain": host,
            "path": "/",
            "expires": int(time.time()) + 3600,
            "httpOnly": True,
            "secure": secure,
            "sameSite": "Lax",
        }
    ],
    "origins": [],
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2)
print(f"wrote {out}")
PY
  chmod 600 "$out"
}

make_state "e2e-user@example.com" "apps/web/e2e/.auth/user.json"
make_state "admin@example.com" "apps/web/e2e/.auth/admin.json"
