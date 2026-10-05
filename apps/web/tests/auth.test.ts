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

  it("allows a public https origin while VK stays restricted to the default localhost port", () => {
    setEnv({ APP_ORIGIN: "https://example.com" });
    expect(publicAuthStatus()).toEqual({ vk: false, yandex: true });
    setEnv({ APP_ORIGIN: "https://example.com:8443" });
    expect(publicAuthStatus()).toEqual({ vk: false, yandex: true });
  });
});

