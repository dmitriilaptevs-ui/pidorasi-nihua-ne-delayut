import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { VARIANTS, type AuthProvider, type Variant, type Viewer } from "../contracts";
import { config, validSecret, type ServerConfig } from "./config";

/**
 * Local single-process OAuth + session store for the onboarding lab.
 *
 * This module is deliberately process-local: handshakes and sessions live in
 * bounded `globalThis` maps and are lost on restart. Nothing here writes a user
 * database, and no token, secret or provider identity is ever logged.
 */

/** How long a started OAuth handshake may wait for the provider callback. */
export const HANDSHAKE_TTL_MS = 10 * 60 * 1000;
/** Local session lifetime (opaque cookie + in-memory record). */
export const SESSION_TTL_MS = 60 * 60 * 1000;
const MAX_HANDSHAKES = 2_000;
const MAX_SESSIONS = 5_000;
const MAX_BODY_BYTES = 8 * 1024;
const MAX_PROVIDER_BODY_BYTES = 64 * 1024;
const FETCH_TIMEOUT_MS = 8_000;
const THROTTLE_WINDOW_MS = 60_000;
const THROTTLE_MAX_REQUESTS = 30;
const MAX_THROTTLE_KEYS = 1_000;

export const SESSION_COOKIE = "rb_session";
export const BIND_COOKIE = "rb_oauth_bind";

/**
 * Every server-issued opaque value (state, browser binding, session id) is a
 * 32-byte random value encoded as exactly 43 base64url characters. Values that
 * do not match this shape are rejected before any hashing or map lookup.
 */
const OPAQUE_TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;

export function isOpaqueToken(value: unknown): value is string {
  return typeof value === "string" && OPAQUE_TOKEN_RE.test(value);
}

export interface Session {
  /** Stable per-user identity for the local limiter (HMAC derived, never raw id). */
  subject: string;
  viewer: Viewer;
  provider: AuthProvider;
  /** Epoch ms at which this session stops being valid. */
  expiresAt: number;
}

interface StoredSession extends Session {
  key: string;
  /** Present only for VK, only server-side, never serialized to the browser. */
  vkAccessToken?: string;
}

interface Handshake {
  stateHash: string;
  provider: AuthProvider;
  variant: Variant;
  verifier: string;
  bindHash: string;
  createdAt: number;
}

interface RateBucket {
  count: number;
  resetAt: number;
}

interface AuthStore {
  handshakes: Map<string, Handshake>;
  sessions: Map<string, StoredSession>;
  rate: Map<string, RateBucket>;
}

const STORE_SYMBOL = Symbol.for("rubai.local-auth.store");

function store(): AuthStore {
  const global = globalThis as unknown as { [STORE_SYMBOL]?: AuthStore };
  if (!global[STORE_SYMBOL]) {
    global[STORE_SYMBOL] = {
      handshakes: new Map(),
      sessions: new Map(),
      rate: new Map(),
    };
  }
  return global[STORE_SYMBOL];
}

/** Test-only escape hatch. Not exposed through any HTTP route. */
export function __resetAuthStateForTests(): void {
  store().handshakes.clear();
  store().sessions.clear();
  store().rate.clear();
}

// ---------------------------------------------------------------------------
// Crypto helpers
// ---------------------------------------------------------------------------

/** URL-safe random token (default 32 bytes => 43 base64url chars). */
export function randomToken(bytes = 32): string {
  return randomBytes(bytes).toString("base64url");
}

/** Unkeyed SHA-256 hash, used for lookup keys of high-entropy values. */
export function hashValue(value: string): string {
  return createHash("sha256").update(value).digest("base64url");
}

/** HMAC-SHA256 keyed by the configured session secret. */
export function hmacValue(secret: string, value: string): string {
  return createHmac("sha256", secret).update(value).digest("base64url");
}

