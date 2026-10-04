"use client";

import { useCallback, useEffect, useRef } from "react";
import { AlertCircle, Loader2, ShieldCheck, X } from "lucide-react";
import type { AuthProvider, IntegrationStatus } from "../lib/contracts";

export interface LoginDialogProps {
  open: boolean;
  onClose: () => void;
  providers: IntegrationStatus["providers"];
  authPending: boolean;
  authError: string | null;
  onSignIn: (provider: AuthProvider) => void;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function LoginDialog({
  open,
  onClose,
  providers,
  authPending,
  authError,
  onSignIn,
}: LoginDialogProps) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      restoreRef.current = (document.activeElement as HTMLElement) ?? null;
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const previous = restoreRef.current;
    return () => {
      previous?.focus?.({ preventScroll: true });
    };
  }, [open]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!open) return;
    const handleCancel = (event: Event) => {
      event.preventDefault();
      onClose();
    };
    dialog.addEventListener("cancel", handleCancel);
    return () => dialog.removeEventListener("cancel", handleCancel);
  }, [open, onClose]);

  const handleBackdrop = useCallback(
    (event: React.MouseEvent<HTMLDialogElement>) => {
      if (event.target === dialogRef.current) onClose();
    },
    [onClose],
  );

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const nodes = Array.from(
        panel.querySelectorAll<HTMLElement>(FOCUSABLE),
      ).filter((node) => node.offsetParent !== null);
      if (nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    },
    [],
  );

  const bothMissing = !providers.vk && !providers.yandex;

  return (
    <dialog
      ref={dialogRef}
      className="rb-dialog"
      aria-labelledby="login-title"
      onClick={handleBackdrop}
      onClose={onClose}
    >
      <div
        className="rb-dialog__panel"
        ref={panelRef}
        onKeyDown={handleKeyDown}
      >
        <button
          type="button"
          className="rb-dialog__close"
          onClick={onClose}
          aria-label="Закрыть окно входа"
        >
          <X size={18} aria-hidden />
        </button>

        <p className="rb-eyebrow">
          <ShieldCheck size={14} aria-hidden />
          Вход
        </p>
        <h2 id="login-title" className="rb-dialog__title">
          Войдите, чтобы отправить запрос
        </h2>
        <p className="rb-dialog__text">
          Выберите привычный аккаунт. Новый пароль не понадобится — вход
          выполняется на стороне VK ID или Яндекс ID.
        </p>

        {authError ? (
          <p className="rb-alert" role="alert">
            <AlertCircle size={16} aria-hidden />
            <span>{authError}</span>
          </p>
        ) : null}

        <div className="rb-providers">
          <button
            type="button"
            className="rb-provider rb-provider--vk"
            onClick={() => onSignIn("vk")}
            disabled={!providers.vk || authPending}
            aria-describedby={!providers.vk ? "vk-note" : undefined}
          >
            <span className="rb-vk-mark" aria-hidden>
              VK
            </span>
            <span className="rb-provider__label">
              {authPending ? "Открываем провайдера…" : "Продолжить с VK ID"}
            </span>
            {authPending ? (
              <Loader2 className="rb-spin" size={18} aria-hidden />
            ) : null}
          </button>
          {!providers.vk ? (
            <p id="vk-note" className="rb-provider__note">
              VK ID пока недоступен в локальной конфигурации. Проверьте
              идентификатор приложения, его тип и адрес localhost. Демо-входа нет.
            </p>
          ) : null}

          <button
            type="button"
            className="rb-provider rb-provider--yandex"
            onClick={() => onSignIn("yandex")}
            disabled={!providers.yandex || authPending}
            aria-describedby={!providers.yandex ? "ya-note" : undefined}
          >
            <span className="rb-ya-mark" aria-hidden>
              Я
            </span>
            <span className="rb-provider__label">
              {authPending ? "Открываем провайдера…" : "Продолжить с Яндекс ID"}
            </span>
            {authPending ? (
              <Loader2 className="rb-spin" size={18} aria-hidden />
            ) : null}
          </button>
          {!providers.yandex ? (
            <p id="ya-note" className="rb-provider__note">
              Яндекс ID пока недоступен в локальной конфигурации. Проверьте
              идентификатор приложения и настройки входа. Демо-входа нет.
            </p>
          ) : null}
        </div>

        {bothMissing ? (
          <p className="rb-dialog__foot">
            Ни один провайдер не настроен. Заполните переменные в{" "}
            <code>apps/web/.env.local</code> и перезапустите приложение.
          </p>
        ) : (
          <p className="rb-dialog__foot">
            Продолжая, вы входите в локальную сессию этой итерации. Это не
            постоянный аккаунт платформы.
          </p>
        )}
      </div>
    </dialog>
  );
}
