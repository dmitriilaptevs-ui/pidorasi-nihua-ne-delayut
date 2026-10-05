/** Browser-side API helpers: same-origin fetch with uniform error handling. */

export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  const response = await fetch(path, { ...init, headers, credentials: "same-origin", cache: "no-store" });
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    const error = (payload as { error?: { message?: string; code?: string } } | null)?.error;
    throw new ApiClientError(error?.message || "Не удалось выполнить запрос.", response.status, error?.code);
  }
  return payload as T;
}

export function formatRub(kopecks: number): string {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    minimumFractionDigits: 2,
  }).format(kopecks / 100);
}

export function formatNumber(value: string | number): string {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 4 }).format(numeric);
}

export function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
}

export interface Viewer {
  id: string;
  email: string | null;
  display_name: string | null;
  role: string;
  status: string;
  email_verified: boolean;
  signup_method: string;
}

export interface WalletState {
  balance_kopecks: number;
  reserved_kopecks: number;
  available_kopecks: number;
}

export interface KeyItem {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  revoked_at: string | null;
  last_used_at: string | null;
  monthly_limit_kopecks: number | null;
}

export interface LedgerItem {
  transaction_id: string;
  kind: string;
  amount_kopecks: number;
  memo: string | null;
  created_at: string;
}

export interface PricingView {
  version: number;
  input_rub_per_mtok: string;
  output_rub_per_mtok: string;
  cached_rub_per_mtok: string | null;
  fx_rate: string;
  markup: string;
  valid_from: string;
}

export interface CatalogItem {
  id: string;
  name: string;
  provider: string;
  context_length: number;
  supports_tools: boolean;
  available: boolean;
  pricing: PricingView | null;
}

export interface RequestItem {
  id: string;
  request_ref: string;
  model: string;
  provider: string | null;
  status: string;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  cost_kopecks: number;
  price_version: number | null;
  created_at: string;
}

export interface ReconciliationView {
  id: string;
  request_ref: string;
  kind: string;
  payload: Record<string, unknown>;
  created_at: string;
}
