import { getFreeModels } from "@/lib/server/free-models";
import { errorResponse, jsonResponse } from "@/lib/server/http";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  try { return jsonResponse({ models: await getFreeModels() }); }
  catch (error) { return errorResponse(error); }
}
