import type { ModelOption } from "../contracts";
import { config } from "./config";
import { ApiError } from "./http";
import { readBoundedResponse } from "./bounded-json";

const CATALOG_URL = "https://openrouter.ai/api/v1/models";

/** Exact lexical zero check: do not round tiny nonzero prices into free access. */
export function isExactZero(value: unknown): boolean {
  return typeof value === "string" && /^0+(?:\.0+)?(?:[eE][+-]?\d+)?$/.test(value);
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function parseFreeModels(payload: unknown, allowlist: string[] = []): ModelOption[] {
  if (!record(payload) || !Array.isArray(payload.data)) {
    throw new ApiError(502, "catalog_invalid", "Каталог моделей вернул некорректные данные.");
  }
  const models = new Map<string, ModelOption>();
  for (const raw of payload.data) {
    if (!record(raw) || typeof raw.id !== "string" || !/^[a-zA-Z0-9._-]+\/[a-zA-Z0-9._:-]+:free$/.test(raw.id) ||
      raw.id.length > 200 || typeof raw.name !== "string" || !raw.name.trim() || raw.name.length > 250) continue;
    if (allowlist.length && !allowlist.includes(raw.id)) continue;
    if (!record(raw.pricing) || !isExactZero(raw.pricing.prompt) || !isExactZero(raw.pricing.completion) ||
      !Object.values(raw.pricing).every(isExactZero)) continue;
    if (!record(raw.architecture) || !Array.isArray(raw.architecture.input_modalities) ||
      !Array.isArray(raw.architecture.output_modalities) || !raw.architecture.input_modalities.includes("text") ||
      !raw.architecture.output_modalities.includes("text")) continue;
    if (!Number.isSafeInteger(raw.context_length) || (raw.context_length as number) < 2048) continue;
    models.set(raw.id, {
      id: raw.id,
      name: raw.name.replace(/\s*\(free\)\s*$/i, "").trim(),
      provider: raw.id.split("/")[0],
      contextLength: raw.context_length as number,
    });
  }
  return [...models.values()].sort((a, b) => a.name.localeCompare(b.name));
}

interface CatalogCache { expires: number; allowlistKey: string; models: ModelOption[] }
let cache: CatalogCache | undefined;
let inFlight: { key: string; promise: Promise<ModelOption[]> } | undefined;
let failedUntil = 0;

interface CatalogOptions { fresh?: boolean; fetcher?: typeof fetch; signal?: AbortSignal }

export async function getFreeModels(options: CatalogOptions = {}): Promise<ModelOption[]> {
  const allowlist = config().freeModelAllowlist;
  const key = allowlist.join(",");
  const shared = !options.fresh && !options.fetcher && !options.signal;
  if (shared && cache && cache.expires > Date.now() && cache.allowlistKey === key) return cache.models;
  if (shared && inFlight?.key === key) return inFlight.promise;
  if (shared && failedUntil > Date.now()) throw new ApiError(503, "catalog_unavailable", "Каталог временно недоступен. Повторите через несколько секунд.");
  const promise = loadCatalog(options, allowlist, key);
  if (!shared) return promise;
  inFlight = { key, promise };
  try { return await promise; }
  catch (error) { failedUntil = Date.now() + 5000; throw error; }
  finally { if (inFlight?.promise === promise) inFlight = undefined; }
}

async function loadCatalog(options: CatalogOptions, allowlist: string[], key: string): Promise<ModelOption[]> {
  let response: Response;
  try {
    response = await (options.fetcher || fetch)(CATALOG_URL, {
      headers: { Accept: "application/json" }, cache: "no-store", redirect: "error",
      signal: options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(12_000)]) : AbortSignal.timeout(12_000),
    });
  } catch {
    throw new ApiError(503, "catalog_unavailable", "Не удалось загрузить каталог OpenRouter. Обновите его чуть позже.");
  }
  if (!response.ok) {
    await response.body?.cancel();
    throw new ApiError(503, "catalog_unavailable", "Каталог OpenRouter сейчас недоступен. Попробуйте обновить.");
  }
  let payload: unknown;
  try { payload = await readBoundedResponse(response, 4 * 1024 * 1024); } catch {
    throw new ApiError(502, "catalog_invalid", "Каталог моделей вернул некорректные данные.");
  }
  const models = parseFreeModels(payload, allowlist);
  if (!options.fetcher) cache = { expires: Date.now() + 30_000, allowlistKey: key, models };
  return models;
}
