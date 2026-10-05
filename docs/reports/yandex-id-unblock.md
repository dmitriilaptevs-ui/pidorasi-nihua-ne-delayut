# Yandex ID unblock kit — owner steps and adapter test plan

**Status:** documentation only; no source changes. The integration itself belongs
to the orchestrator's identity task. The task contract explicitly pauses for
Yandex ID credentials.

**Date:** 2026-10-05. Live state: `GET /api/status` and
`GET /api/auth/providers` both report `yandex:false`; YK/Yandex login has no
served endpoint at the moment (see §2).

## 1. What the adapter expects (from the existing code)

Source of truth (read-only inspection):

- `apps/web/src/lib/server/auth.ts`
  - authorize: `https://oauth.yandex.ru/authorize` with
    `response_type=code`, `client_id`, `redirect_uri`,
    `state`, `code_challenge`, `code_challenge_method=S256`, `scope=login:info`
  - redirect URI: `` `${cfg.origin}/api/auth/callback/yandex` ``
  - token: `POST https://oauth.yandex.ru/token`, form =
    `grant_type=authorization_code`, `code`, `client_id`, `code_verifier`,
    plus `client_secret` only when configured
  - userinfo: `GET https://login.yandex.ru/info?format=json`,
    header `Authorization: OAuth <access_token>`; the adapter rejects the
    response unless `client_id` equals ours and `id` is a numeric string
    (stable account id — never the mutable login)
- `apps/web/src/lib/server/config.ts` — env names `YANDEX_CLIENT_ID`,
  `YANDEX_CLIENT_SECRET`; `publicAuthStatus()` reports `yandex` as ready when
  `SESSION_SECRET` is valid (43+ base64url chars) and `clientId` is non-empty.
  Unlike VK, Yandex is **not** restricted to default localhost ports, so it can
  run on the public https origin.
- `apps/web/.env.example` documents the dev expectations (scope `login:info`,
  redirect `http://localhost/api/auth/callback/yandex`).

## 2. Current gap beyond credentials

In the current working tree the web BFF OAuth routes
`apps/web/src/app/api/auth/start/route.ts` and
`apps/web/src/app/api/auth/callback/[provider]/route.ts` are deleted, and
`apps/api` has no OAuth endpoints (only email+password). So Yandex/VK cannot go
live until the OAuth port to FastAPI lands. Credentials alone are necessary but
not sufficient. The test plan below is written to be applicable to whichever
side hosts the flow, but the redirect URI path must stay exactly
`/api/auth/callback/yandex` unless the orchestrator changes it deliberately
(any change must also be reflected in the Yandex app settings).

## 3. Owner steps (exact)

1. Open <https://oauth.yandex.ru/client/new> (Yandex ID → "Создать приложение"),
   platform **Web services**.
2. Redirect URIs — Yandex requires an exact match, and multiple URIs are
   allowed. Register both:
   - `https://<PUBLIC_ORIGIN>/api/auth/callback/yandex`
     (`PUBLIC_ORIGIN` is in `infra/.env`; it rotates with the free tunnel — see
     the RU-access matrix for the stable-hostname problem)
   - `http://localhost/api/auth/callback/yandex` (dev copy behind the loopback
     :80 proxy used for VK ID)
3. Scopes: `login:info` only. `login:email` is **not** needed by the adapter
   (it uses name only, no email); add it later only if the product actually
   wants the verified Yandex email.
4. Copy **ClientID** and **Client secret** into `apps/web/.env.local`
   (mode 0600, never committed):
   `YANDEX_CLIENT_ID=…`, `YANDEX_CLIENT_SECRET=…`.
   The compose `web` service already loads that file via `env_file`.
5. Once the OAuth endpoints are deployed: restart `web` (orchestrator action)
   and check `GET /api/status` → `providers.yandex: true`. If it stays false,
   the first suspects are `SESSION_SECRET` validity and `APP_ORIGIN`
   parseability, not the Yandex credentials.
6. Re-register the redirect URI whenever the tunnel hostname changes (free
   lhr.life subdomains rotate), or move to a stable hostname first.

## 4. Adapter test plan (for whoever implements/ports the flow)

Unit level (extend `apps/web/tests/auth.test.ts` or the API equivalent):

- authorize URL contains exactly `scope=login:info`, the configured
  `redirect_uri`, a fresh `state` and PKCE `code_challenge` (S256).
- token exchange sends `code_verifier`; sends `client_secret` only when
  configured; maps provider HTTP errors to `AuthFlowError("provider")`.
- userinfo validation: mismatched `client_id` → `invalid_response`; non-numeric
  `id` → `invalid_response`; missing `access_token` → `invalid_response`.
- handshake negatives: unknown/replayed `state`, wrong browser-binding cookie,
  expired handshake.
- secret hygiene: `YANDEX_CLIENT_SECRET` never appears in client bundles or
  logs (grep the built output in the frontend task).

Live checklist (manual browser, both origins):

1. `/` → Yandex button enabled → consent screen → callback sets a session;
   display name rendered.
2. Replay the callback URL after completion → rejected (single-use handshake).
3. A redirect URI not registered in the Yandex app → provider error page
   (expected, proves exact-match registration matters).
4. `/api/status` shows `yandex:true`; pressing the button twice does not create
   two accounts for the same Yandex id.

## 5. Parallelization

Per the identity contract the task pauses only for the Yandex credentials. The
VK port, email/password hardening, keys-catalog and ledger work are unaffected
and can proceed while the owner creates the app and supplies ClientID/secret.
