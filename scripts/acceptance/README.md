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

## keys_catalog_live.py

Covers the keys-catalog contract over HTTP plus read-only DB / separate-process
witnesses:

| Step | Assertion |
| --- | --- |
| verified account (register→verify→login) | session established |
| unverified account | `POST /api/keys` → 403 `email_not_verified` |
| key creation | 201, raw `sk-rubai-…` returned once, `item.prefix = raw[:16]`, no digest in the payload |
| key list | prefix only; raw key and `key_hash` absent from the response |
| DB witness | `key_hash == sha256(raw)`, prefix matches, raw key absent from the row |
| pre-revoke auth (separate process) | `authenticate_api_key` → true |
| ownership | a second user revoking the key → 404 `key_not_found` |
| revoke | 200; the very next separate-process auth → false; propagation (raw elapsed minus process-startup baseline) < 5 s; `revoked_at` set |
| public catalog | 200 anonymously, 465 items, every available model priced, non-negative, 6-decimal strings, parseable version/valid_from |
| sync RBAC | anonymous 401, non-admin 403 `admin_required` |
| sync consistency | `created+repriced+unchanged == seen` |
| sync idempotency | second run: `created=0, repriced=0, unavailable=0, unchanged=seen` |
| history | no active version/price/valid_from drifts on an idempotent sync; DB: no duplicate active versions, retired rows preserved |
| decimal math (independent) | every DB row recomputed with stdlib `Decimal` half-up matches exactly |

Run against the API directly, or through the web proxy (the production path):

```bash
PUB=$(grep -E '^PUBLIC_ORIGIN=' infra/.env | cut -d= -f2-)
apps/api/.venv/bin/python scripts/acceptance/keys_catalog_live.py \
    --base-url http://127.0.0.1:3001 --origin "$PUB"
```

`/healthz` and `/readyz` are not proxied, so health probes always go to
`--health-base-url` (default `http://127.0.0.1:8080`). Register throttling is
per client IP, so alternating direct/proxied runs use separate windows.

### Known finding (2026-10-05): sentinel `-1` prices reach the catalog

Seven auto-routing models (`openrouter/auto`, `openrouter/auto-beta`,
`openrouter/bodybuilder`, `openrouter/fusion`, `openrouter/pareto-code`,
`nvidia/switchyard`, `typesafe/jev-router`) carry `pricing.prompt = "-1"` in
OpenRouter's feed. `usd_per_mtok` does not reject negatives, so the catalog
publishes `-120000000.000000` RUB/Mtok (input and output) for them. The pack
fails until the provider sentinel is rejected (or mapped to no pricing /
unavailable); the gateway must not be able to turn this into a negative
charge.

## ledger_concurrency_live.py

100 concurrent `/v1/chat/completions` requests on one wallet/key, with
read-only DB witnesses and an in-container poller sampling every 2 ms:

| Step | Assertion |
| --- | --- |
| 5-kopeck wallet | every response is a platform 402 `insufficient_funds` (no reserve) or a funded request (exactly one reserve, released/settled exactly once); balance 5 and reserved 0 afterwards |
| overspend witness | the poller observes the maximum concurrently *held* reserve and `reserved_kopecks`; both must stay within the wallet balance (observed max: exactly 5 held on a 5-kopeck wallet under 100 concurrent requests) |
| funded wallet | 100 funded requests, 100 reserves, each released/settled exactly once; balance and reserved projections consistent; peak held observed |
| global invariants | no negative reserved projection; every wallet balance equals the sum of its postings; reserve references unique; no open reconciliation items from clean failures |
| provider state probe | records the live upstream result; an absent or rejected provider key is reported as BLOCKED, not as a concurrency failure |
| settle/double-charge | blocked while the provider key is rejected/absent (needs a successful upstream call) |

```bash
apps/api/.venv/bin/python scripts/acceptance/ledger_concurrency_live.py
```

The pack is mode-agnostic: upstream 200 responses count as settled, upstream
errors (including credential errors) count as released, and both are checked
against the exact-once lifecycle and balance math.

### Finding 2026-10-05 18:07Z: provider key expired

The live probe returns upstream `HTTP 401 {"error":{"message":"API key expired.",
"code":401,"metadata":{"headers":{"WWW-Authenticate":"Bearer
invalid_token, error_description=\"API key expired\""}}}}`. Consequence: no
free/paid live model call can succeed, so the settle leg and the product
journey (criterion 3) stay blocked until the owner supplies a fresh OpenRouter
key. Reserve behavior on upstream errors is correct: every reserve released,
wallet balance unchanged, peak held never above the balance.

Result on the gateway image 2026-10-05 18:07Z: **7/9 passed, 2 blocked**.
