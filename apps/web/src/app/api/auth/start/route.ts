import {
  allowAuthRequest,
  beginAuth,
  isAuthProvider,
  isSameOrigin,
  isVariant,
  publicAuthStatus,
  readBoundedJson,
} from "../../../../lib/server/auth";
import { config } from "../../../../lib/server/config";

export const dynamic = "force-dynamic";

function jsonError(status: number, code: string, message: string): Response {
  const headers = new Headers();
  headers.set("content-type", "application/json; charset=utf-8");
  headers.set("cache-control", "no-store");
  headers.set("referrer-policy", "no-referrer");
  headers.set("x-content-type-options", "nosniff");
  return new Response(JSON.stringify({ error: { code, message } }), { status, headers });
}

export async function POST(request: Request): Promise<Response> {
  let cfg;
  try {
    cfg = config();
  } catch {
    return jsonError(400, "unavailable", "OAuth is not configured for this process.");
  }

  if (!isSameOrigin(request, cfg)) {
    return jsonError(403, "origin", "Request origin is not allowed.");
  }

  if (!allowAuthRequest()) {
    return jsonError(429, "rate_limited", "Too many sign-in attempts. Try again later.");
  }

  const body = await readBoundedJson(request);
  const provider = body?.provider;
  const variant = body?.variant;
  if (!isAuthProvider(provider) || !isVariant(variant)) {
    return jsonError(400, "invalid_request", "provider and variant are required.");
  }

  if (!publicAuthStatus()[provider]) {
    return jsonError(400, "unavailable", "This provider is not configured.");
  }

  const started = beginAuth(provider, variant);
  if (!started) {
    return jsonError(400, "unavailable", "This provider is not configured.");
  }

  const headers = new Headers();
  headers.set("content-type", "application/json; charset=utf-8");
  headers.set("cache-control", "no-store");
  headers.set("referrer-policy", "no-referrer");
  headers.set("x-content-type-options", "nosniff");
  headers.append("set-cookie", started.cookie);
  return new Response(JSON.stringify({ url: started.url }), { status: 200, headers });
}
