import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  BIND_COOKIE,
  SESSION_COOKIE,
  __resetAuthStateForTests,
  buildAuthorizeUrl,
  generatePkce,
  getSession,
  pkceChallenge,
  publicAuthStatus,
} from "../src/lib/server/auth";
import { POST as startPOST } from "../src/app/api/auth/start/route";
import { GET as callbackGET } from "../src/app/api/auth/callback/[provider]/route";
import { GET as sessionGET } from "../src/app/api/auth/session/route";
import { POST as logoutPOST } from "../src/app/api/auth/logout/route";
import type { AuthProvider } from "../src/lib/contracts";

const ENV: Record<string, string> = {
  APP_ORIGIN: "http://localhost",
  SESSION_SECRET: "a".repeat(43),
  VK_CLIENT_ID: "vk-client",
  VK_APP_TYPE: "confidential",
  VK_SERVICE_TOKEN: "vk-service",
  YANDEX_CLIENT_ID: "ya-client",
  YANDEX_CLIENT_SECRET: "ya-secret",
  OPENROUTER_API_KEY: "",
  OPENROUTER_FREE_MODELS: "",
};

function setEnv(overrides: Record<string, string | undefined> = {}): void {
  for (const [key, value] of Object.entries({ ...ENV, ...overrides })) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
}

