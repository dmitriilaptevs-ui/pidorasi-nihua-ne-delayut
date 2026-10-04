import {
  clearBindCookie,
  completeAuth,
  isAuthProvider,
  parseCookies,
  publicAuthStatus,
  sessionCookie,
  BIND_COOKIE,
} from "../../../../../lib/server/auth";
import { config } from "../../../../../lib/server/config";

export const dynamic = "force-dynamic";

function callbackHeaders(): Headers {
  const headers = new Headers();
  headers.set("cache-control", "no-store");
  headers.set("referrer-policy", "no-referrer");
  headers.set("content-security-policy", "default-src 'none'");
  headers.set("x-content-type-options", "nosniff");
  return headers;
}

function redirectTo(origin: string, variant: string, errorCode?: string): Response {
  const target = new URL(`/${variant}`, origin);
  if (errorCode) target.searchParams.set("auth_error", errorCode);
  target.hash = "workspace";

  const headers = callbackHeaders();
  headers.set("location", target.toString());
  return new Response(null, { status: 303, headers });
}

export async function GET(
  request: Request,
  ctx: { params: Promise<{ provider: string }> },
): Promise<Response> {
  const { provider } = await ctx.params;

  let cfg;
  try {
    cfg = config();
  } catch {
    return new Response(null, { status: 500, headers: callbackHeaders() });
  }

  if (!isAuthProvider(provider) || !publicAuthStatus()[provider]) {
    return redirectTo(cfg.origin, "flow", "unavailable");
  }

  const url = new URL(request.url);
  const bindToken = parseCookies(request)[BIND_COOKIE] ?? null;
  const result = await completeAuth(provider, url.searchParams, bindToken);

  if (!result.ok) {
    const response = redirectTo(cfg.origin, result.variant, result.code);
    response.headers.append("set-cookie", clearBindCookie(cfg));
    return response;
  }

  const response = redirectTo(cfg.origin, result.variant);
  response.headers.append("set-cookie", sessionCookie(result.token, cfg));
  response.headers.append("set-cookie", clearBindCookie(cfg));
  return response;
}