/** Constant-time string comparison for equal-length base64url values. */
export function timingSafeEqualStrings(a: string, b: string): boolean {
  const left = Buffer.from(a, "utf8");
  const right = Buffer.from(b, "utf8");
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

export interface PkcePair {
  verifier: string;
  challenge: string;
}

/** Generate an RFC 7636 verifier (43 chars) and its S256 challenge. */
export function generatePkce(): PkcePair {
  const verifier = randomToken(32);
  return { verifier, challenge: pkceChallenge(verifier) };
}

/** S256 code challenge: base64url(sha256(verifier)). */
export function pkceChallenge(verifier: string): string {
  return createHash("sha256").update(verifier).digest("base64url");
}

// ---------------------------------------------------------------------------
// Allowlists and readiness
// ---------------------------------------------------------------------------

export function isAuthProvider(value: unknown): value is AuthProvider {
  return value === "vk" || value === "yandex";
}

export function isVariant(value: unknown): value is Variant {
  return typeof value === "string" && (VARIANTS as readonly string[]).includes(value);
}

function safeConfig(): ServerConfig | null {
  try {
    return config();
  } catch {
    return null;
  }
}

/**
 * VK ID restricts localhost apps to the default HTTP(S) ports (80/443).
 * `new URL("http://localhost:80").port` is `""`, so an empty port already
 * means "default for the scheme"; explicit non-default ports are rejected.
 */
function isDefaultOAuthPort(origin: string): boolean {
  try {
    const url = new URL(origin);
    if (url.hostname !== "localhost") return false;
    if (url.port === "") return true;
    return (url.protocol === "http:" && url.port === "80") ||
      (url.protocol === "https:" && url.port === "443");
  } catch {
    return false;
  }
}

/**
 * Whether each provider is fully configured for this process. Never returns
 * or logs secret material — only booleans.
 *
 * Only VK ID is restricted to the default localhost ports; Yandex is allowed on
 * any loopback port so it can be exercised directly on the dev server.
 */
export function publicAuthStatus(): Record<AuthProvider, boolean> {
  const cfg = safeConfig();
  const base = cfg !== null && validSecret(cfg.sessionSecret);
  const vk = base &&
    isDefaultOAuthPort(cfg.origin) &&
    cfg.vk.clientId.length > 0 &&
    (cfg.vk.appType === "public" || cfg.vk.serviceToken.length > 0);
  const yandex = base && cfg.yandex.clientId.length > 0;
  return { vk, yandex };
}

// ---------------------------------------------------------------------------
// Cookies
// ---------------------------------------------------------------------------

export function parseCookies(request: Request): Record<string, string> {
  const header = request.headers.get("cookie");
  if (!header) return {};
  const out: Record<string, string> = {};
  for (const part of header.split(";")) {
    const index = part.indexOf("=");
    if (index < 0) continue;
    const name = part.slice(0, index).trim();
    const value = part.slice(index + 1).trim();
    if (name && !(name in out)) out[name] = value;
  }
  return out;
}

function secureAttribute(cfg: ServerConfig): string {
  return cfg.origin.startsWith("https:") ? "; Secure" : "";
}

export function bindCookie(token: string, cfg: ServerConfig): string {
  return `${BIND_COOKIE}=${token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=600${secureAttribute(cfg)}`;
}

export function clearBindCookie(cfg: ServerConfig): string {
  return `${BIND_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0${secureAttribute(cfg)}`;
}

export function sessionCookie(token: string, cfg: ServerConfig): string {
  return `${SESSION_COOKIE}=${token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=3600${secureAttribute(cfg)}`;
}

export function clearSessionCookie(cfg: ServerConfig): string {
  return `${SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0${secureAttribute(cfg)}`;
}

// ---------------------------------------------------------------------------
// Handshakes
// ---------------------------------------------------------------------------

function evictExpiredHandshakes(now: number): void {
  const { handshakes } = store();
  for (const [key, handshake] of handshakes) {
    if (handshake.createdAt + HANDSHAKE_TTL_MS <= now) handshakes.delete(key);
  }
}

function boundHandshakes(): void {
  const { handshakes } = store();
  while (handshakes.size >= MAX_HANDSHAKES) {
    const oldest = handshakes.keys().next().value;
    if (oldest === undefined) break;
    handshakes.delete(oldest);
  }
}

export interface StartedAuth {
  url: string;
  bindToken: string;
  cookie: string;
}

/** Build a provider authorize URL for tests and for the start route. */
export function buildAuthorizeUrl(
  cfg: ServerConfig,
  provider: AuthProvider,
  state: string,
  challenge: string,
): string {
  const redirectUri = `${cfg.origin}/api/auth/callback/${provider}`;
  if (provider === "vk") {
    const url = new URL("https://id.vk.ru/authorize");
    url.searchParams.set("response_type", "code");
    url.searchParams.set("client_id", cfg.vk.clientId);
    url.searchParams.set("redirect_uri", redirectUri);
    url.searchParams.set("state", state);
    url.searchParams.set("code_challenge", challenge);
    url.searchParams.set("code_challenge_method", "S256");
    url.searchParams.set("provider", "vkid");
    url.searchParams.set("scope", "vkid.personal_info");
    return url.toString();
  }
  const url = new URL("https://oauth.yandex.ru/authorize");
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", cfg.yandex.clientId);
  url.searchParams.set("redirect_uri", redirectUri);
  url.searchParams.set("state", state);
  url.searchParams.set("code_challenge", challenge);
  url.searchParams.set("code_challenge_method", "S256");
  url.searchParams.set("scope", "login:info");
  return url.toString();
}

/**
 * Create a one-use handshake (state + PKCE + browser binding) and return the
 * authorize URL. Returns `null` when the provider is not ready.
 */
export function beginAuth(provider: AuthProvider, variant: Variant): StartedAuth | null {
  const cfg = safeConfig();
  if (!cfg || !publicAuthStatus()[provider]) return null;

  const now = Date.now();
  evictExpiredHandshakes(now);
  boundHandshakes();

  const state = randomToken(32);
  const { verifier, challenge } = generatePkce();
  const bindToken = randomToken(32);
  const stateHash = hashValue(state);

  store().handshakes.set(stateHash, {
    stateHash,
    provider,
    variant,
    verifier,
    bindHash: hmacValue(cfg.sessionSecret, bindToken),
    createdAt: now,
  });

  return {
    url: buildAuthorizeUrl(cfg, provider, state, challenge),
    bindToken,
    cookie: bindCookie(bindToken, cfg),
  };
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

function evictSessions(now: number): void {
  const { sessions } = store();
  for (const [key, session] of sessions) {
    if (session.expiresAt <= now) sessions.delete(key);
  }
  while (sessions.size >= MAX_SESSIONS) {
    const oldest = sessions.keys().next().value;
    if (oldest === undefined) break;
    sessions.delete(oldest);
  }
}

function createSession(
  cfg: ServerConfig,
  provider: AuthProvider,
  userId: string,
  name: string,
  vkAccessToken?: string,
): { session: Session; token: string } {
  const now = Date.now();
  evictSessions(now);

  const token = randomToken(32);
  const key = hmacValue(cfg.sessionSecret, token);
  const session: StoredSession = {
    key,
    subject: hmacValue(cfg.sessionSecret, `${provider}:${userId}`),
    viewer: { name, provider },
    provider,
    expiresAt: now + SESSION_TTL_MS,
    ...(vkAccessToken ? { vkAccessToken } : {}),
  };
  store().sessions.set(key, session);

  return {
    session: {
      subject: session.subject,
      viewer: session.viewer,
      provider: session.provider,
      expiresAt: session.expiresAt,
    },
    token,
  };
}

/** Read and validate the local session cookie. Returns `null` when absent/expired. */
export function getSession(request: Request): Session | null {
  const cfg = safeConfig();
  if (!cfg || !validSecret(cfg.sessionSecret)) return null;

  const token = parseCookies(request)[SESSION_COOKIE];
  if (!isOpaqueToken(token)) return null;

  const key = hmacValue(cfg.sessionSecret, token);
  const session = store().sessions.get(key);
  if (!session) return null;
  if (session.expiresAt <= Date.now()) {
    store().sessions.delete(key);
    return null;
  }
  return {
    subject: session.subject,
    viewer: session.viewer,
    provider: session.provider,
    expiresAt: session.expiresAt,
  };
}

/**
 * Delete the local session and best-effort revoke the VK access token.
 * Always resolves; provider failures are ignored.
 */
export async function endSession(request: Request): Promise<boolean> {
  const cfg = safeConfig();
  if (!cfg || !validSecret(cfg.sessionSecret)) return false;

  const token = parseCookies(request)[SESSION_COOKIE];
  if (!isOpaqueToken(token)) return false;
  const key = hmacValue(cfg.sessionSecret, token);
  const session = store().sessions.get(key);
  if (!session) return false;

  store().sessions.delete(key);

  if (session.provider === "vk" && session.vkAccessToken) {
    try {
      const response = await fetch("https://id.vk.ru/oauth2/logout", {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          client_id: cfg.vk.clientId,
          access_token: session.vkAccessToken,
        }).toString(),
        redirect: "error",
        cache: "no-store",
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      });
      // The local session is already gone; never interpret the provider reply.
      await response.body?.cancel().catch(() => undefined);
    } catch {
      // Best effort only; the local session is already gone.
    }
  }
  return true;
}

