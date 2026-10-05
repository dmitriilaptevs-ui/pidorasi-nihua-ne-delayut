"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { apiFetch, formatNumber, type CatalogItem } from "@/lib/api";
import "./panel.css";

const FEATURES = [
  {
    title: "Один ключ платформы",
    text: "Ваш ключ sk-rubai-… работает со всеми моделями каталога. Ключ OpenRouter остаётся на сервере и никогда не попадает к клиенту.",
  },
  {
    title: "Оплата в рублях",
    text: "Баланс — в рублях и копейках. Тарифы пересчитываются из закупочных цен с наценкой платформы и фиксируются вместе с курсом.",
  },
  {
    title: "OpenAI-совместимый API",
    text: "Подключите любой SDK, указав base URL платформы: /v1/models, /v1/chat/completions, streaming и tools.",
  },
  {
    title: "Лимиты и история",
    text: "Месячные лимиты по каждому ключу, журнал запросов и расходов, резервы до запроса и сверка неопределённых затрат.",
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

  useEffect(() => {
    apiFetch<{ items: CatalogItem[] }>("/api/catalog")
      .then((data) =>
        setModels(
          data.items
            .filter((item) => item.available && item.pricing)
            .sort((a, b) => Number(a.pricing!.input_rub_per_mtok) - Number(b.pricing!.input_rub_per_mtok))
            .slice(0, 6),
        ),
      )
      .catch(() => setModels([]));
    apiFetch<{ user: unknown }>("/api/auth/me")
      .then(() => setSignedIn(true))
      .catch(() => setSignedIn(false));
  }, []);

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
          <h1>Доступ к зарубежным AI-моделям за рубли</h1>
          <p>
            Российские пользователи подключают OpenRouter-модели через единый OpenAI-совместимый API. Платформа
            принимает запросы внутри страны, маршрутизирует их к провайдеру и считает фактическую стоимость в рублях.
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
            Оплата в рублях · OpenAI-совместимый API · Без VPN · Данные аккаунта и баланса — на сервере платформы
          </p>
        </section>

        <section id="features" className="rb-card">
          <h2>Что внутри</h2>
          <div className="rb-grid">
            {FEATURES.map((feature) => (
              <article className="rb-stat" key={feature.title}>
                <div className="rb-stat__label">{feature.title}</div>
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
            Цены — в рублях за миллион токенов с учётом наценки платформы. Курс и наценка фиксируются в версии тарифа:
            прошлые списания не меняются.
          </p>
          <div className="rb-models" style={{ marginTop: 14 }}>
            {models.map((model) => (
              <article className="rb-model" key={model.id}>
                <div className="rb-model__name">{model.name}</div>
                <div className="rb-muted">{model.provider}</div>
                <div className="rb-model__price">
                  вход {formatNumber(model.pricing!.input_rub_per_mtok)} ₽/Мток
                  <br />
                  выход {formatNumber(model.pricing!.output_rub_per_mtok)} ₽/Мток
                </div>
                <div className="rb-muted" style={{ marginTop: 6 }}>
                  {formatNumber(model.context_length)} токенов контекста
                  {model.supports_tools ? " · tools" : ""}
                </div>
              </article>
            ))}
            {models.length === 0 ? <p className="rb-muted">Каталог обновляется. Загляните чуть позже.</p> : null}
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
