"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { AuthProvider, GenerationResult, IntegrationStatus, ModelOption, Variant, Viewer } from "@/lib/contracts";
import { Experience } from "./experience";

const EMPTY_STATUS: IntegrationStatus = { providers: { vk: false, yandex: false }, inference: false };
const AUTH_ERRORS: Record<string, string> = {
  state: "Время ожидания входа истекло или проверка безопасности не прошла. Начните вход заново.",
  denied: "Вы отменили вход или провайдер отказал в доступе. Можно попробовать другой способ.",
  invalid_request: "Сервис входа вернул неполные данные. Проверьте callback URL и начните вход заново.",
  invalid_response: "Не удалось подтвердить аккаунт по ответу провайдера. Проверьте настройки OAuth-приложения.",
  provider: "Сервис входа временно недоступен или отклонил обмен кода. Проверьте настройки приложения.",
  unavailable: "Этот способ входа ещё не настроен. Проверьте локальную конфигурацию.",
  access_denied: "Вы отменили вход. Можно выбрать другой способ или попробовать снова.",
  provider_denied: "Вход отменён или не разрешён провайдером.",
  invalid_state: "Время ожидания входа истекло или проверка безопасности не прошла. Начните вход заново.",
  invalid_callback: "Не удалось проверить ответ сервиса входа. Начните вход заново.",
  auth_failed: "Вход не завершён. Проверьте настройки приложения и попробуйте снова.",
  provider_unavailable: "Сервис входа временно недоступен. Попробуйте ещё раз.",
  provider_not_configured: "Этот способ входа ещё не настроен на сервере.",
};

class ClientApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, cache: "no-store", credentials: "same-origin" });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new ClientApiError(payload?.error?.message || "Не удалось выполнить запрос. Попробуйте ещё раз.", response.status);
  return payload as T;
}

function saveModel(id: string) {
  try { window.sessionStorage.setItem("rubai:selected-model", id); } catch { /* storage is optional */ }
}

