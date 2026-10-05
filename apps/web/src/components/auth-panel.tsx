"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { ApiClientError, apiFetch, type Viewer } from "@/lib/api";
import "./panel.css";

type Mode = "login" | "register" | "verify" | "reset";

const PROVIDERS = [
  { id: "vk", label: "VK ID" },
  { id: "yandex", label: "Яндекс ID" },
] as const;

export function AuthPanel({ mode, token = "" }: { mode: Mode; token?: string }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Verification/reset links are one-shot: exchange them on load.
  useEffect(() => {
    if (mode !== "verify") return;
    if (!token) {
      setError("В ссылке нет кода подтверждения. Откройте письмо и перейдите по ссылке целиком.");
      return;
    }
    setBusy(true);
    apiFetch<{ user: Viewer }>("/api/auth/verify-email", {
      method: "POST",
      body: JSON.stringify({ token }),
    })
      .then((data) => setNotice(`Адрес подтверждён: ${data.user.email ?? "аккаунт"}. Теперь можно войти.`))
      .catch((err: unknown) => setError(err instanceof ApiClientError ? err.message : "Ссылка недействительна."))
      .finally(() => setBusy(false));
  }, [mode, token]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "login") {
        await apiFetch("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
        router.push("/account");
        return;
      }
      if (mode === "register") {
        await apiFetch("/api/auth/register", { method: "POST", body: JSON.stringify({ email, password }) });
        setNotice("Аккаунт создан. Мы отправили письмо со ссылкой для подтверждения адреса.");
        setPassword("");
        return;
      }
      if (mode === "reset") {
        await apiFetch("/api/auth/password/reset", {
          method: "POST",
          body: JSON.stringify({ token, password: newPassword }),
        });
        setNotice("Пароль обновлён, все активные сессии завершены. Войдите заново.");
        return;
      }
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Не удалось выполнить запрос.");
    } finally {
      setBusy(false);
    }
  }

  async function forgot() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await apiFetch("/api/auth/password/forgot", { method: "POST", body: JSON.stringify({ email }) });
      setNotice("Если адрес зарегистрирован, письмо со ссылкой для сброса уже отправлено.");
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Не удалось отправить письмо.");
    } finally {
      setBusy(false);
    }
  }

  async function provider(id: string) {
    setBusy(true);
    setError(null);
    try {
      const data = await apiFetch<{ url: string }>("/api/auth/start", {
        method: "POST",
        body: JSON.stringify({ provider: id, variant: "canvas" }),
      });
      window.location.assign(data.url);
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Этот способ входа недоступен.");
      setBusy(false);
    }
  }

  const title =
    mode === "login" ? "Вход" : mode === "register" ? "Регистрация" : mode === "verify" ? "Подтверждение адреса" : "Новый пароль";

  return (
    <div className="rb-page">
      <div className="rb-page__wrap">
        <nav className="rb-page__nav">
          <Link className="rb-page__brand" href="/">
            rubai
          </Link>
          <div className="rb-page__links">
            {mode === "login" ? (
              <Link href="/register">Регистрация</Link>
            ) : (
              <Link href="/login">Вход</Link>
            )}
          </div>
        </nav>

        <div className="rb-card rb-card--narrow">
          <h1>{title}</h1>

          {error ? <div className="rb-alert rb-alert--error" role="alert">{error}</div> : null}
          {notice ? <div className="rb-alert rb-alert--ok" role="status">{notice}</div> : null}

          {mode === "verify" ? (
            <div className="rb-actions">
              <Link className="rb-btn" href="/login">
                Перейти ко входу
              </Link>
            </div>
          ) : (
            <form onSubmit={submit}>
              {mode !== "reset" ? (
                <div className="rb-field">
                  <label htmlFor="email">Электронная почта</label>
                  <input
                    id="email"
                    type="email"
                    autoComplete="email"
                    required
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                  />
                </div>
              ) : null}

              {mode === "reset" ? (
                <div className="rb-field">
                  <label htmlFor="new-password">Новый пароль</label>
                  <input
                    id="new-password"
                    type="password"
                    autoComplete="new-password"
                    minLength={10}
                    required
                    value={newPassword}
                    onChange={(event) => setNewPassword(event.target.value)}
                  />
                </div>
              ) : (
                <div className="rb-field">
                  <label htmlFor="password">Пароль</label>
                  <input
                    id="password"
                    type="password"
                    autoComplete={mode === "register" ? "new-password" : "current-password"}
                    minLength={10}
                    required
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                  {mode === "register" ? <span className="rb-muted">Минимум 10 символов.</span> : null}
                </div>
              )}

              <div className="rb-actions">
                <button className="rb-btn" type="submit" disabled={busy}>
                  {busy ? "Отправляем…" : mode === "login" ? "Войти" : mode === "register" ? "Создать аккаунт" : "Сохранить пароль"}
                </button>
                {mode === "login" ? (
                  <button
                    className="rb-btn rb-btn--ghost"
                    type="button"
                    onClick={forgot}
                    disabled={busy || !email}
                    title={email ? undefined : "Сначала укажите адрес электронной почты"}
                  >
                    Забыли пароль?
                  </button>
                ) : null}
              </div>
            </form>
          )}

          {mode === "login" || mode === "register" ? (
            <>
              <p className="rb-muted" style={{ marginTop: 18 }}>
                Или войдите через провайдера:
              </p>
              <div className="rb-actions">
                {PROVIDERS.map((providerItem) => (
                  <button
                    key={providerItem.id}
                    className="rb-btn rb-btn--ghost"
                    type="button"
                    disabled={busy}
                    onClick={() => provider(providerItem.id)}
                  >
                    {providerItem.label}
                  </button>
                ))}
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
