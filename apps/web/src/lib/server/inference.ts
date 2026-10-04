import { randomUUID } from "node:crypto";
import type { GenerationResult, ModelOption } from "../contracts";
import { config } from "./config";
import { ApiError } from "./http";
import { isExactZero } from "./free-models";
import { readBoundedResponse } from "./bounded-json";

export interface PromptRequest { model: string; prompt: string; consent: true }

export function validatePrompt(body: Record<string, unknown>): PromptRequest {
  if (Object.keys(body).some((key) => !["model", "prompt", "consent"].includes(key))) {
    throw new ApiError(400, "unsupported_parameters", "Эта версия принимает только текстовый запрос и выбранную модель.");
  }
  if (typeof body.model !== "string" || body.model.length > 200 || !body.model.endsWith(":free")) {
    throw new ApiError(400, "free_model_required", "Выберите бесплатную модель из каталога.");
  }
  if (typeof body.prompt !== "string" || !body.prompt.trim() || body.prompt.length > 2000) {
    throw new ApiError(400, "invalid_prompt", "Введите запрос длиной от 1 до 2000 символов.");
  }
  if (body.consent !== true) {
    throw new ApiError(400, "consent_required", "Подтвердите передачу текста OpenRouter и провайдеру модели.");
  }
  return { model: body.model, prompt: body.prompt.trim(), consent: true };
}

/** Single-process local budget. Restart resets it: NOT production rate limiting. */
export class InferenceBudget {
  private users = new Map<string, { since: number; count: number }>();
  private active = new Set<string>();
  private day = "";
  private dailyCount = 0;
  private blocked = false;

  blockForUnexpectedCost(): void { this.blocked = true; }

  acquire(subject: string, now = Date.now()): () => void {
    if (this.blocked) throw new ApiError(503, "inference_paused", "Запросы приостановлены: нулевую стоимость нельзя подтвердить. Нужна проверка конфигурации.");
    const day = new Date(now).toISOString().slice(0, 10);
    if (this.day !== day) { this.day = day; this.dailyCount = 0; }
    for (const [key, value] of this.users) if (now - value.since >= 60_000) this.users.delete(key);
    if (this.active.has(subject) || this.active.size >= 2) {
      throw new ApiError(429, "request_in_progress", "Дождитесь завершения текущего запроса.");
    }
    const user = this.users.get(subject) || { since: now, count: 0 };
    if (user.count >= 3 || this.dailyCount >= 20 || (!this.users.has(subject) && this.users.size >= 1000)) {
      throw new ApiError(429, "local_rate_limit", "Лимит локальной версии: 3 запроса в минуту и 20 в сутки на приложение. Повторите позже.");
    }
    user.count++;
    this.users.set(subject, user);
    this.dailyCount++;
    this.active.add(subject);
    let released = false;
    return () => { if (!released) { this.active.delete(subject); released = true; } };
  }
}

const shared = globalThis as typeof globalThis & { rubaiInferenceBudget?: InferenceBudget };
export const inferenceBudget = shared.rubaiInferenceBudget ||= new InferenceBudget();

export function upstreamBody(input: PromptRequest) {
  return {
    model: input.model,
    messages: [{ role: "user", content: input.prompt }],
    max_tokens: 600,
    stream: false,
    usage: { include: true },
    // No tools/plugins, automatic model choice or paid fallback. Price ceiling
    // remains zero even if the catalog changes between validation and inference.
    provider: {
      allow_fallbacks: false,
      require_parameters: true,
      max_price: { prompt: 0, completion: 0, request: 0, image: 0 },
    },
  };
}

