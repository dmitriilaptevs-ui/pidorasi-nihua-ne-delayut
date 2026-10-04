"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, Lock, ShieldCheck, Sparkles, Zap } from "lucide-react";
import type { AuthProvider, ExperienceProps, Variant } from "../lib/contracts";
import { LoginDialog } from "./login-dialog";
import { ModelPicker } from "./model-picker";
import { Workspace } from "./workspace";
import "./experience.css";

const PROVIDER_LABEL: Record<AuthProvider, string> = {
  vk: "VK ID",
  yandex: "Яндекс ID",
};

const VARIANT_LABEL: Record<Variant, string> = {
  flow: "Поток",
  pulse: "Импульс",
  canvas: "Холст",
};

function PrimaryCta({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="rb-cta rb-cta--xl" onClick={onClick}>
      <span>Начать бесплатно</span>
      <ArrowRight size={20} aria-hidden />
    </button>
  );
}

function DesignSelector({ variant }: { variant: Variant }) {
  const items: Variant[] = ["flow", "pulse", "canvas"];
  return (
    <nav className="rb-selector" aria-label="Выбор дизайна (инструмент ревью)">
      <span className="rb-selector__hint">Дизайн</span>
      {items.map((v) => (
        <a
          key={v}
          href={`/${v}`}
          className="rb-selector__item"
          aria-current={v === variant ? "page" : undefined}
          title={`Открыть дизайн «${VARIANT_LABEL[v]}»`}
        >
          {VARIANT_LABEL[v]}
        </a>
      ))}
    </nav>
  );
}

