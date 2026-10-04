import { getSession } from "../../../../lib/server/auth";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  const session = getSession(request);
  const headers = new Headers();
  headers.set("content-type", "application/json; charset=utf-8");
  headers.set("cache-control", "no-store");
  headers.set("referrer-policy", "no-referrer");
  headers.set("x-content-type-options", "nosniff");
  return new Response(
    JSON.stringify({ viewer: session ? session.viewer : null }),
    { status: 200, headers },
  );
}
