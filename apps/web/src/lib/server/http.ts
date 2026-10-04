import { config } from "./config";

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message);
  }
}

export function requireOrigin(request: Request): void {
  if (request.headers.get("origin") !== config().origin) {
    throw new ApiError(403, "invalid_origin", "Откройте приложение по настроенному локальному адресу.");
  }
}

export function jsonResponse(value: unknown, status = 200, headers?: HeadersInit): Response {
  return Response.json(value, { status, headers: { "Cache-Control": "no-store", ...headers } });
}

export function errorResponse(error: unknown): Response {
  if (error instanceof ApiError) {
    return jsonResponse({ error: { code: error.code, message: error.message } }, error.status,
      error.status === 429 ? { "Retry-After": "60" } : undefined);
  }
  return jsonResponse({ error: { code: "service_unavailable", message: "Сервис временно недоступен. Попробуйте ещё раз." } }, 503);
}

export async function readBoundedJson(request: Request, limit = 12_000): Promise<Record<string, unknown>> {
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) {
    throw new ApiError(415, "invalid_content_type", "Ожидается JSON-запрос.");
  }
  const reader = request.body?.getReader();
  if (!reader) throw new ApiError(400, "invalid_body", "Запрос пуст.");
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) {
        await reader.cancel();
        throw new ApiError(413, "body_too_large", "Слишком большой запрос.");
      }
      chunks.push(value);
    }
  } finally {
    try { reader.releaseLock(); } catch { /* retain original error */ }
  }
  try {
    const buffer = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { buffer.set(chunk, offset); offset += chunk.byteLength; }
    const value: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(buffer));
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
    return value as Record<string, unknown>;
  } catch {
    throw new ApiError(400, "invalid_body", "Некорректный JSON-запрос.");
  }
}
