"use client";

import { useCallback, useState } from "react";
import {
  AlertCircle,
  Ban,
  Check,
  Copy,
  Info,
  Loader2,
  Lock,
  LogOut,
  Send,
  Sparkles,
} from "lucide-react";
import type {
  ExperienceProps,
  GenerationResult,
  IntegrationStatus,
  ModelOption,
  Viewer,
} from "../lib/contracts";

const MAX_PROMPT = 2000;

const SUGGESTIONS = [
  "Составь план изучения Python на 4 недели",
  "Перепиши этот текст короче и понятнее:",
  "Придумай 5 идей для названия небольшого проекта",
  "Объясни разницу между REST и GraphQL простыми словами",
];

export interface WorkspaceProps {
  sectionRef: React.RefObject<HTMLElement | null>;
  viewer: Viewer | null;
  status: IntegrationStatus;
  selectedModel: ModelOption | null;
  prompt: string;
  onPromptChange: ExperienceProps["onPromptChange"];
  consent: boolean;
  onConsentChange: ExperienceProps["onConsentChange"];
  onGenerate: () => void;
  onCancel: () => void;
  generating: boolean;
  result: GenerationResult | null;
  generationError: string | null;
  onSignOut: () => void;
  onOpenLogin: () => void;
}

function formatTokens(value: number | null): string | null {
  if (value === null || !Number.isFinite(value)) return null;
  return new Intl.NumberFormat("ru-RU").format(value);
}