export function Experience(props: ExperienceProps) {
  const {
    variant,
    viewer,
    status,
    models,
    modelsLoading,
    modelsError,
    selectedModel,
    onSelectModel,
    onRefreshModels,
    onSignIn,
    onSignOut,
    authPending,
    authError,
    prompt,
    onPromptChange,
    consent,
    onConsentChange,
    onGenerate,
    onCancel,
    generating,
    result,
    generationError,
  } = props;

  const [loginOpen, setLoginOpen] = useState(false);
  const workspaceRef = useRef<HTMLElement | null>(null);

  const goToWorkspace = useCallback(() => {
    const el = workspaceRef.current;
    if (!el) return;
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
    const area = el.querySelector<HTMLTextAreaElement>("textarea");
    window.setTimeout(
      () => area?.focus({ preventScroll: true }),
      reduce ? 0 : 350,
    );
  }, []);

  const handleStart = useCallback(() => {
    if (viewer) goToWorkspace();
    else setLoginOpen(true);
  }, [viewer, goToWorkspace]);

  // Surface OAuth-return failures immediately instead of hiding them behind a
  // closed dialog. Re-open only when a new error arrives (or after it clears).
  const lastAuthError = useRef<string | null>(null);
  useEffect(() => {
    if (!authError) {
      lastAuthError.current = null;
      return;
    }
    if (authError !== lastAuthError.current) {
      lastAuthError.current = authError;
      setLoginOpen(true);
    }
  }, [authError]);

  const selectedModelOption = models.find((m) => m.id === selectedModel) ?? null;

  return (
    <div className="rb-root" data-variant={variant}>
      <a className="rb-skip" href="#workspace">
        Перейти к запросу
      </a>

      <header className="rb-nav">
        <div className="rb-nav__inner">
          <a className="rb-brand" href={`/${variant}`} aria-label="rubai — на главную">
            <span className="rb-brand__mark" aria-hidden>
              r
            </span>
            <span className="rb-brand__name">rubai</span>
          </a>

          <nav className="rb-nav__links" aria-label="Основная навигация">
            <a href="#how">Как это работает</a>
            <a href="#models">Модели</a>
            <a href="#workspace">Запрос</a>
          </nav>

          <div className="rb-nav__actions">
            {viewer ? (
              <div className="rb-user">
                <span className="rb-user__name" title={viewer.name}>
                  {viewer.name}
                </span>
                <span className="rb-user__provider">
                  {PROVIDER_LABEL[viewer.provider]}
                </span>
                <button type="button" className="rb-ghost" onClick={onSignOut}>
                  Выйти
                </button>
              </div>
            ) : (
              <button
                type="button"
                className="rb-ghost"
                onClick={() => setLoginOpen(true)}
              >
                Войти
              </button>
            )}
            <button
              type="button"
              className="rb-cta rb-cta--sm"
              onClick={handleStart}
            >
              Начать бесплатно
            </button>
          </div>
        </div>
      </header>

      <main className="rb-main">
        {variant === "flow" ? (
          <section className="rb-hero rb-hero--flow" aria-labelledby="hero-title">
            <div className="rb-hero__copy">
              <p className="rb-eyebrow">
                <Sparkles size={14} aria-hidden />
                Первый запуск без карты
              </p>
              <h1 id="hero-title" className="rb-h1">
                От идеи —<br />
                к первому <span className="rb-accent">ответу.</span>
              </h1>
              <p className="rb-lede">
                Бесплатные AI-модели в одном месте. Войдите через привычный
                аккаунт, выберите модель — и просто спросите.
              </p>
              <div className="rb-cta-row">
                <PrimaryCta onClick={handleStart} />
                <a className="rb-link" href="#models">
                  Посмотреть модели
                  <ArrowRight size={16} aria-hidden />
                </a>
              </div>
              <ul className="rb-points">
                <li>
                  <ShieldCheck size={18} aria-hidden />
                  Войдите через VK ID или Яндекс ID
                </li>
                <li>
                  <Zap size={18} aria-hidden />
                  Бесплатные модели за 0 ₽
                </li>
                <li>
                  <Lock size={18} aria-hidden />
                  Отправка только с вашего согласия
                </li>
              </ul>
            </div>

            <div className="rb-hero__art rb-art-flow" aria-hidden="true">
              <div className="rb-sculpt rb-sculpt--a">
                <span className="rb-chip">Вход</span>
                <div className="rb-sculpt__row">
                  <span className="rb-sculpt__pill">VK ID</span>
                  <span className="rb-sculpt__pill">Яндекс ID</span>
                </div>
              </div>
              <div className="rb-sculpt rb-sculpt--b">
                <span className="rb-chip">Каталог :free</span>
                <span className="rb-sculpt__bar" />
                <span className="rb-sculpt__bar rb-sculpt__bar--short" />
                <span className="rb-sculpt__bar rb-sculpt__bar--mid" />
              </div>
              <div className="rb-sculpt rb-sculpt--c">
                <span className="rb-chip">Первый запрос</span>
                <span className="rb-sculpt__line" />
                <span className="rb-sculpt__line rb-sculpt__line--short" />
              </div>
            </div>
          </section>
        ) : null}

        {variant === "pulse" ? (
          <section className="rb-hero rb-hero--pulse" aria-labelledby="hero-title">
            <div className="rb-orbit" aria-hidden="true">
              <span className="rb-orbit__ring rb-orbit__ring--1" />
              <span className="rb-orbit__ring rb-orbit__ring--2" />
              <span className="rb-orbit__ring rb-orbit__ring--3" />
              <span className="rb-orbit__core" />
            </div>
            <p className="rb-eyebrow rb-eyebrow--center">
              <Sparkles size={14} aria-hidden />
              Бесплатный первый запуск
            </p>
            <h1 id="hero-title" className="rb-h1 rb-h1--xl">
              Ваша мысль.<br />
              Новый импульс.
            </h1>
            <p className="rb-lede rb-lede--center">
              Задайте вопрос — AI ответит. Войдите через привычный аккаунт и
              начните бесплатно.
            </p>
            <div className="rb-cta-row rb-cta-row--center">
              <PrimaryCta onClick={handleStart} />
            </div>
            <ol className="rb-path" aria-label="Путь к первому ответу">
              <li className="rb-path__item">
                <span className="rb-path__num">1</span>
                <span className="rb-path__label">Войдите</span>
              </li>
              <li className="rb-path__item">
                <span className="rb-path__num">2</span>
                <span className="rb-path__label">Выберите модель</span>
              </li>
              <li className="rb-path__item">
                <span className="rb-path__num">3</span>
                <span className="rb-path__label">Задайте вопрос</span>
              </li>
            </ol>
          </section>
        ) : null}

        {variant === "canvas" ? (
          <section className="rb-hero rb-hero--canvas" aria-labelledby="hero-title">
            <div className="rb-bento">
              <div className="rb-bento__tile rb-bento__tile--title">
                <p className="rb-eyebrow">
                  <Sparkles size={14} aria-hidden />
                  Место для ваших идей
                </p>
                <h1 id="hero-title" className="rb-h1">
                  AI. Просто.<br />
                  По делу.
                </h1>
                <p className="rb-lede">
                  Войдите через привычный аккаунт, выберите бесплатную модель и
                  задайте вопрос. Без карты и лишних шагов.
                </p>
                <PrimaryCta onClick={handleStart} />
              </div>
              <div className="rb-bento__tile rb-bento__tile--a">
                <span className="rb-bento__k">Стоимость</span>
                <span className="rb-bento__v">0 ₽</span>
              </div>
              <div className="rb-bento__tile rb-bento__tile--b">
                <span className="rb-bento__k">Оплата</span>
                <span className="rb-bento__v">Без карты</span>
              </div>
              <div className="rb-bento__tile rb-bento__tile--c">
                <span className="rb-bento__k">Модель</span>
                <span className="rb-bento__v">Вы выбираете</span>
              </div>
              <div className="rb-bento__tile rb-bento__tile--d">
                <span className="rb-bento__k">Вход</span>
                <span className="rb-bento__v">VK ID · Яндекс ID</span>
              </div>
              <div className="rb-bento__tile rb-bento__tile--e">
                <span className="rb-bento__k">Отправка</span>
                <span className="rb-bento__v">По согласию</span>
              </div>
            </div>
          </section>
        ) : null}

        <section id="how" className="rb-section rb-section--how">
          <div className="rb-section__head">
            <p className="rb-eyebrow">Как это работает</p>
            <h2 className="rb-h2">Три шага до первого ответа</h2>
          </div>
          <ol className="rb-steps">
            <li className="rb-step">
              <span className="rb-step__num">01</span>
              <h3 className="rb-step__title">Войдите</h3>
              <p className="rb-step__text">
                Через VK ID или Яндекс ID. Пароль вводится на стороне
                провайдера — приложение его не получает.
              </p>
            </li>
            <li className="rb-step">
              <span className="rb-step__num">02</span>
              <h3 className="rb-step__title">Выберите модель</h3>
              <p className="rb-step__text">
                Все доступные модели — бесплатные. Выберите подходящую и
                посмотрите размер контекста.
              </p>
            </li>
            <li className="rb-step">
              <span className="rb-step__num">03</span>
              <h3 className="rb-step__title">Отправьте запрос</h3>
              <p className="rb-step__text">
                Подтвердите согласие на передачу текста и отправьте запрос.
                Ответ появится прямо на странице.
              </p>
            </li>
          </ol>
        </section>

        <ModelPicker
          models={models}
          modelsLoading={modelsLoading}
          modelsError={modelsError}
          selectedModel={selectedModel}
          onSelectModel={onSelectModel}
          onRefreshModels={onRefreshModels}
          inference={status.inference}
        />

        <Workspace
          sectionRef={workspaceRef}
          viewer={viewer}
          status={status}
          selectedModel={selectedModelOption}
          prompt={prompt}
          onPromptChange={onPromptChange}
          consent={consent}
          onConsentChange={onConsentChange}
          onGenerate={onGenerate}
          onCancel={onCancel}
          generating={generating}
          result={result}
          generationError={generationError}
          onSignOut={onSignOut}
          onOpenLogin={() => setLoginOpen(true)}
        />
      </main>

      <footer className="rb-footer">
        <div className="rb-footer__inner">
          <div className="rb-footer__brand">
            <span className="rb-brand__mark" aria-hidden>
              r
            </span>
            <span>rubai</span>
          </div>
          <p className="rb-footer__note">
            Локальная итерация онбординга. Это не production-сборка: вход
            создаёт локальную сессию, а не постоянный аккаунт платформы.
          </p>
        </div>
      </footer>

      <DesignSelector variant={variant} />

      <LoginDialog
        open={loginOpen}
        onClose={() => setLoginOpen(false)}
        providers={status.providers}
        authPending={authPending}
        authError={authError}
        onSignIn={onSignIn}
      />
    </div>
  );
}
