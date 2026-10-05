# rubai web — интерфейс платформы

Next.js (App Router) с дизайном «Холст». Интерфейс не хранит балансы и ключи: все данные приходят из платформенного API `apps/api` через rewrite `/api/*` и `/v1/*` (see `next.config.ts`). Клиентский секрет только один — HttpOnly cookie сессии, выставленная API.

## Страницы

| Путь | Назначение |
| --- | --- |
| `/` | лендинг: преимущества, каталог с рублёвыми ценами, шаги подключения |
| `/register`, `/login` | регистрация и вход: почта+пароль, VK ID, Яндекс ID, сброс пароля |
| `/verify-email?token=…` | подтверждение адреса (одноразовая ссылка из письма) |
| `/reset-password?token=…` | новый пароль, отзывает все сессии |
| `/account` | баланс, ключи (показ один раз, отзыв с подтверждением), история операций, каталог, админ-секция |
| `/api/status` | liveness для healthcheck контейнера |

## Локальная разработка

```bash
npm ci
npm run dev        # http://127.0.0.1:3001, API ожидается на 127.0.0.1:8080
npm test           # 6 unit-тестов клиентских помощников
npm run typecheck
npm run build
```

Для проверки против compose-стека удобнее открыть `http://127.0.0.1:3001` при `PUBLIC_ORIGIN=http://127.0.0.1:3001` (см. infra/README.md): Origin-проверки API требуют совпадения адреса.

## E2E (Playwright)

Набор `e2e/app.spec.ts` работает против развёрнутого стенда:

```bash
E2E_BASE_URL=https://<публичный-адрес> \
E2E_USER_EMAIL=… E2E_USER_PASSWORD=… \
E2E_FORM_EMAIL=… E2E_FORM_PASSWORD=… \
E2E_ADMIN_EMAIL=… npx playwright test
```

Сессии для тестов готовит `scripts/acceptance/prepare_e2e.sh`: он создаёт cookie через операторский CLI (`app.services.identity.create_session`) и пишет `e2e/.auth/*.json` (в git не попадают). Так лимит входа не расходуется на каждый тест. Сценарий выхода использует отдельный аккаунт, потому что logout отзывает сессию из общего storage state.

Проекты `desktop` (1440×1000) и `mobile` (iPhone 13, Chromium) запускаются из `playwright.config.ts`.

## Ограничения

- Живой вход VK ID/Яндекс ID на публичном адресе требует приложения, зарегистрированного на этом домене (см. docs/ACCEPTANCE-REPORT.md).
- Пополнение в песочнице: пока провайдер не настроен, `/api/payments` отвечает `503 payments_not_configured`.
