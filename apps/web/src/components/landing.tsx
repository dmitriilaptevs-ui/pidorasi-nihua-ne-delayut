"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { apiFetch, formatNumber, type CatalogItem } from "@/lib/api";
import "./panel.css";

const FEATURES = [
  {
    title: "Один ключ вместо многих",
    text: "Выберите оплату с баланса платформы или сохраните свой ключ OpenRouter для BYOK запросов.",
  },
  {
    title: "Выбор источника оплаты",
    text: "Создайте API-ключ с балансом платформы или выберите оплату своим ключом OpenRouter.",
  },
  {
    title: "OpenAI-совместимый API",
    text: "Подключите привычный SDK, указав адрес платформы: /v1/models, /v1/chat/completions, потоковые ответы и вызов инструментов.",
  },
  {
    title: "Лимиты и история",
    text: "Задайте месячный лимит каждому ключу и следите за расходом: журнал операций и стоимость каждого запроса видны в кабинете.",
  },
  {
    title: "Hermes и Pi в браузере",
    text: "Запускайте текстовые задачи в веб-агентах с моделями каталога или настройте агента на своём компьютере.",
  },
];

const STEPS = [
  { title: "Зарегистрируйтесь", text: "Почта и пароль, VK ID или Яндекс ID. Подтвердите адрес — и кабинет открыт." },
  { title: "Создайте ключ", text: "Ключ показывается один раз. Назначьте лимит на месяц, если нужно." },
  { title: "Подключите SDK", text: "Укажите базовый URL платформы и ключ: клиент работает как с OpenAI." },
];

export function Landing() {
  const [models, setModels] = useState<CatalogItem[]>([]);
  const [signedIn, setSignedIn] = useState(false);
  const [catalogState, setCatalogState] = useState<"loading" | "ready" | "error">("loading");

  const loadModels = useCallback(() => {
    setCatalogState("loading");
    apiFetch<{ items: CatalogItem[] }>("/api/catalog?limit=200&sort=price")
      .then((data) => {
        setModels(
          data.items
            .filter((item) => item.available && item.pricing)
            .slice(0, 6),
        );
        setCatalogState("ready");
      })
      .catch(() => setCatalogState("error"));
  }, []);

  useEffect(() => {
    loadModels();
    apiFetch<{ user: unknown }>("/api/auth/me")
      .then(() => setSignedIn(true))
      .catch(() => setSignedIn(false));
  }, [loadModels]);

  return (
    <div className="rb-page">
      <div className="rb-page__wrap">
        <nav className="rb-page__nav" aria-label="Основная навигация">
          <Link className="rb-page__brand" href="/">
            rubai
          </Link>
          <div className="rb-page__links">
            <a href="#features">Возможности</a>
            <a href="#models">Модели</a>
            {signedIn ? (
              <Link className="rb-btn rb-btn--sm" href="/account">
                Личный кабинет
              </Link>
            ) : (
              <>
                <Link className="rb-btn rb-btn--ghost rb-btn--sm" href="/login">
                  Войти
                </Link>
                <Link className="rb-btn rb-btn--sm" href="/register">
                  Начать
                </Link>
              </>
            )}
          </div>
        </nav>

        <section className="rb-card">
          <p className="rb-muted">AI API Platform</p>
          <h1>Доступ к моделям OpenRouter и веб-агентам</h1>
          <p>
            Подключайте модели OpenRouter через OpenAI-совместимый API. Выбирайте оплату с баланса платформы или
            используйте собственный ключ провайдера. В личном кабинете доступны веб-агенты Hermes и Pi.
          </p>
          <div className="rb-actions">
            <Link className="rb-btn" href={signedIn ? "/account" : "/register"}>
              {signedIn ? "Открыть кабинет" : "Создать аккаунт"}
            </Link>
            <a className="rb-btn rb-btn--ghost" href="#models">
              Смотреть каталог
            </a>
          </div>
          <p className="rb-muted" style={{ marginTop: 14 }}>
            OpenAI-совместимый API · Поддержка собственного ключа провайдера · Веб-агенты
          </p>
        </section>

        <section id="features" className="rb-card">
          <h2>Что внутри</h2>
          <div className="rb-grid">
            {FEATURES.map((feature) => (
              <article className="rb-stat" key={feature.title}>
                <h3 className="rb-feature__title">{feature.title}</h3>
                <p className="rb-muted" style={{ marginTop: 8 }}>
                  {feature.text}
                </p>
              </article>
            ))}
          </div>
        </section>

        <section id="models" className="rb-card">
          <h2>Каталог моделей</h2>
          <p className="rb-muted">
            Цены — за 1 миллион токенов, уже с наценкой платформы. Курс и наценка фиксируются в версии тарифа: прошлые
            списания не меняются.
          </p>
          <div className="rb-models" style={{ marginTop: 14 }}>
            {catalogState === "loading" ? <p className="rb-muted">Загружаем каталог…</p> : null}
            {catalogState === "error" ? (
              <div>
                <p className="rb-muted">Не удалось загрузить каталог.</p>
                <button className="rb-btn rb-btn--sm" type="button" onClick={loadModels}>
                  Повторить
                </button>
              </div>
            ) : null}
            {catalogState === "ready"
              ? models.map((model) => (
                  <article className="rb-model" key={model.id}>
                    <div className="rb-model__name">{model.name}</div>
                    <div className="rb-muted">{model.provider}</div>
                    <div className="rb-model__price">
                      вход {formatNumber(model.pricing!.input_rub_per_mtok)} ₽ за 1 млн токенов
                      <br />
                      выход {formatNumber(model.pricing!.output_rub_per_mtok)} ₽ за 1 млн токенов
                    </div>
                    <div className="rb-muted" style={{ marginTop: 6 }}>
                      {formatNumber(model.context_length)} токенов контекста
                      {model.supports_tools ? " · вызов инструментов" : ""}
                    </div>
                  </article>
                ))
              : null}
            {catalogState === "ready" && models.length === 0 ? (
              <p className="rb-muted">Каталог пока пуст. Загляните чуть позже.</p>
            ) : null}
          </div>
        </section>

        <section className="rb-card">
          <h2>Как начать</h2>
          <div className="rb-steps">
            {STEPS.map((step) => (
              <article className="rb-step" key={step.title}>
                <div style={{ fontWeight: 800, marginBottom: 6 }}>{step.title}</div>
                <p className="rb-muted">{step.text}</p>
              </article>
            ))}
          </div>
          <div className="rb-actions">
            <Link className="rb-btn" href="/register">
              Создать аккаунт
            </Link>
          </div>
        </section>

        <footer className="rb-footer">
          <span>rubai · AI API Platform</span>
          <span>
            <Link href="/login">Вход</Link> · <Link href="/register">Регистрация</Link>
          </span>
        </footer>
      </div>
    </div>
  );
}