export function Onboarding({ variant }: { variant: Variant }) {
  const [viewer, setViewer] = useState<Viewer | null>(null);
  const [status, setStatus] = useState<IntegrationStatus>(EMPTY_STATUS);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState("");
  const [authPending, setAuthPending] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [consent, setConsent] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<GenerationResult | null>(null);
  const [generationError, setGenerationError] = useState<string | null>(null);
  const pendingGeneration = useRef<AbortController | null>(null);
  const pendingCatalog = useRef<AbortController | null>(null);

  const refreshModels = useCallback(async () => {
    pendingCatalog.current?.abort();
    const controller = new AbortController();
    pendingCatalog.current = controller;
    setModelsLoading(true);
    setModelsError(null);
    try {
      const { models: fresh } = await jsonFetch<{ models: ModelOption[] }>("/api/models", { signal: controller.signal });
      if (controller.signal.aborted) return;
      setModels(fresh);
      setSelectedModel((previous) => {
        let remembered = "";
        try { remembered = window.sessionStorage.getItem("rubai:selected-model") || ""; } catch { /* optional */ }
        const chosen = [previous, remembered].find((id) => fresh.some((model) => model.id === id)) || fresh[0]?.id || "";
        if (chosen) saveModel(chosen);
        return chosen;
      });
    } catch (error) {
      if (controller.signal.aborted) return;
      setModels([]);
      setSelectedModel("");
      setModelsError(error instanceof Error ? error.message : "Не удалось загрузить модели.");
    } finally {
      if (!controller.signal.aborted) setModelsLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const query = new URLSearchParams(window.location.search);
    const errorCode = query.get("auth_error");
    if (errorCode) {
      setAuthError(AUTH_ERRORS[errorCode] || "Вход не завершён. Попробуйте снова или проверьте настройки OAuth-приложения.");
      window.history.replaceState({}, "", `/${variant}${window.location.hash}`);
    }
    jsonFetch<{ user: { email: string; signup_method: string } }>("/api/auth/me", { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setViewer({ name: data.user.email, provider: (data.user.signup_method as Viewer["provider"]) }); })
      .catch((error) => {
        if (controller.signal.aborted) return;
        // No session is a normal state, not an error banner.
        if (error instanceof ClientApiError && error.status === 401) { setViewer(null); return; }
        setAuthError("Не удалось проверить сессию. Обновите страницу.");
      });
    jsonFetch<IntegrationStatus>("/api/status", { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setStatus(data); })
      .catch(() => { if (!controller.signal.aborted) setAuthError("Не удалось проверить настройки интеграций. Обновите страницу."); });
    void refreshModels();
    return () => { controller.abort(); pendingCatalog.current?.abort(); pendingGeneration.current?.abort(); };
  }, [refreshModels, variant]);

  useEffect(() => {
    if (viewer && window.location.hash === "#workspace") {
      const frame = requestAnimationFrame(() => {
        document.getElementById("workspace")?.scrollIntoView({ behavior: "auto" });
        document.getElementById("prompt")?.focus({ preventScroll: true });
      });
      return () => cancelAnimationFrame(frame);
    }
  }, [viewer]);

  async function signIn(provider: AuthProvider) {
    if (authPending) return;
    setAuthPending(true);
    setAuthError(null);
    try {
      const { url } = await jsonFetch<{ url: string }>("/api/auth/start", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider, variant }),
      });
      const destination = new URL(url);
      if (destination.protocol !== "https:" || !["id.vk.ru", "oauth.yandex.ru"].includes(destination.hostname)) throw new Error("Некорректный адрес сервиса входа.");
      window.location.assign(destination.toString());
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "Не удалось начать вход.");
      setAuthPending(false);
    }
  }

  async function signOut() {
    if (authPending) return;
    setAuthPending(true);
    pendingGeneration.current?.abort();
    pendingGeneration.current = null;
    setGenerating(false);
    setResult(null);
    setPrompt("");
    setConsent(false);
    try {
      await jsonFetch("/api/auth/logout", { method: "POST" });
      setViewer(null);
      setGenerationError(null);
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "Не удалось завершить сессию.");
    } finally { setAuthPending(false); }
  }

  async function run() {
    if (pendingGeneration.current || !viewer || !consent || !selectedModel || !prompt.trim() || !status.inference) return;
    const controller = new AbortController();
    pendingGeneration.current = controller;
    setGenerating(true);
    setResult(null);
    setGenerationError(null);
    try {
      const data = await jsonFetch<GenerationResult>("/api/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: selectedModel, prompt, consent }), signal: controller.signal,
      });
      if (!controller.signal.aborted && pendingGeneration.current === controller) setResult(data);
    } catch (error) {
      if (!controller.signal.aborted) {
        setGenerationError(error instanceof Error ? error.message : "Не удалось получить ответ.");
        if (error instanceof ClientApiError && error.status === 401) {
          setViewer(null);
          setAuthError("Сессия завершилась. Войдите снова, чтобы продолжить.");
        }
      }
    } finally {
      if (pendingGeneration.current === controller) { pendingGeneration.current = null; setGenerating(false); }
    }
  }

  function cancel() {
    pendingGeneration.current?.abort();
    pendingGeneration.current = null;
    setGenerating(false);
    setGenerationError("Запрос остановлен. Провайдер мог учесть его в своей бесплатной квоте; повтор не отправлялся.");
  }

  return <Experience variant={variant} viewer={viewer} status={status} models={models}
    modelsLoading={modelsLoading} modelsError={modelsError} selectedModel={selectedModel}
    onSelectModel={(id) => { if (!generating) { setSelectedModel(id); saveModel(id); } }} onRefreshModels={() => void refreshModels()}
    onSignIn={(provider) => void signIn(provider)} onSignOut={() => void signOut()} authPending={authPending} authError={authError}
    prompt={prompt} onPromptChange={setPrompt} consent={consent} onConsentChange={setConsent}
    onGenerate={() => void run()} onCancel={cancel} generating={generating} result={result} generationError={generationError} />;
}
