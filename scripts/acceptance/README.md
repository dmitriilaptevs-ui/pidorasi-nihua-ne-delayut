# Acceptance harnesses (black-box, live stack)

Scripts in this directory exercise the **deployed** compose stack over HTTP only.
They must never import `app.*`, write to the database, or touch container state —
read-only `docker logs`/`inspect` plus the single documented admin bootstrap exec.

The unit suites (`apps/api/tests`, `apps/web/tests`) remain the authority for
logic-level checks (token expiry windows, throttle internals, concurrency
primitives). These packs produce *live* evidence for the goal task contracts.

## Prerequisites

- Stack up and healthy: `cd infra && docker compose up -d --build`,
  `curl -s localhost:8080/readyz` → `{"database":true,"redis":true}`
- Console mail + development env in the api container
  (`MAIL_TRANSPORT=console`, `APP_ENV=development`) so verification/reset
  tokens can be read from the container log
- Docker access for the harness process (docker group or `sg docker`)
- `httpx` — easiest via the repo venv:
  `apps/api/.venv/bin/python scripts/acceptance/identity_live.py`

## identity_live.py

```bash
apps/api/.venv/bin/python scripts/acceptance/identity_live.py --throttle
# manual tokens (SMTP runs):
apps/api/.venv/bin/python scripts/acceptance/identity_live.py \
    --token-source manual --verify-token <…> --reset-token <…>
```

Covers, in order:

| Step | Assertion |
| --- | --- |
| `/healthz`, `/readyz` | 200 |
| `/api/auth/providers` | email_password=true (vk/yandex informational) |
| register | 201, verification_sent, email_verified=false |
| verify-email | 200, email_verified=true |
| verify-email replay | 400 `token_used` |
| login → me → logout → me | 200+cookie → 200 → 204 → 401 |
| password forgot/reset | 202; reset revokes sessions: old session 401, old password 401, new password 200 |
| admin bootstrap | `app.cli create-admin` via `docker exec` (idempotent) |
| admin RBAC | admin `/api/admin/whoami` 200 = email; normal user 403 `admin_required` |
| login throttle (`--throttle`) | 429 within limit+1 attempts |

Evidence JSON is written to `scripts/acceptance/evidence/` (tokens redacted to
length + sha256 prefix; passwords never recorded). Exit code is non-zero unless
every required step passed.

### Known blocker (2026-10-05): mail tokens absent from docker logs

`ConsoleMailer` logs at INFO, but uvicorn's default `LOGGING_CONFIG` declares no
root logger, so root stays at WARNING and mail records never reach
`docker logs rubai-api-1`. Tokens are stored one-way (`token_digest`), so with
console mail there is **no** other way to read them. Fix (owner of `apps/api`):
`logging.basicConfig(level=logging.INFO)` in `app/main.py` (or a `rubai`
logger with a StreamHandler / uvicorn `--log-config` with root=INFO). Until
then the pack records `verify-email` and `password reset` as **BLOCKED** and
exits non-zero. `--token-source manual` unblocks single runs without a rebuild.

### Operational notes

- Throttles are fixed-window: register 5/h per IP, forgot 3/h per IP, login
  10/15min per email+IP. Repeated harness runs can exhaust the register/forgot
  windows; space runs out or wait for the window to pass.
- The API sets a `Secure` cookie when `PUBLIC_ORIGIN` is https, so the harness
  sends the session cookie explicitly instead of relying on a cookie jar over
  http://127.0.0.1.
- Running this pack against a *public* origin additionally needs the request
  `Origin` header to equal `PUBLIC_ORIGIN`; the local 8080 path omits Origin on
  purpose (the API rejects a mismatching one). Read `PUBLIC_ORIGIN` from
  `infra/.env` rather than hardcoding it — free lhr.life subdomains rotate on
  reconnect.

## Planned harnesses (added when their APIs land)

- **ledger concurrency** — ≥100 parallel chat/reserve operations on one
  wallet/key, asserting no overspend, no lost reserve, no double charge, via
  HTTP + read-only ledger checks.
- **gateway SDK smoke** — standard `openai` SDK against the deployed base URL
  for a free and a paid model, with a ledger cross-check of the recorded usage.
- **payments replay** — YooKassa sandbox webhook duplicates/replays/forgeries
  and wrong amount/currency, asserting exactly one credit and refund records.