beforeEach(() => {
  __resetAuthStateForTests();
  setEnv();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

interface MockReply {
  body?: unknown;
  text?: string;
  ok?: boolean;
  status?: number;
}

function mockFetchSequence(replies: MockReply[]) {
  const queue = [...replies];
  const impl = async (
    _input: RequestInfo | URL,
    _init?: RequestInit,
  ): Promise<Response> => {
    const next = queue.shift();
    if (!next) throw new Error("unexpected fetch");
    const payload = next.text !== undefined ? next.text : JSON.stringify(next.body ?? {});
    return new Response(payload, {
      status: next.status ?? (next.ok === false ? 400 : 200),
      headers: { "content-type": "application/json" },
    });
  };
  const mock = vi.fn(impl);
  vi.stubGlobal("fetch", mock);
  return mock;
}

function startRequest(
  provider: string,
  variant: string,
  origin: string | null = "http://localhost",
): Request {
  const headers = new Headers({ "content-type": "application/json" });
  if (origin !== null) headers.set("origin", origin);
  return new Request("http://localhost/api/auth/start", {
    method: "POST",
    headers,
    body: JSON.stringify({ provider, variant }),
  });
}

function cookieValue(response: Response, name: string): string {
  for (const cookie of response.headers.getSetCookie()) {
    if (cookie.startsWith(`${name}=`)) {
      return cookie.slice(name.length + 1).split(";")[0];
    }
  }
  throw new Error(`missing cookie ${name}`);
}

async function startVk(variant = "flow"): Promise<{ state: string; bind: string; response: Response }> {
  const response = await startPOST(startRequest("vk", variant));
  const { url } = (await response.clone().json()) as { url: string };
  const state = new URL(url).searchParams.get("state") ?? "";
  return { state, bind: cookieValue(response, BIND_COOKIE), response };
}

function callbackRequest(
  provider: string,
  params: Array<[string, string]>,
  bindToken?: string | null,
): Request {
  const url = new URL(`http://localhost/api/auth/callback/${provider}`);
  for (const [key, value] of params) url.searchParams.append(key, value);
  const headers = new Headers();
  if (bindToken) headers.set("cookie", `${BIND_COOKIE}=${bindToken}`);
  return new Request(url, { method: "GET", headers });
}

async function callCallback(
  provider: AuthProvider,
  params: Array<[string, string]>,
  bindToken?: string | null,
): Promise<Response> {
  return callbackGET(callbackRequest(provider, params, bindToken), {
    params: Promise.resolve({ provider }),
  });
}

function getWithCookie(path: string, cookie?: string): Request {
  const headers = new Headers();
  if (cookie) headers.set("cookie", cookie);
  return new Request(`http://localhost${path}`, { headers });
}

describe("PKCE and crypto structure", () => {
  it("generates RFC 7636 S256 verifier/challenge pairs", () => {
    const pair = generatePkce();
    expect(pair.verifier).toMatch(/^[A-Za-z0-9_-]{43,128}$/);
    expect(pair.challenge).toBe(pkceChallenge(pair.verifier));
    expect(Buffer.from(pair.challenge, "base64url")).toHaveLength(32);
    expect(generatePkce().verifier).not.toBe(pair.verifier);
  });

  it("builds provider authorize URLs with S256 and state", () => {
    const cfg = {
      origin: "http://localhost",
      sessionSecret: "a".repeat(43),
      vk: { clientId: "vk-client", appType: "public" as const, serviceToken: "" },
      yandex: { clientId: "ya-client", clientSecret: "" },
      openRouterKey: "",
      freeModelAllowlist: [],
    };
    const vk = new URL(buildAuthorizeUrl(cfg, "vk", "state-1", "challenge-1"));
    expect(vk.origin).toBe("https://id.vk.ru");
    expect(vk.searchParams.get("state")).toBe("state-1");
    expect(vk.searchParams.get("code_challenge")).toBe("challenge-1");
    expect(vk.searchParams.get("code_challenge_method")).toBe("S256");

    const yandex = new URL(buildAuthorizeUrl(cfg, "yandex", "state-2", "challenge-2"));
    expect(yandex.origin).toBe("https://oauth.yandex.ru");
    expect(yandex.searchParams.get("scope")).toBe("login:info");
  });
});

describe("provider readiness", () => {
  it("returns booleans only and never leaks secrets", () => {
    const status = publicAuthStatus();
    expect(status).toEqual({ vk: true, yandex: true });
    const serialized = JSON.stringify(status);
    expect(serialized).not.toContain(ENV.SESSION_SECRET);
    expect(serialized).not.toContain(ENV.VK_SERVICE_TOKEN);
    expect(serialized).not.toContain(ENV.YANDEX_CLIENT_SECRET);
  });

  it("marks VK confidential unavailable without a service token", () => {
    setEnv({ VK_SERVICE_TOKEN: undefined });
    expect(publicAuthStatus()).toEqual({ vk: false, yandex: true });
  });

  it("allows public VK without a service token", () => {
    setEnv({ VK_APP_TYPE: "public", VK_SERVICE_TOKEN: undefined });
    expect(publicAuthStatus().vk).toBe(true);
  });

  it("restricts only VK to the default localhost port", () => {
    setEnv({ APP_ORIGIN: "http://localhost:3001" });
    expect(publicAuthStatus()).toEqual({ vk: false, yandex: true });
    setEnv({ APP_ORIGIN: "http://127.0.0.1:3001" });
    expect(publicAuthStatus()).toEqual({ vk: false, yandex: true });
    setEnv({ SESSION_SECRET: "too-short" });
    expect(publicAuthStatus()).toEqual({ vk: false, yandex: false });
  });

  it("fails closed when APP_ORIGIN is not loopback", () => {
    setEnv({ APP_ORIGIN: "https://example.com" });
    expect(publicAuthStatus()).toEqual({ vk: false, yandex: false });
  });
});

describe("start route", () => {
  it("rejects missing and foreign Origin on mutations", async () => {
    const missing = await startPOST(startRequest("vk", "flow", null));
    expect(missing.status).toBe(403);
    expect((await missing.json()).error.code).toBe("origin");

    const foreign = await startPOST(startRequest("vk", "flow", "https://evil.example"));
    expect(foreign.status).toBe(403);

    const ok = await startPOST(startRequest("vk", "flow"));
    expect(ok.status).toBe(200);
  });

  it("strictly compares Origin instead of normalizing garbage", async () => {
    const garbage = [
      "http://localhost/",
      "http://localhost/../evil",
      "http://user@localhost",
      "http://localhost/?x=1",
      "http://LOCALHOST",
    ];
    for (const origin of garbage) {
      const response = await startPOST(startRequest("vk", "flow", origin));
      expect(response.status).toBe(403);
    }
  });

  it("validates provider and variant allowlists", async () => {
    for (const [provider, variant] of [["github", "flow"], ["vk", "nope"]]) {
      const response = await startPOST(startRequest(provider, variant));
      expect(response.status).toBe(400);
      expect((await response.json()).error.code).toBe("invalid_request");
    }
  });

  it("rejects malformed bodies with a safe error shape", async () => {
    const request = new Request("http://localhost/api/auth/start", {
      method: "POST",
      headers: { origin: "http://localhost", "content-type": "application/json" },
      body: "{not json",
    });
    const response = await startPOST(request);
    expect(response.status).toBe(400);
    const body = await response.json();
    expect(body.error.code).toBe("invalid_request");
    expect(JSON.stringify(body)).not.toMatch(/SyntaxError|at .*route/);
  });

  it("returns a VK authorize URL with the documented parameters", async () => {
    const { response } = await startVk();
    const { url } = (await response.json()) as { url: string };
    const parsed = new URL(url);
    expect(parsed.origin).toBe("https://id.vk.ru");
    expect(parsed.pathname).toBe("/authorize");
    expect(parsed.searchParams.get("response_type")).toBe("code");
    expect(parsed.searchParams.get("client_id")).toBe("vk-client");
    expect(parsed.searchParams.get("redirect_uri")).toBe(
      "http://localhost/api/auth/callback/vk",
    );
    expect(parsed.searchParams.get("code_challenge_method")).toBe("S256");
    expect(parsed.searchParams.get("provider")).toBe("vkid");
    expect(parsed.searchParams.get("scope")).toBe("vkid.personal_info");
    const state = parsed.searchParams.get("state") ?? "";
    expect(Buffer.from(state, "base64url").length).toBeGreaterThanOrEqual(32);
    expect(parsed.searchParams.get("code_challenge")).toBeTruthy();
  });

  it("returns a Yandex authorize URL with login:info scope", async () => {
    const response = await startPOST(startRequest("yandex", "pulse"));
    const { url } = (await response.json()) as { url: string };
    const parsed = new URL(url);
    expect(parsed.origin).toBe("https://oauth.yandex.ru");
    expect(parsed.pathname).toBe("/authorize");
    expect(parsed.searchParams.get("scope")).toBe("login:info");
    expect(parsed.searchParams.get("code_challenge_method")).toBe("S256");
    expect(parsed.searchParams.get("redirect_uri")).toBe(
      "http://localhost/api/auth/callback/yandex",
    );
  });

  it("sets an HttpOnly SameSite=Lax bind cookie", async () => {
    const response = await startPOST(startRequest("vk", "flow"));
    const cookie = response.headers.getSetCookie().find((c) => c.startsWith(BIND_COOKIE));
    expect(cookie).toBeTruthy();
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("SameSite=Lax");
    expect(cookie).toContain("Max-Age=600");
    expect(cookie).not.toContain("Secure");
  });

  it("marks cookies Secure on https origins", async () => {
    setEnv({ APP_ORIGIN: "https://localhost" });
    const response = await startPOST(startRequest("yandex", "canvas", "https://localhost"));
    const cookie = response.headers.getSetCookie().find((c) => c.startsWith(BIND_COOKIE));
    expect(cookie).toContain("Secure");
  });

  it("issues exactly 43-char base64url state and binding tokens", async () => {
    const response = await startPOST(startRequest("vk", "flow"));
    const { url } = (await response.json()) as { url: string };
    const state = new URL(url).searchParams.get("state") ?? "";
    expect(state).toMatch(/^[A-Za-z0-9_-]{43}$/);
    const bind = cookieValue(response, BIND_COOKIE);
    expect(bind).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });

  it("enforces streaming byte bounds on the request body", async () => {
    const tracker = { canceled: false };
    let sent = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (sent >= 9 * 1024) {
          controller.close();
          return;
        }
        sent += 1024;
        controller.enqueue(new Uint8Array(1024));
      },
      cancel() {
        tracker.canceled = true;
      },
    });
    const request = new Request("http://localhost/api/auth/start", {
      method: "POST",
      headers: { origin: "http://localhost", "content-type": "application/json" },
      body: stream,
      // Node fetch requires half-duplex for stream bodies.
      duplex: "half",
    } as RequestInit & { duplex: "half" });

    const response = await startPOST(request);
    expect(response.status).toBe(400);
    expect((await response.json()).error.code).toBe("invalid_request");
    expect(tracker.canceled).toBe(true);
  });

  it("throttles a single local bucket regardless of forwarded headers", async () => {
    for (let index = 0; index < 30; index += 1) {
      const request = startRequest("vk", "flow");
      request.headers.set("x-forwarded-for", `10.0.0.${index}`);
      const response = await startPOST(request);
      expect(response.status).toBe(200);
    }
    const limited = startRequest("vk", "flow");
    limited.headers.set("x-forwarded-for", "203.0.113.7");
    const response = await startPOST(limited);
    expect(response.status).toBe(429);
    expect((await response.json()).error.code).toBe("rate_limited");
  });
});

