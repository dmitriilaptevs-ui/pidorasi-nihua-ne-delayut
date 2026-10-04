import { publicAuthStatus } from "@/lib/server/auth";
import { config } from "@/lib/server/config";
import { errorResponse, jsonResponse } from "@/lib/server/http";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  try { return jsonResponse({ providers: publicAuthStatus(), inference: Boolean(config().openRouterKey) }); }
  catch (error) { return errorResponse(error); }
}
