"use client";

import { useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  AlertCircle,
  Check,
  ChevronDown,
  Cpu,
  Loader2,
  RefreshCw,
} from "lucide-react";
import type { ModelOption } from "../lib/contracts";

const INITIAL_VISIBLE = 6;

export interface ModelPickerProps {
  models: ModelOption[];
  modelsLoading: boolean;
  modelsError: string | null;
  selectedModel: string;
  onSelectModel: (id: string) => void;
  onRefreshModels: () => void;
  inference: boolean;
}

function formatContext(length: number): string {
  if (!Number.isFinite(length) || length <= 0) return "контекст не указан";
  if (length >= 1000) {
    const k = Math.round(length / 1000);
    return `${new Intl.NumberFormat("ru-RU").format(k)}K токенов`;
  }
  return `${new Intl.NumberFormat("ru-RU").format(length)} токенов`;
}

export function ModelPicker({
  models,
  modelsLoading,
  modelsError,
  selectedModel,
  onSelectModel,
  onRefreshModels,
  inference,
}: ModelPickerProps) {
  const hasModels = models.length > 0;
  const [expanded, setExpanded] = useState(false);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // Collapsed: first six real models, plus the selected one if it falls
  // outside that window so the current choice is never hidden.
  const visible = useMemo(() => {
    if (expanded || models.length <= INITIAL_VISIBLE) return models;
    const head = models.slice(0, INITIAL_VISIBLE);
    if (selectedModel && !head.some((model) => model.id === selectedModel)) {
      const selected = models.find((model) => model.id === selectedModel);
      if (selected) return [...head, selected];
    }
    return head;
  }, [expanded, models, selectedModel]);

  const selectedIndex = visible.findIndex((model) => model.id === selectedModel);

  function handleKeyDown(
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) {
    const key = event.key;
    let next: number;
    if (key === "ArrowRight" || key === "ArrowDown") {
      next = (index + 1) % visible.length;
    } else if (key === "ArrowLeft" || key === "ArrowUp") {
      next = (index - 1 + visible.length) % visible.length;
    } else if (key === "Home") {
      next = 0;
    } else if (key === "End") {
      next = visible.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    const target = visible[next];
    if (!target) return;
    onSelectModel(target.id);
    itemRefs.current[next]?.focus();
  }

  return (
    <section id="models" className="rb-section rb-models" aria-labelledby="models-title">
      <div className="rb-section__head rb-section__head--row">
        <div>
          <p className="rb-eyebrow">
            <Cpu size={14} aria-hidden />
            Каталог
          </p>
          <h2 id="models-title" className="rb-h2">
            Бесплатные модели
          </h2>
          <p className="rb-section__sub">
            Все модели здесь бесплатные. Выберите ту, что подойдёт для задачи —
            она подставится в поле ниже.
          </p>
        </div>
        <button
          type="button"
          className="rb-ghost rb-refresh"
          onClick={onRefreshModels}
          disabled={modelsLoading}
        >
          {modelsLoading ? (
            <Loader2 className="rb-spin" size={16} aria-hidden />
          ) : (
            <RefreshCw size={16} aria-hidden />
          )}
          Обновить
        </button>
      </div>

      {!inference ? (
        <p className="rb-alert rb-alert--warn">
          <AlertCircle size={16} aria-hidden />
          <span>
            Инференс не настроен: на сервере нет ключа OpenRouter. Список
            моделей может загружаться, но отправка запроса недоступна.
          </span>
        </p>
      ) : null}

      {modelsLoading ? (
        <div className="rb-models__grid" aria-busy="true" aria-live="polite">
          {[0, 1, 2].map((i) => (
            <div key={i} className="rb-model rb-model--skeleton" aria-hidden>
              <span className="rb-skeleton rb-skeleton--title" />
              <span className="rb-skeleton rb-skeleton--line" />
              <span className="rb-skeleton rb-skeleton--line rb-skeleton--short" />
            </div>
          ))}
          <span className="rb-visually-hidden">Загружаем каталог моделей…</span>
        </div>
      ) : null}

      {!modelsLoading && modelsError ? (
        <div className="rb-alert rb-alert--error" role="alert">
          <AlertCircle size={18} aria-hidden />
          <div>
            <p className="rb-alert__title">Не удалось загрузить каталог</p>
            <p className="rb-alert__text">{modelsError}</p>
            <button type="button" className="rb-ghost rb-refresh" onClick={onRefreshModels}>
              <RefreshCw size={16} aria-hidden />
              Повторить
            </button>
          </div>
        </div>
      ) : null}

      {!modelsLoading && !modelsError && !hasModels ? (
        <div className="rb-alert rb-alert--warn" role="status">
          <AlertCircle size={18} aria-hidden />
          <div>
            <p className="rb-alert__title">Каталог пуст</p>
            <p className="rb-alert__text">
              Сервер не вернул ни одной бесплатной модели. Тарифы и каталог
              меняются, поэтому обновите список позже.
            </p>
            <button type="button" className="rb-ghost rb-refresh" onClick={onRefreshModels}>
              <RefreshCw size={16} aria-hidden />
              Обновить
            </button>
          </div>
        </div>
      ) : null}

      {!modelsLoading && !modelsError && hasModels ? (
        <>
          <div className="rb-models__grid" role="radiogroup" aria-label="Выбор модели">
            {visible.map((model, index) => {
              const active = model.id === selectedModel;
              const tabbable = active || (selectedIndex < 0 && index === 0);
              return (
                <button
                  key={model.id}
                  ref={(element) => {
                    itemRefs.current[index] = element;
                  }}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  tabIndex={tabbable ? 0 : -1}
                  className={`rb-model${active ? " rb-model--active" : ""}`}
                  onClick={() => onSelectModel(model.id)}
                  onKeyDown={(event) => handleKeyDown(event, index)}
                >
                  <span className="rb-model__top">
                    <span className="rb-model__name">{model.name}</span>
                    {active ? (
                      <span className="rb-model__check" aria-hidden>
                        <Check size={14} />
                      </span>
                    ) : null}
                  </span>
                  <span className="rb-model__provider">{model.provider}</span>
                  <span className="rb-model__foot">
                    <span className="rb-model__meta">
                      {formatContext(model.contextLength)}
                    </span>
                    <span className="rb-model__price">0 ₽</span>
                  </span>
                </button>
              );
            })}
          </div>

          {models.length > INITIAL_VISIBLE ? (
            <div className="rb-models__more">
              <button
                type="button"
                className="rb-ghost"
                onClick={() => setExpanded((value) => !value)}
                aria-expanded={expanded}
              >
                <ChevronDown
                  className={`rb-more__chevron${expanded ? " rb-more__chevron--up" : ""}`}
                  size={16}
                  aria-hidden
                />
                {expanded ? "Свернуть" : `Все модели (${models.length})`}
              </button>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