describe("VK callback", () => {
  it("exchanges a code, calls user_info and creates a session", async () => {
    const { state, bind } = await startVk("pulse");
    const fetchMock = mockFetchSequence([
      { body: { access_token: "vk-token", user_id: 42, state } },
      { body: { user: { user_id: 42, first_name: "Иван", last_name: "Петров" } } },
    ]);

    const response = await callCallback(
      "vk",
      [["code", "code-1"], ["state", state], ["device_id", "dev-1"]],
      bind,
    );

    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("http://localhost/pulse#workspace");
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    expect(response.headers.get("content-security-policy")).toBe("default-src 'none'");

    const [tokenCall, userCall] = fetchMock.mock.calls;
    expect(tokenCall[0]).toBe("https://id.vk.ru/oauth2/auth");
    expect((tokenCall[1] as RequestInit).cache).toBe("no-store");
    const tokenBody = new URLSearchParams((tokenCall[1] as RequestInit).body as string);
    expect(tokenBody.get("grant_type")).toBe("authorization_code");
    expect(tokenBody.get("code")).toBe("code-1");
    expect(tokenBody.get("redirect_uri")).toBe("http://localhost/api/auth/callback/vk");
    expect(tokenBody.get("client_id")).toBe("vk-client");
    expect(tokenBody.get("code_verifier")).toMatch(/^[A-Za-z0-9_-]{43,128}$/);
    expect(tokenBody.get("device_id")).toBe("dev-1");
    expect(tokenBody.get("service_token")).toBe("vk-service");
    expect(tokenBody.get("state")).toBe(state);

    expect(userCall[0]).toBe("https://id.vk.ru/oauth2/user_info");
    const userBody = new URLSearchParams((userCall[1] as RequestInit).body as string);
    expect(userBody.get("client_id")).toBe("vk-client");
    expect(userBody.get("access_token")).toBe("vk-token");

    const sessionToken = cookieValue(response, SESSION_COOKIE);
    const sessionResponse = await sessionGET(getWithCookie("/api/auth/session", `rb_session=${sessionToken}`));
    expect((await sessionResponse.json()).viewer).toEqual({
      name: "Иван Петров",
      provider: "vk",
    });
  });

  it("rejects a wrong browser-binding cookie and consumes the handshake", async () => {
    const { state, bind } = await startVk();
    mockFetchSequence([{ body: { access_token: "t", user_id: 1 } }]);

    const wrong = await callCallback(
      "vk",
      [["code", "c"], ["state", state], ["device_id", "d"]],
      `${bind}-tampered`,
    );
    expect(wrong.headers.get("location")).toContain("auth_error=state");

    // The real handshake was consumed, so even the correct binding now fails.
    const retry = await callCallback(
      "vk",
      [["code", "c"], ["state", state], ["device_id", "d"]],
      bind,
    );
    expect(retry.headers.get("location")).toContain("auth_error=state");
  });

  it("rejects malformed state and binding token shapes before hashing", async () => {
    const { state, bind } = await startVk();
    const tooShort = await callCallback(
      "vk",
      [["code", "c"], ["state", state.slice(0, 42)], ["device_id", "d"]],
      bind,
    );
    expect(tooShort.headers.get("location")).toContain("auth_error=state");

    const badChars = await callCallback(
      "vk",
      [["code", "c"], ["state", `${state.slice(0, 42)}!`], ["device_id", "d"]],
      bind,
    );
    expect(badChars.headers.get("location")).toContain("auth_error=state");

    const shortBind = await callCallback(
      "vk",
      [["code", "c"], ["state", state], ["device_id", "d"]],
      "short-bind",
    );
    expect(shortBind.headers.get("location")).toContain("auth_error=state");

    // A malformed binding consumes the handshake just like a wrong one.
    const replay = await callCallback(
      "vk",
      [["code", "c"], ["state", state], ["device_id", "d"]],
      bind,
    );
    expect(replay.headers.get("location")).toContain("auth_error=state");
  });

  it("rejects a replayed state without a second provider call", async () => {
    const { state, bind } = await startVk();
    const fetchMock = mockFetchSequence([
      { body: { access_token: "vk-token", user_id: 42 } },
      { body: { user: { user_id: 42, first_name: "A" } } },
    ]);
    const params: Array<[string, string]> = [
      ["code", "c"],
      ["state", state],
      ["device_id", "d"],
    ];

    const first = await callCallback("vk", params, bind);
    expect(first.status).toBe(303);
    const replay = await callCallback("vk", params, bind);

    expect(replay.headers.get("location")).toContain("auth_error=state");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("expires handshakes after ten minutes", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const { state, bind } = await startVk();
    vi.setSystemTime(Date.now() + 10 * 60 * 1000 + 1);
    mockFetchSequence([{ body: { access_token: "t", user_id: 1 } }]);

    const response = await callCallback(
      "vk",
      [["code", "c"], ["state", state], ["device_id", "d"]],
      bind,
    );
    expect(response.headers.get("location")).toContain("auth_error=state");
  });

  it("rejects duplicate callback parameters", async () => {
    const { state, bind } = await startVk();
    const response = await callCallback(
      "vk",
      [["code", "a"], ["code", "b"], ["state", state], ["device_id", "d"]],
      bind,
    );
    expect(response.headers.get("location")).toContain("auth_error=invalid_request");
  });

  it("rejects VK callbacks missing device_id", async () => {
    const { state, bind } = await startVk();
    mockFetchSequence([]);
    const response = await callCallback("vk", [["code", "c"], ["state", state]], bind);
    expect(response.headers.get("location")).toContain("auth_error=invalid_request");
  });

  it("maps provider denials to a safe code", async () => {
    const { state, bind } = await startVk();
    const response = await callCallback("vk", [["state", state], ["error", "access_denied"]], bind);
    expect(response.headers.get("location")).toBe("http://localhost/flow?auth_error=denied#workspace");
  });

  it("maps token state mismatch and user id mismatch safely", async () => {
    const first = await startVk();
    mockFetchSequence([{ body: { access_token: "t", state: "different", user_id: 1 } }]);
    const mismatch = await callCallback(
      "vk",
      [["code", "c"], ["state", first.state], ["device_id", "d"]],
      first.bind,
    );
    expect(mismatch.headers.get("location")).toContain("auth_error=state");

    const second = await startVk();
    mockFetchSequence([
      { body: { access_token: "t", user_id: 1 } },
      { body: { user: { user_id: 2, first_name: "X" } } },
    ]);
    const userMismatch = await callCallback(
      "vk",
      [["code", "c"], ["state", second.state], ["device_id", "d"]],
      second.bind,
    );
    expect(userMismatch.headers.get("location")).toContain("auth_error=invalid_response");
  });

  it("maps malformed and failing provider responses safely", async () => {
    const malformed = await startVk();
    mockFetchSequence([{ text: "not-json" }]);
    const badJson = await callCallback(
      "vk",
      [["code", "c"], ["state", malformed.state], ["device_id", "d"]],
      malformed.bind,
    );
    expect(badJson.headers.get("location")).toContain("auth_error=invalid_response");

    const failing = await startVk();
    mockFetchSequence([{ body: { error: "boom" }, status: 500 }]);
    const badStatus = await callCallback(
      "vk",
      [["code", "c"], ["state", failing.state], ["device_id", "d"]],
      failing.bind,
    );
    expect(badStatus.headers.get("location")).toContain("auth_error=provider");
  });

  it("cancels an oversized provider body while streaming", async () => {
    const { state, bind } = await startVk();
    const tracker = { canceled: false };
    let sent = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (sent >= 70 * 1024) {
          controller.close();
          return;
        }
        sent += 1024;
        controller.enqueue(new Uint8Array(1024));
      },
      cancel() {
        tracker.canceled = true;
      },
    });
    const fetchMock = vi.fn(async () => new Response(stream, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const response = await callCallback(
      "vk",
      [["code", "c"], ["state", state], ["device_id", "d"]],
      bind,
    );
    expect(response.headers.get("location")).toContain("auth_error=invalid_response");
    expect(tracker.canceled).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("Yandex callback", () => {
  it("exchanges a code with PKCE and reads userinfo via bearer header", async () => {
    const start = await startPOST(startRequest("yandex", "canvas"));
    const { url } = (await start.json()) as { url: string };
    const state = new URL(url).searchParams.get("state") ?? "";
    const bind = cookieValue(start, BIND_COOKIE);

    const fetchMock = mockFetchSequence([
      { body: { access_token: "ya-token" } },
      { body: { id: 7, real_name: "Мария", client_id: "ya-client" } },
    ]);

    const response = await callCallback("yandex", [["code", "c"], ["state", state]], bind);
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("http://localhost/canvas#workspace");

    const [tokenCall, infoCall] = fetchMock.mock.calls;
    expect(tokenCall[0]).toBe("https://oauth.yandex.ru/token");
    const tokenBody = new URLSearchParams((tokenCall[1] as RequestInit).body as string);
    expect(tokenBody.get("grant_type")).toBe("authorization_code");
    expect(tokenBody.get("client_id")).toBe("ya-client");
    expect(tokenBody.get("client_secret")).toBe("ya-secret");
    expect(tokenBody.get("code_verifier")).toMatch(/^[A-Za-z0-9_-]{43,128}$/);

    expect(infoCall[0]).toBe("https://login.yandex.ru/info?format=json");
    const infoInit = infoCall[1] as RequestInit;
    expect((infoInit.headers as Record<string, string>).authorization).toBe("OAuth ya-token");
  });

  it("rejects a mismatched Yandex client_id", async () => {
    const start = await startPOST(startRequest("yandex", "flow"));
    const { url } = (await start.json()) as { url: string };
    const state = new URL(url).searchParams.get("state") ?? "";
    const bind = cookieValue(start, BIND_COOKIE);
    mockFetchSequence([
      { body: { access_token: "ya-token" } },
      { body: { id: 7, real_name: "M", client_id: "someone-else" } },
    ]);
    const response = await callCallback("yandex", [["code", "c"], ["state", state]], bind);
    expect(response.headers.get("location")).toContain("auth_error=invalid_response");
  });

  it("requires the returned Yandex client_id", async () => {
    const start = await startPOST(startRequest("yandex", "flow"));
    const { url } = (await start.json()) as { url: string };
    const state = new URL(url).searchParams.get("state") ?? "";
    const bind = cookieValue(start, BIND_COOKIE);
    mockFetchSequence([
      { body: { access_token: "ya-token" } },
      { body: { id: 7, real_name: "M" } },
    ]);
    const response = await callCallback("yandex", [["code", "c"], ["state", state]], bind);
    expect(response.headers.get("location")).toContain("auth_error=invalid_response");
  });

  it("requires the stable Yandex id and never falls back to login", async () => {
    const start = await startPOST(startRequest("yandex", "flow"));
    const { url } = (await start.json()) as { url: string };
    const state = new URL(url).searchParams.get("state") ?? "";
    const bind = cookieValue(start, BIND_COOKIE);
    mockFetchSequence([
      { body: { access_token: "ya-token" } },
      { body: { login: "mutable-login", real_name: "M", client_id: "ya-client" } },
    ]);
    const response = await callCallback("yandex", [["code", "c"], ["state", state]], bind);
    expect(response.headers.get("location")).toContain("auth_error=invalid_response");
  });
});

describe("sessions", () => {
  it("exposes a stable derived subject and nulls it after logout", async () => {
    const { state, bind } = await startVk();
    mockFetchSequence([
      { body: { access_token: "t", user_id: 42 } },
      { body: { user: { user_id: 42, first_name: "A" } } },
    ]);
    const response = await callCallback(
      "vk",
      [["code", "c"], ["state", state], ["device_id", "d"]],
      bind,
    );
    const token = cookieValue(response, SESSION_COOKIE);

    const session = getSession(getWithCookie("/api/auth/session", `rb_session=${token}`));
    expect(session).not.toBeNull();
    expect(session!.viewer).toEqual({ name: "A", provider: "vk" });
    expect(session!.subject).not.toContain("42");
    expect(session!.subject.length).toBeGreaterThanOrEqual(20);

    mockFetchSequence([{ body: {} }]);
    const logout = await logoutPOST(
      new Request("http://localhost/api/auth/logout", {
        method: "POST",
        headers: { origin: "http://localhost", cookie: `rb_session=${token}` },
      }),
    );
    expect(logout.status).toBe(200);
    expect(await logout.json()).toEqual({ ok: true });
    expect(logout.headers.getSetCookie().some((c) => c.startsWith("rb_session=;"))).toBe(true);

    const after = await sessionGET(getWithCookie("/api/auth/session", `rb_session=${token}`));
    expect((await after.json()).viewer).toBeNull();
  });

  it("expires sessions after one hour", async () => {
    const { state, bind } = await startVk();
    mockFetchSequence([
      { body: { access_token: "t", user_id: 1 } },
      { body: { user: { user_id: 1, first_name: "A" } } },
    ]);
    const response = await callCallback(
      "vk",
      [["code", "c"], ["state", state], ["device_id", "d"]],
      bind,
    );
    const token = cookieValue(response, SESSION_COOKIE);
    expect(getSession(getWithCookie("/", `rb_session=${token}`))).not.toBeNull();

    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(Date.now() + 60 * 60 * 1000 + 1);
    expect(getSession(getWithCookie("/", `rb_session=${token}`))).toBeNull();
  });

  it("requires same-origin logout and reports the cookie pattern", async () => {
    const foreign = await logoutPOST(
      new Request("http://localhost/api/auth/logout", {
        method: "POST",
        headers: { origin: "https://evil.example" },
      }),
    );
    expect(foreign.status).toBe(403);

    const ok = await logoutPOST(
      new Request("http://localhost/api/auth/logout", {
        method: "POST",
        headers: { origin: "http://localhost" },
      }),
    );
    const cookie = ok.headers.getSetCookie().find((c) => c.startsWith(SESSION_COOKIE));
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("SameSite=Lax");
    expect(cookie).toContain("Max-Age=0");
  });

  it("rejects malformed session cookie values before hashing", () => {
    for (const value of ["", "short", "a".repeat(42), "a".repeat(44), `${"a".repeat(42)}!`]) {
      expect(getSession(getWithCookie("/", `rb_session=${value}`))).toBeNull();
    }
  });

  it("cancels the VK logout response body", async () => {
    const { state, bind } = await startVk();
    mockFetchSequence([
      { body: { access_token: "t", user_id: 1 } },
      { body: { user: { user_id: 1, first_name: "A" } } },
    ]);
    const callback = await callCallback(
      "vk",
      [["code", "c"], ["state", state], ["device_id", "d"]],
      bind,
    );
    const token = cookieValue(callback, SESSION_COOKIE);

    const tracker = { canceled: false };
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.enqueue(new Uint8Array(8));
      },
      cancel() {
        tracker.canceled = true;
      },
    });
    const fetchMock = vi.fn(async () => new Response(stream, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const logout = await logoutPOST(
      new Request("http://localhost/api/auth/logout", {
        method: "POST",
        headers: { origin: "http://localhost", cookie: `rb_session=${token}` },
      }),
    );
    expect(logout.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(tracker.canceled).toBe(true);
  });
});