export function Workspace({
  sectionRef,
  viewer,
  status,
  selectedModel,
  prompt,
  onPromptChange,
  consent,
  onConsentChange,
  onGenerate,
  onCancel,
  generating,
  result,
  generationError,
  onSignOut,
  onOpenLogin,
}: WorkspaceProps) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");

  const handleCopy = useCallback(async () => {
    if (!result?.text) return;
    let ok = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(result.text);
        ok = true;
      }
    } catch {
      ok = false;
    }
    if (!ok) {
      try {
        const area = document.createElement("textarea");
        area.value = result.text;
        area.setAttribute("readonly", "");
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        ok = document.execCommand("copy");
        document.body.removeChild(area);
      } catch {
        ok = false;
      }
    }
    setCopyState(ok ? "copied" : "error");
    window.setTimeout(() => setCopyState("idle"), 2500);
  }, [result]);

  const promptEmpty = prompt.trim().length === 0;
  const canGenerate =
    !!viewer &&
    consent &&
    !!selectedModel &&
    status.inference &&
    !promptEmpty &&
    !generating;

  let blockedReason: string | null = null;
  if (viewer && !canGenerate && !generating) {
    if (!status.inference) {
      blockedReason =
        "Инференс не настроен: нет серверного ключа OpenRouter, отправка недоступна.";
    } else if (!selectedModel) {
      blockedReason = "Выберите бесплатную модель в каталоге выше.";
    } else if (promptEmpty) {
      blockedReason = "Введите текст запроса.";
    } else if (!consent) {
      blockedReason = "Подтвердите согласие на передачу текста провайдеру.";
    }
  }

  const inputTokens = formatTokens(result?.inputTokens ?? null);
  const outputTokens = formatTokens(result?.outputTokens ?? null);

  return (
    <section
      id="workspace"
      ref={sectionRef}
      className="rb-section rb-workspace"
      aria-labelledby="workspace-title"
      tabIndex={-1}
    >
      <div className="rb-section__head rb-section__head--row">
        <div>
          <p className="rb-eyebrow">
            <Sparkles size={14} aria-hidden />
            Рабочее поле
          </p>
          <h2 id="workspace-title" className="rb-h2">
            Первый запрос
          </h2>
          <p className="rb-section__sub">
            Текст уходит на сервер приложения, затем в OpenRouter и выбранному
            провайдеру модели. Не отправляйте персональные данные и секреты.
          </p>
        </div>
        {viewer ? (
          <button type="button" className="rb-ghost" onClick={onSignOut}>
            <LogOut size={16} aria-hidden />
            Выйти
          </button>
        ) : null}
      </div>

      {!viewer ? (
        <div className="rb-gate">
          <Lock size={28} aria-hidden />
          <h3 className="rb-gate__title">Сначала войдите</h3>
          <p className="rb-gate__text">
            Отправка запроса доступна только авторизованным пользователям.
            Войдите через VK ID или Яндекс ID — это займёт пару шагов.
          </p>
          <div className="rb-gate__row">
            <button type="button" className="rb-cta" onClick={onOpenLogin}>
              Войти и продолжить
            </button>
            <a className="rb-link" href="#models">
              Сначала выбрать модель
            </a>
          </div>
        </div>
      ) : (
        <div className="rb-workspace__body">
          {selectedModel ? (
            <p className="rb-selected">
              <Check size={16} aria-hidden />
              Модель: <strong>{selectedModel.name}</strong>
              <span className="rb-selected__provider">{selectedModel.provider}</span>
            </p>
          ) : (
            <p className="rb-selected rb-selected--empty">
              <AlertCircle size={16} aria-hidden />
              Модель не выбрана. Откройте каталог выше и выберите бесплатную
              модель.
            </p>
          )}

          <label className="rb-label" htmlFor="prompt">
            Текст запроса
          </label>
          <textarea
            id="prompt"
            className="rb-textarea"
            value={prompt}
            maxLength={MAX_PROMPT}
            rows={6}
            placeholder="Напишите, что нужно сделать. Например: «Составь план…»"
            onChange={(event) => onPromptChange(event.target.value)}
            disabled={generating}
          />
          <div className="rb-counter">
            {prompt.length} / {MAX_PROMPT}
          </div>

          <div className="rb-chips" aria-label="Подсказки для запроса">
            {SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                className="rb-chip-btn"
                onClick={() => onPromptChange(suggestion)}
                disabled={generating}
              >
                {suggestion}
              </button>
            ))}
          </div>

          <label className="rb-consent">
            <input
              type="checkbox"
              checked={consent}
              onChange={(event) => onConsentChange(event.target.checked)}
              disabled={generating}
            />
            <span>
              Я понимаю, что текст запроса будет передан через сервер
              приложения в OpenRouter и далее выбранному провайдеру модели. Я не
              отправляю персональные данные, пароли и секреты.
            </span>
          </label>

          <div className="rb-actions">
            <button
              type="button"
              className="rb-cta rb-cta--send"
              onClick={onGenerate}
              disabled={!canGenerate}
            >
              {generating ? (
                <Loader2 className="rb-spin" size={18} aria-hidden />
              ) : (
                <Send size={18} aria-hidden />
              )}
              {generating ? "Отправляем…" : "Отправить запрос"}
            </button>
            {generating ? (
              <button type="button" className="rb-ghost" onClick={onCancel}>
                <Ban size={16} aria-hidden />
                Отменить
              </button>
            ) : null}
          </div>

          {blockedReason ? <p className="rb-hint">{blockedReason}</p> : null}

          <div className="rb-result" aria-live="polite">
            {generating ? (
              <div className="rb-result__loading" role="status">
                <Loader2 className="rb-spin" size={20} aria-hidden />
                <span>
                  Запрос отправлен. Ждём ответ модели — это может занять
                  несколько секунд.
                </span>
              </div>
            ) : null}

            {!generating && generationError ? (
              <div className="rb-alert rb-alert--error" role="alert">
                <AlertCircle size={18} aria-hidden />
                <div>
                  <p className="rb-alert__title">Запрос не выполнен</p>
                  <p className="rb-alert__text">{generationError}</p>
                  <p className="rb-alert__text">
                    Платный fallback не используется. Можно повторить запрос
                    или выбрать другую модель.
                  </p>
                </div>
              </div>
            ) : null}

            {!generating && result ? (
              <article className="rb-result__card">
                <header className="rb-result__head">
                  <h3 className="rb-result__title">Ответ модели</h3>
                  <button
                    type="button"
                    className="rb-ghost"
                    onClick={handleCopy}
                    aria-live="polite"
                  >
                    {copyState === "copied" ? (
                      <Check size={16} aria-hidden />
                    ) : (
                      <Copy size={16} aria-hidden />
                    )}
                    {copyState === "copied"
                      ? "Скопировано"
                      : copyState === "error"
                        ? "Скопируйте вручную"
                        : "Копировать"}
                  </button>
                </header>
                <p className="rb-result__text">{result.text}</p>
                <dl className="rb-result__meta">
                  <div>
                    <dt>Модель</dt>
                    <dd>{result.model}</dd>
                  </div>
                  <div>
                    <dt>Request ID</dt>
                    <dd>
                      <code>{result.requestId}</code>
                    </dd>
                  </div>
                  {inputTokens ? (
                    <div>
                      <dt>Входные токены</dt>
                      <dd>{inputTokens}</dd>
                    </div>
                  ) : null}
                  {outputTokens ? (
                    <div>
                      <dt>Выходные токены</dt>
                      <dd>{outputTokens}</dd>
                    </div>
                  ) : null}
                </dl>
              </article>
            ) : null}

            {!generating && !result && !generationError ? (
              <p className="rb-result__empty">
                Здесь появится настоящий ответ выбранной модели. До отправки
                поле пустое.
              </p>
            ) : null}
          </div>

          <div className="rb-disclosure">
            <Info size={16} aria-hidden />
            <p>
              Модели <code>:free</code> имеют лимиты запросов и не дают
              гарантий SLA. Каталог и квоты могут меняться. Платный fallback
              выключен: если бесплатная модель недоступна, запрос завершится
              ошибкой, а не списанием денег.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