// ---------------------------------------------------------------------------
// Provider network exchange
// ---------------------------------------------------------------------------

class AuthFlowError extends Error {
  constructor(readonly code: string) {
    super(code);
  }
}

interface ProviderRequest {
  method: "GET" | "POST";
  url: string;
  form?: Record<string, string>;
  headers?: Record<string, string>;
}

async function providerFetch(request: ProviderRequest): Promise<Response> {
  const headers: Record<string, string> = { ...request.headers };
  let body: string | undefined;
  if (request.form) {
    headers["content-type"] = "application/x-www-form-urlencoded";
    body = new URLSearchParams(request.form).toString();
  }
  const response = await fetch(request.url, {
    method: request.method,
    headers,
    body,
    redirect: "error",
    cache: "no-store",
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  return response;
}

/**
 * Read a web stream while enforcing a byte budget *during* reading. Once the
 * budget is exceeded the stream is cancelled and `null` is returned, so a
 * hostile provider (or request) can never exceed the limit in memory.
 */
async function readBoundedStream(
  body: ReadableStream<Uint8Array> | null,
  limit: number,
): Promise<string | null> {
  if (!body) return "";
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      total += value.byteLength;
      if (total > limit) {
        await reader.cancel().catch(() => undefined);
        return null;
      }
      chunks.push(value);
    }
  } catch {
    return null;
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // Already released/errored; nothing to do.
    }
  }
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder().decode(merged);
}

