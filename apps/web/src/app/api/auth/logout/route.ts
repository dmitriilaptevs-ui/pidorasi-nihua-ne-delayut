import { clearSessionCookie, endSession, isSameOrigin } from "../../../../lib/server/auth";
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
    return jsonError(400, "unavailable", "Session is not configured for this process.");
  }

  if (!isSameOrigin(request, cfg)) {
    return jsonError(403, "origin", "Request origin is not allowed.");
  }

  await endSession(request);

  const headers = new Headers();
  headers.set("content-type", "application/json; charset=utf-8");
  headers.set("cache-control", "no-store");
  headers.set("referrer-policy", "no-referrer");
  headers.set("x-content-type-options", "nosniff");
  headers.append("set-cookie", clearSessionCookie(cfg));
  return new Response(JSON.stringify({ ok: true }), { status: 200, headers });
}
