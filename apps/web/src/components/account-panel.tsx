"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import {
  ApiClientError,
  apiFetch,
  formatDate,
  formatNumber,
  formatRub,
  type CatalogItem,
  type KeyItem,
  type LedgerItem,
  type ReconciliationView,
  type Viewer,
  type WalletState,
} from "@/lib/api";
import "./panel.css";

const LEDGER_LABELS: Record<string, string> = {
  topup: "Пополнение",
  usage: "Расход",
  adjustment: "Корректировка",
  refund: "Возврат",
};

export function AccountPanel() {
  const router = useRouter();
  const [viewer, setViewer] = useState<Viewer | null>(null);
  const [wallet, setWallet] = useState<WalletState | null>(null);
  const [keys, setKeys] = useState<KeyItem[]>([]);
  const [ledger, setLedger] = useState<LedgerItem[]>([]);
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [reconciliation, setReconciliation] = useState<ReconciliationView[]>([]);
  const [syncReport, setSyncReport] = useState<string | null>(null);
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const [keyName, setKeyName] = useState("");
  const [keyLimit, setKeyLimit] = useState("");
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    const me = await apiFetch<{ user: Viewer }>("/api/auth/me");
    setViewer(me.user);
    const [walletData, keysData, ledgerData, catalogData] = await Promise.all([
      apiFetch<{ wallet: WalletState }>("/api/wallet"),
      apiFetch<{ items: KeyItem[] }>("/api/keys"),
      apiFetch<{ items: LedgerItem[] }>("/api/wallet/ledger"),
      apiFetch<{ items: CatalogItem[] }>("/api/catalog"),
    ]);
    setWallet(walletData.wallet);
    setKeys(keysData.items);
    setLedger(ledgerData.items);
    setCatalog(catalogData.items.filter((item) => item.available));
    if (me.user.role === "admin") {
      const queue = await apiFetch<{ items: ReconciliationView[] }>("/api/admin/reconciliation");
      setReconciliation(queue.items);
    }
  }, []);

  useEffect(() => {
    load().catch((err: unknown) => {
      if (err instanceof ApiClientError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err instanceof Error ? err.message : "Не удалось загрузить кабинет.");
    });
  }, [load, router]);

  async function guard(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
    } catch (err) {
      setError(err instanceof ApiClientError || err instanceof Error ? err.message : "Ошибка запроса.");
    } finally {
      setBusy(false);
    }
  }

  const createKey = () =>
    guard(async () => {
      const limit = keyLimit.trim() ? Math.round(Number(keyLimit) * 100) : null;
      if (limit !== null && (!Number.isFinite(limit) || limit < 0)) throw new Error("Лимит указан неверно.");
      const data = await apiFetch<{ key: string; item: KeyItem }>("/api/keys", {
        method: "POST",
        body: JSON.stringify({ name: keyName.trim() || "Ключ", monthly_limit_kopecks: limit }),
      });
      setFreshKey(data.key);
      setKeyName("");
      setKeyLimit("");
      setNotice("Ключ создан. Скопируйте его сейчас — он показывается один раз.");
    });

  const revokeKey = (id: string) =>
    guard(async () => {
      await apiFetch(`/api/keys/${id}/revoke`, { method: "POST" });
      setNotice("Ключ отозван.");
    });

  const logout = () =>
    guard(async () => {
      await apiFetch("/api/auth/logout", { method: "POST" });
      router.push("/");
    });

  const syncCatalog = () =>
    guard(async () => {
      const report = await apiFetch<{ seen: number; created: number; repriced: number; unchanged: number; unavailable: number }>(
        "/api/admin/catalog/sync",
        { method: "POST" },
      );
      setSyncReport(
        `Проверено ${report.seen}: новых ${report.created}, переоценено ${report.repriced}, без изменений ${report.unchanged}, недоступно ${report.unavailable}.`,
      );
    });

  const visibleModels = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const items = needle
      ? catalog.filter(
          (item) => item.id.toLowerCase().includes(needle) || item.name.toLowerCase().includes(needle),
        )
      : catalog;
    return items.slice(0, 40);
  }, [catalog, filter]);

  if (!viewer) {
    return (
      <div className="rb-page">
        <div className="rb-page__wrap">
          <div className="rb-card rb-card--narrow">
            <p className="rb-muted">Загружаем кабинет…</p>
            {error ? <div className="rb-alert rb-alert--error">{error}</div> : null}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rb-page">
      <div className="rb-page__wrap">
        <nav className="rb-page__nav">
          <Link className="rb-page__brand" href="/">
            rubai
          </Link>
          <div className="rb-page__links">
            <span className="rb-muted">{viewer.email ?? viewer.display_name ?? "аккаунт"}</span>
            {viewer.email_verified ? (
              <span className="rb-badge rb-badge--ok">почта подтверждена</span>
            ) : (
              <span className="rb-badge rb-badge--warn">почта не подтверждена</span>
            )}
            <button className="rb-btn rb-btn--ghost rb-btn--sm" type="button" onClick={logout} disabled={busy}>
              Выйти
            </button>
          </div>
        </nav>

        {error ? <div className="rb-alert rb-alert--error">{error}</div> : null}
        {notice ? <div className="rb-alert rb-alert--ok">{notice}</div> : null}

        <section className="rb-card" aria-label="Баланс">
          <h2>Баланс</h2>
          <div className="rb-grid">
            <div className="rb-stat">
              <div className="rb-stat__label">Всего</div>
              <div className="rb-stat__value">{wallet ? formatRub(wallet.balance_kopecks) : "—"}</div>
            </div>
            <div className="rb-stat">
              <div className="rb-stat__label">Доступно</div>
              <div className="rb-stat__value">{wallet ? formatRub(wallet.available_kopecks) : "—"}</div>
            </div>
            <div className="rb-stat">
              <div className="rb-stat__label">В резерве</div>
              <div className="rb-stat__value">{wallet ? formatRub(wallet.reserved_kopecks) : "—"}</div>
            </div>
          </div>
          <p className="rb-muted" style={{ marginTop: 12 }}>
            Списания идут только после фактического ответа модели; ошибки провайдера возвращают резерв. Пополнение —
            тестовое (песочница).
          </p>
        </section>

        <section className="rb-card" aria-label="API-ключи">
          <h2>API-ключи</h2>
          {freshKey ? (
            <>
              <p className="rb-muted">Новый ключ — единственный раз, когда он виден целиком:</p>
              <div className="rb-key">{freshKey}</div>
              <div className="rb-actions">
                <button
                  className="rb-btn rb-btn--sm"
                  type="button"
                  onClick={() => {
                    navigator.clipboard?.writeText(freshKey).catch(() => undefined);
                    setNotice("Ключ скопирован.");
                  }}
                >
                  Скопировать
                </button>
                <button className="rb-btn rb-btn--ghost rb-btn--sm" type="button" onClick={() => setFreshKey(null)}>
                  Скрыть
                </button>
              </div>
            </>
          ) : null}

          <div className="rb-grid" style={{ marginTop: 12, marginBottom: 12 }}>
            <div className="rb-field">
              <label htmlFor="key-name">Название ключа</label>
              <input id="key-name" value={keyName} onChange={(event) => setKeyName(event.target.value)} placeholder="Cursor" />
            </div>
            <div className="rb-field">
              <label htmlFor="key-limit">Лимит в месяц, ₽ (необязательно)</label>
              <input
                id="key-limit"
                value={keyLimit}
                onChange={(event) => setKeyLimit(event.target.value)}
                inputMode="decimal"
                placeholder="3000"
              />
            </div>
          </div>
          <div className="rb-actions">
            <button className="rb-btn" type="button" onClick={createKey} disabled={busy || !viewer.email_verified}>
              Создать ключ
            </button>
            {!viewer.email_verified ? <span className="rb-muted">Сначала подтвердите почту.</span> : null}
          </div>

          <div className="rb-table-wrap">
          <table className="rb-table" style={{ marginTop: 16 }}>
            <thead>
              <tr>
                <th>Ключ</th>
                <th>Название</th>
                <th>Лимит</th>
                <th>Создан</th>
                <th>Статус</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {keys.map((item) => (
                <tr key={item.id}>
                  <td className="rb-table__num">{item.prefix}…</td>
                  <td>{item.name}</td>
                  <td className="rb-table__num">
                    {item.monthly_limit_kopecks === null ? "—" : formatRub(item.monthly_limit_kopecks)}
                  </td>
                  <td>{formatDate(item.created_at)}</td>
                  <td>
                    {item.revoked_at ? (
                      <span className="rb-badge rb-badge--warn">отозван</span>
                    ) : (
                      <span className="rb-badge rb-badge--ok">активен</span>
                    )}
                  </td>
                  <td>
                    {item.revoked_at ? null : (
                      <button
                        className="rb-btn rb-btn--danger rb-btn--sm"
                        type="button"
                        onClick={() => revokeKey(item.id)}
                        disabled={busy}
                      >
                        Отозвать
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {keys.length === 0 ? (
                <tr>
                  <td colSpan={6} className="rb-muted">
                    Ключей пока нет.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
          </div>
        </section>

        <section className="rb-card" aria-label="История">
          <h2>История операций</h2>
          <div className="rb-table-wrap">
          <table className="rb-table">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Операция</th>
                <th>Сумма</th>
                <th>Комментарий</th>
              </tr>
            </thead>
            <tbody>
              {ledger.map((item) => (
                <tr key={item.transaction_id}>
                  <td>{formatDate(item.created_at)}</td>
                  <td>{LEDGER_LABELS[item.kind] ?? item.kind}</td>
                  <td className="rb-table__num">{formatRub(item.amount_kopecks)}</td>
                  <td className="rb-muted">{item.memo ?? "—"}</td>
                </tr>
              ))}
              {ledger.length === 0 ? (
                <tr>
                  <td colSpan={4} className="rb-muted">
                    Операций пока нет.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
          </div>
        </section>

        <section className="rb-card" aria-label="Каталог">
          <h2>Каталог моделей</h2>
          <div className="rb-field" style={{ maxWidth: 320 }}>
            <label htmlFor="model-filter">Поиск</label>
            <input
              id="model-filter"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="например, gemini"
            />
          </div>
          <div className="rb-table-wrap">
          <table className="rb-table">
            <thead>
              <tr>
                <th>Модель</th>
                <th>Контекст</th>
                <th>Вход, ₽/Мток</th>
                <th>Выход, ₽/Мток</th>
                <th>Tools</th>
              </tr>
            </thead>
            <tbody>
              {visibleModels.map((model) => (
                <tr key={model.id}>
                  <td>
                    <div style={{ fontWeight: 700 }}>{model.name}</div>
                    <div className="rb-muted">{model.id}</div>
                  </td>
                  <td className="rb-table__num">{formatNumber(model.context_length)}</td>
                  <td className="rb-table__num">{model.pricing ? formatNumber(model.pricing.input_rub_per_mtok) : "—"}</td>
                  <td className="rb-table__num">{model.pricing ? formatNumber(model.pricing.output_rub_per_mtok) : "—"}</td>
                  <td>{model.supports_tools ? "да" : "нет"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          <p className="rb-muted">Показаны первые 40 моделей; уточните поиск, чтобы найти нужную.</p>
        </section>

        {viewer.role === "admin" ? (
          <section className="rb-card" aria-label="Администрирование">
            <h2>Администрирование</h2>
            <div className="rb-actions">
              <button className="rb-btn" type="button" onClick={syncCatalog} disabled={busy}>
                Синхронизировать каталог
              </button>
            </div>
            {syncReport ? <div className="rb-alert rb-alert--ok">{syncReport}</div> : null}

            <h2 style={{ marginTop: 20 }}>Сверка ({reconciliation.length})</h2>
            <div className="rb-table-wrap">
            <table className="rb-table">
              <thead>
                <tr>
                  <th>Дата</th>
                  <th>Запрос</th>
                  <th>Причина</th>
                  <th>Данные</th>
                </tr>
              </thead>
              <tbody>
                {reconciliation.map((item) => (
                  <tr key={item.id}>
                    <td>{formatDate(item.created_at)}</td>
                    <td className="rb-table__num">{item.request_ref.slice(0, 12)}…</td>
                    <td>{item.kind}</td>
                    <td className="rb-muted">{JSON.stringify(item.payload)}</td>
                  </tr>
                ))}
                {reconciliation.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="rb-muted">
                      Открытых расхождений нет.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
            </div>
          </section>
        ) : null}

        <footer className="rb-footer">
          <span>rubai · AI API Platform</span>
          <span className="rb-muted">
            Ключ показывается один раз · лимиты действуют на каждый ключ · тарифы фиксируются версиями
          </span>
        </footer>
      </div>
    </div>
  );
}