async function readProviderJson(response: Response): Promise<Record<string, unknown>> {
  const text = await readBoundedStream(response.body, MAX_PROVIDER_BODY_BYTES);
  if (text === null) throw new AuthFlowError("invalid_response");
  try {
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new AuthFlowError("invalid_response");
    }
    return parsed as Record<string, unknown>;
  } catch (error) {
    if (error instanceof AuthFlowError) throw error;
    throw new AuthFlowError("invalid_response");
  }
}

function asString(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return "";
}

function displayName(value: unknown, fallback: string): string {
  const name = asString(value).trim();
  return name.length > 0 ? name.slice(0, 80) : fallback;
}

interface VkToken {
  accessToken: string;
  tokenUserId: string | null;
}

async function exchangeVkCode(
  cfg: ServerConfig,
  code: string,
  verifier: string,
  deviceId: string,
  state: string,
): Promise<VkToken> {
  const form: Record<string, string> = {
    grant_type: "authorization_code",
    code,
    code_verifier: verifier,
    redirect_uri: `${cfg.origin}/api/auth/callback/vk`,
    client_id: cfg.vk.clientId,
    device_id: deviceId,
    state,
  };
  if (cfg.vk.appType === "confidential") form.service_token = cfg.vk.serviceToken;

  const response = await providerFetch({
    method: "POST",
    url: "https://id.vk.ru/oauth2/auth",
    form,
  });
  if (!response.ok) throw new AuthFlowError("provider");
  const data = await readProviderJson(response);

  const accessToken = asString(data.access_token);
  if (!accessToken) throw new AuthFlowError("invalid_response");
  const returnedState = asString(data.state);
  if (returnedState && returnedState !== state) throw new AuthFlowError("state");
  const tokenUserId = data.user_id === undefined || data.user_id === null
    ? null
    : asString(data.user_id);

  return { accessToken, tokenUserId };
}

