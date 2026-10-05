export const dynamic = "force-dynamic";

/** Liveness endpoint for the container healthcheck. */
export async function GET() {
  return Response.json({ status: "ok" });
}
