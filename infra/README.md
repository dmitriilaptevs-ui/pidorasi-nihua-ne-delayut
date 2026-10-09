# Private deployment

The initial rollout is private. The intended request path is:

```text
browser → Sites Worker → API on the owner's computer
                         ├─ PostgreSQL
                         ├─ Redis
                         └─ loopback Hermes/Pi broker (when enabled)
```

The Worker is the public entry point and proxies API requests to the computer
running this compose stack. Keep the API published on loopback and establish
the upstream network path separately; do not expose the API or agent broker
directly to the internet. Configure an allowlist at the Worker during the
private rollout.

## Secrets and trust boundaries

Copy `infra/.env.example` to `infra/.env` and restrict its permissions to the
operator. Compose injects `OPENROUTER_API_KEY`,
`PROVIDER_KEY_ENCRYPTION_KEY`, `API_PROXY_TOKEN`, and agent runtime settings
into the **API container only**. They are runtime values, never frontend
environment variables or Docker build arguments. The API's OAuth credentials
remain in `apps/web/.env.local`; compose passes that file to the API only.
The web container gets the public origin and a build-time internal API URL,
with no provider credential.

`OPENROUTER_API_KEY` funds platform-issued keys. A user's own OpenRouter BYOK
is submitted through the account UI and encrypted at rest by the API with
`PROVIDER_KEY_ENCRYPTION_KEY`. The raw BYOK is not returned after saving.
Platform API keys are shown once when created; the database stores a digest
and prefix, and later listings show only the prefix. A lost key must be
revoked and replaced.

Set `API_PROXY_TOKEN` to a random secret and configure the Sites Worker to
send it as `X-Rubai-Proxy-Token` on `/api/*` and `/v1/*` requests. The API
rejects those requests when the token is configured and missing or incorrect.
Production mode rejects all API traffic when this token is not configured.
Store this value as a Worker secret; never put it in browser-visible code.

## Start the private origin

1. Create `infra/.env` from `.env.example`. Set database credentials,
   `PUBLIC_ORIGIN`, a strong `API_PROXY_TOKEN`, the platform OpenRouter key if
   platform-funded requests are enabled, and a URL-safe Base64 32-byte
   `PROVIDER_KEY_ENCRYPTION_KEY` before allowing users to save BYOK.
2. Put the OAuth provider values in `apps/web/.env.local`. Keep both env files
   untracked and readable only by the operator.
3. Start the stack:

   ```bash
   cd infra
   docker compose up -d --build
   docker compose ps
   curl -fsS http://127.0.0.1:8080/readyz
   curl -fsS http://127.0.0.1:3001/api/status
   ```

4. Configure the Sites Worker to proxy to the private origin and send the
   proxy token. Verify the allowlist, API authentication, cookie origin, and
   upstream reachability before inviting initial users.

Compose publishes only loopback ports (`127.0.0.1:8080` for API and
`127.0.0.1:3001` for web); PostgreSQL and Redis have no host ports. The web
image receives only `API_ORIGIN` at build time so its rewrites can reach the
API service inside Compose.

## This Windows computer

The current computer uses local PostgreSQL and Redis because Docker is not
installed. `infra/scripts/start-local.ps1` loads Windows DPAPI-protected secrets
from the ignored `data/runtime-secrets.json`, starts PostgreSQL, checks or starts
Redis, applies migrations, and launches the API and agent broker in hidden
windows. Run it from PowerShell 7. The API Python environment must have the
development requirements installed, including `pgserver`.

The ignored `data/runtime-config.json` contains `public_origin` (the Sites
address), `api_origin` (the HTTPS tunnel address), and `redis_directory` (the
installed Redis directory, relative to the repository). Redis reads
`data/redis/redis.conf`; it must bind to loopback. Keep these files and the
database backed up separately from Git. DPAPI-protected secrets can only be
decrypted by this Windows account; database backups also need the original
`PROVIDER_KEY_ENCRYPTION_KEY` to recover customer credentials.

The active HTTPS connection uses `localhost.run`:

```powershell
ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes -T -R 80:127.0.0.1:8080 nokey@localhost.run
```

Keep the tunnel process running and the computer awake. Its free URL can change
after reconnecting: update `api_origin` and the Sites `API_ORIGIN` environment
value, preserving the existing secret `API_PROXY_TOKEN`. The Windows Redis
community build is suitable for this private trial. Move the origin to a stable
host and supported Redis deployment before general customer availability.

## Hermes and Pi

Hermes and Pi are real text runtimes started by the isolated local broker in
`apps/agents`. The broker uses temporary homes and disables filesystem, shell,
browser, and other tools. Keep it bound to loopback. Configure
`AGENT_RUNTIME_URL` and `AGENT_RUNTIME_TOKEN` only after installing and
validating both runtimes; see [apps/agents/README.md](../apps/agents/README.md).

## Operations

```bash
docker compose logs -f api web
docker compose down                 # stop; retain database volume
docker compose down -v              # destructive: remove database volume
```

Use the backup and restore scripts in `infra/scripts/` before data-changing
maintenance. Keep dumps and credentials out of Git and the public site. The
private rollout does not establish production readiness, payment readiness,
or approval for public registration; those require separate acceptance and
operational review.