async function fetchVkUser(
  cfg: ServerConfig,
  accessToken: string,
): Promise<{ userId: string; name: string }> {
  const response = await providerFetch({
    method: "POST",
    url: "https://id.vk.ru/oauth2/user_info",
    form: { client_id: cfg.vk.clientId, access_token: accessToken },
  });
  if (!response.ok) throw new AuthFlowError("provider");
  const data = await readProviderJson(response);
  const user = data.user;
  if (!user || typeof user !== "object" || Array.isArray(user)) {
    throw new AuthFlowError("invalid_response");
  }
  const record = user as Record<string, unknown>;
  const userId = asString(record.user_id);
  if (!userId) throw new AuthFlowError("invalid_response");
  const name = displayName(
    [asString(record.first_name), asString(record.last_name)].filter(Boolean).join(" "),
    "VK ID",
  );
  return { userId, name };
}

async function exchangeYandexCode(
  cfg: ServerConfig,
  code: string,
  verifier: string,
): Promise<string> {
  const form: Record<string, string> = {
    grant_type: "authorization_code",
    code,
    client_id: cfg.yandex.clientId,
    code_verifier: verifier,
  };
  if (cfg.yandex.clientSecret) form.client_secret = cfg.yandex.clientSecret;

  const response = await providerFetch({
    method: "POST",
    url: "https://oauth.yandex.ru/token",
    form,
  });
  if (!response.ok) throw new AuthFlowError("provider");
  const data = await readProviderJson(response);
  const accessToken = asString(data.access_token);
  if (!accessToken) throw new AuthFlowError("invalid_response");
  return accessToken;
}

async function fetchYandexUser(
  cfg: ServerConfig,
  accessToken: string,
): Promise<{ userId: string; name: string }> {
  const response = await providerFetch({
    method: "GET",
    url: "https://login.yandex.ru/info?format=json",
    headers: { authorization: `OAuth ${accessToken}` },
  });
  if (!response.ok) throw new AuthFlowError("provider");
  const data = await readProviderJson(response);

  const clientId = asString(data.client_id);
  // The userinfo endpoint always returns client_id; require it to equal ours.
  if (!clientId || clientId !== cfg.yandex.clientId) {
    throw new AuthFlowError("invalid_response");
  }

  // Yandex `id` is the stable numeric account id. Never fall back to a
  // mutable login string for the session subject.
  const userId = asString(data.id);
  if (!/^[0-9]{1,32}$/.test(userId)) throw new AuthFlowError("invalid_response");
  const name = displayName(
    asString(data.real_name) || asString(data.display_name) || asString(data.login),
    "Яндекс ID",
  );
  return { userId, name };
}

// ---------------------------------------------------------------------------
// Callback completion
// ---------------------------------------------------------------------------

export interface CallbackSuccess {
  ok: true;
  session: Session;
  token: string;
  variant: Variant;
}

export interface CallbackFailure {
  ok: false;
  code: string;
  variant: Variant;
}

export type CallbackResult = CallbackSuccess | CallbackFailure;

const FALLBACK_VARIANT: Variant = "canvas";
const DUPLICATE_GUARDED_PARAMS = ["code", "state", "device_id", "error"] as const;

function failure(code: string, variant: Variant): CallbackFailure {
  return { ok: false, code, variant };
}

/**
 * Validate a provider callback and, on success, create a local session.
 *
 * The handshake is consumed atomically *before* any provider network call, so a
 * replayed state can never reach the token endpoint. Device/browser binding is
 * checked with constant-time comparison.
 */