function tokenCount(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

export async function generate(
  input: PromptRequest, catalog: ModelOption[], signal: AbortSignal,
  fetcher: typeof fetch = fetch,
): Promise<GenerationResult> {
  const { openRouterKey } = config();
  if (!openRouterKey) throw new ApiError(503, "inference_not_configured", "Серверный ключ OpenRouter ещё не настроен.");
  if (!catalog.some((model) => model.id === input.model)) {
    throw new ApiError(400, "model_unavailable", "Модель больше не доступна бесплатно. Обновите каталог и выберите другую.");
  }
  const requestId = randomUUID();
  let response: Response;
  try {
    response = await fetcher("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST", redirect: "error", cache: "no-store",
      headers: { Authorization: `Bearer ${openRouterKey}`, "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(upstreamBody(input)),
      signal: AbortSignal.any([signal, AbortSignal.timeout(60_000)]),
    });
  } catch {
    throw new ApiError(signal.aborted ? 499 : 504, signal.aborted ? "cancelled" : "provider_timeout",
      signal.aborted ? "Запрос остановлен. Провайдер мог учесть его в своей квоте." : "Модель не ответила вовремя. Автоматический повтор не выполнялся.");
  }
  if (!response.ok) {
    await response.body?.cancel();
    if (response.status === 429) throw new ApiError(429, "provider_rate_limit", "Бесплатная квота OpenRouter или модели исчерпана. Попробуйте позже.");
    if ([401, 403].includes(response.status)) throw new ApiError(503, "provider_access_denied", "OpenRouter не разрешил запрос. Проверьте серверный ключ и доступ к модели.");
    if ([400, 402, 404].includes(response.status)) throw new ApiError(503, "provider_model_unavailable", "Для этой модели сейчас нет подходящего бесплатного маршрута. Выберите другую.");
    throw new ApiError(502, "provider_error", "Модель временно недоступна. Платный запасной маршрут не использовался.");
  }
  let payload: unknown;
  try { payload = await readBoundedResponse(response, 256 * 1024); } catch {
    throw new ApiError(502, "invalid_provider_response", "Модель вернула некорректный ответ.");
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new ApiError(502, "invalid_provider_response", "Модель вернула некорректный ответ.");
  const data = payload as { model?: unknown; error?: unknown; choices?: Array<{ message?: { content?: unknown; refusal?: unknown } }>; usage?: { cost?: unknown; prompt_tokens?: unknown; completion_tokens?: unknown } };
  const cost = data.usage?.cost;
  if (cost === undefined || cost === null) {
    inferenceBudget.blockForUnexpectedCost();
    throw new ApiError(502, "unconfirmed_provider_cost", "Не удалось подтвердить нулевую стоимость. Запросы приостановлены до проверки OpenRouter.");
  }
  if (cost !== 0 && !isExactZero(cost)) {
    inferenceBudget.blockForUnexpectedCost();
    throw new ApiError(502, "unexpected_provider_cost", "Провайдер сообщил неожиданную стоимость. Остановите тестирование и проверьте настройки OpenRouter.");
  }
  if (data.error) throw new ApiError(502, "provider_error", "Провайдер не завершил запрос. Выберите другую бесплатную модель.");
  // Some responses use the underlying model ID without the catalog :free suffix.
  if (data.model !== undefined && data.model !== input.model && data.model !== input.model.slice(0, -5)) {
    throw new ApiError(502, "model_response_mismatch", "Провайдер вернул другую модель. Автоматический повтор не выполнялся.");
  }
  const message = data.choices?.[0]?.message;
  let text = typeof message?.content === "string" ? message.content : "";
  if (!text && Array.isArray(message?.content)) {
    text = message.content.filter((part) => part && part.type === "text" && typeof part.text === "string").map((part) => part.text).join("\n");
  }
  if (!text && typeof message?.refusal === "string") text = message.refusal;
  if (!text.trim()) throw new ApiError(502, "empty_response", "Модель завершила запрос без текстового ответа. Попробуйте другую модель.");
  return { text, model: input.model, requestId, inputTokens: tokenCount(data.usage?.prompt_tokens), outputTokens: tokenCount(data.usage?.completion_tokens) };
}
