import { getSession } from "@/lib/server/auth";
import { getFreeModels } from "@/lib/server/free-models";
import { config } from "@/lib/server/config";
import { ApiError, errorResponse, jsonResponse, readBoundedJson, requireOrigin } from "@/lib/server/http";
import { generate, inferenceBudget, validatePrompt } from "@/lib/server/inference";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  let release: (() => void) | undefined;
  try {
    requireOrigin(request);
    const session = await getSession(request);
    if (!session) throw new ApiError(401, "login_required", "Войдите через VK ID или Яндекс ID, чтобы отправить запрос.");
    if (!config().openRouterKey) throw new ApiError(503, "inference_not_configured", "Серверный ключ OpenRouter ещё не настроен.");
    const input = validatePrompt(await readBoundedJson(request));
    release = inferenceBudget.acquire(session.subject);
    const models = await getFreeModels({ fresh: true, signal: request.signal });
    if (request.signal.aborted) throw new ApiError(499, "cancelled", "Запрос остановлен до отправки модели.");
    return jsonResponse(await generate(input, models, request.signal));
  } catch (error) {
    return errorResponse(error);
  } finally {
    release?.();
  }
}