export async function completeAuth(
  provider: AuthProvider,
  params: URLSearchParams,
  bindToken: string | null,
): Promise<CallbackResult> {
  const cfg = safeConfig();
  if (!cfg || !validSecret(cfg.sessionSecret) || !publicAuthStatus()[provider]) {
    return failure("unavailable", FALLBACK_VARIANT);
  }

  for (const name of DUPLICATE_GUARDED_PARAMS) {
    if (params.getAll(name).length > 1) return failure("invalid_request", FALLBACK_VARIANT);
  }

  const state = params.get("state") ?? "";
  if (!isOpaqueToken(state)) return failure("state", FALLBACK_VARIANT);

  const stateHash = hashValue(state);
  const now = Date.now();
  evictExpiredHandshakes(now);

  const handshake = store().handshakes.get(stateHash);
  if (!handshake) return failure("state", FALLBACK_VARIANT);

  // Constant-time state verification against the stored hash.
  if (!timingSafeEqualStrings(handshake.stateHash, stateHash)) {
    return failure("state", FALLBACK_VARIANT);
  }
  if (handshake.provider !== provider) {
    store().handshakes.delete(stateHash);
    return failure("state", FALLBACK_VARIANT);
  }
  if (handshake.createdAt + HANDSHAKE_TTL_MS <= now) {
    store().handshakes.delete(stateHash);
    return failure("state", handshake.variant);
  }

  const expectedBind = handshake.bindHash;
  // Validate the browser-binding token shape before hashing it.
  if (!isOpaqueToken(bindToken)) {
    store().handshakes.delete(stateHash);
    return failure("state", handshake.variant);
  }
  const providedBind = hmacValue(cfg.sessionSecret, bindToken);
  if (!timingSafeEqualStrings(expectedBind, providedBind)) {
    store().handshakes.delete(stateHash);
    return failure("state", handshake.variant);
  }

  // Atomic one-use consume, before any network side effect.
  store().handshakes.delete(stateHash);
  const variant = handshake.variant;

  if (params.get("error")) return failure("denied", variant);

  const code = params.get("code") ?? "";
  if (!code || code.length > 2048) return failure("invalid_request", variant);

  try {
    if (provider === "vk") {
      const deviceId = params.get("device_id") ?? "";
      if (!deviceId || deviceId.length > 256) return failure("invalid_request", variant);

      const token = await exchangeVkCode(cfg, code, handshake.verifier, deviceId, state);
      const user = await fetchVkUser(cfg, token.accessToken);
      if (token.tokenUserId && token.tokenUserId !== user.userId) {
        return failure("invalid_response", variant);
      }
      const created = createSession(cfg, "vk", user.userId, user.name, token.accessToken);
      return { ok: true, session: created.session, token: created.token, variant };
    }

    const accessToken = await exchangeYandexCode(cfg, code, handshake.verifier);
    const user = await fetchYandexUser(cfg, accessToken);
    const created = createSession(cfg, "yandex", user.userId, user.name);
    return { ok: true, session: created.session, token: created.token, variant };
  } catch (error) {
    if (error instanceof AuthFlowError) return failure(error.code, variant);
    return failure("provider", variant);
  }
}

// ---------------------------------------------------------------------------
// Start-route helpers (origin + throttle + bounded body)
// ---------------------------------------------------------------------------

/** Same-origin check for mutations; missing, foreign or malformed Origin is rejected. */
export function isSameOrigin(request: Request, cfg: ServerConfig): boolean {
  const origin = request.headers.get("origin");
  // Strict string equality against the configured origin. Parsing via `new
  // URL` would accept userinfo/path garbage such as `http://localhost/x`.
  return origin !== null && origin === cfg.origin;
}

/** Per-process fixed-window throttle on a single local bucket.
 *
 * A loopback TCP proxy preserves attacker-supplied `x-forwarded-for`, so no
 * request header is trusted for the key: all local callers share one bucket.
 */
export function allowAuthRequest(): boolean {
  const { rate } = store();
  const now = Date.now();
  const key = "local";

  if (rate.size >= MAX_THROTTLE_KEYS) {
    for (const [bucketKey, bucket] of rate) {
      if (bucket.resetAt <= now) rate.delete(bucketKey);
    }
    while (rate.size >= MAX_THROTTLE_KEYS) {
      const oldest = rate.keys().next().value;
      if (oldest === undefined) break;
      rate.delete(oldest);
    }
  }

  const bucket = rate.get(key);
  if (!bucket || bucket.resetAt <= now) {
    rate.set(key, { count: 1, resetAt: now + THROTTLE_WINDOW_MS });
    return true;
  }
  if (bucket.count >= THROTTLE_MAX_REQUESTS) return false;
  bucket.count += 1;
  return true;
}

/** Read a small JSON body; returns `null` on malformed/oversized input.
 *
 * The byte budget is enforced while the stream is read, not after. */
export async function readBoundedJson(
  request: Request,
  limit = MAX_BODY_BYTES,
): Promise<Record<string, unknown> | null> {
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declared) && declared > limit) return null;
  const text = await readBoundedStream(request.body, limit);
  if (text === null || text.length === 0) return null;
  try {
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}
