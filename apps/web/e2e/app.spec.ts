import { expect, test, type Page } from "@playwright/test";

/**
 * Live browser tests against the deployed stack (E2E_BASE_URL).
 *
 * The setup project signs in once and stores the session; tests that need an
 * account just open /account. Operator credentials come from the environment:
 *   E2E_USER_EMAIL / E2E_USER_PASSWORD, E2E_ADMIN_EMAIL / E2E_ADMIN_PASSWORD
 */

const hasUser = Boolean(process.env.E2E_USER_EMAIL && process.env.E2E_USER_PASSWORD);
const hasAdmin = Boolean(process.env.E2E_ADMIN_EMAIL);

async function openAccount(page: Page) {
  await page.goto("/account");
  await expect(page.getByRole("heading", { name: "Баланс" })).toBeVisible({ timeout: 45_000 });
}

test("landing renders the catalog and links to registration", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Доступ к зарубежным AI-моделям/ })).toBeVisible();
  await expect(page.locator("#models .rb-model").first()).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("#models .rb-model").first().locator(".rb-model__price")).toContainText("₽");
  await page.getByRole("link", { name: "Создать аккаунт" }).first().click();
  await expect(page).toHaveURL(/\/register$/);
});

test("login page exposes all three sign-in methods", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("button", { name: "Войти", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "VK ID" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Яндекс ID" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Забыли пароль?" })).toBeVisible();
});

test("registration form requires a long password", async ({ page }) => {
  await page.goto("/register");
  await page.fill("#email", "e2e-short-password@example.com");
  await page.fill("#password", "short");
  await page.getByRole("button", { name: "Создать аккаунт" }).click();
  await expect(page.locator(".rb-alert--ok")).toHaveCount(0);
});

test("registration reaches the API (success or the documented IP throttle)", async ({ page }) => {
  await page.goto("/register");
  await page.fill("#email", `e2e-${Date.now()}@example.com`);
  await page.fill("#password", "e2e-password-long-1");
  await page.getByRole("button", { name: "Создать аккаунт" }).click();
  const outcome = page.locator(".rb-alert--ok, .rb-alert--error");
  await expect(outcome.first()).toBeVisible({ timeout: 20_000 });
  const text = (await outcome.first().textContent()) ?? "";
  expect(text).toMatch(/подтвержд|попыток|Слишком много/);
});

test("login form signs in and lands on the account", async ({ page }) => {
  const email = process.env.E2E_FORM_EMAIL;
  const password = process.env.E2E_FORM_PASSWORD;
  test.skip(!email || !password, "E2E_FORM_EMAIL/E2E_FORM_PASSWORD not provided");
  await page.goto("/login");
  await page.fill("#email", email!);
  await page.fill("#password", password!);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL(/\/account$/, { timeout: 25_000 });
  await expect(page.getByRole("heading", { name: "Баланс" })).toBeVisible();
});

test("balance, keys and catalog are visible to the signed-in user", async ({ page }) => {
  test.skip(!hasUser, "E2E_USER_EMAIL/E2E_USER_PASSWORD not provided");
  await openAccount(page);
  await expect(page.locator(".rb-stat").first().locator(".rb-stat__value")).toContainText("₽");
  await expect(page.getByRole("heading", { name: "API-ключи" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Каталог моделей" })).toBeVisible();
  await page.getByLabel("Поиск").fill("gemini");
  await expect(page.locator(".rb-table tbody tr").first()).toBeVisible({ timeout: 15_000 });
});

test("create a key once, copy-once behaviour, then revoke it", async ({ page }) => {
  test.skip(!hasUser, "E2E_USER_EMAIL/E2E_USER_PASSWORD not provided");
  const keyName = `e2e-${Date.now()}`;
  await openAccount(page);
  await page.fill("#key-name", keyName);
  await page.getByRole("button", { name: "Создать ключ" }).click();

  const rawKey = page.locator(".rb-key");
  await expect(rawKey).toBeVisible({ timeout: 20_000 });
  await expect(rawKey).toContainText("sk-rubai-");
  await page.getByRole("button", { name: "Скрыть" }).click();
  await expect(page.locator(".rb-key")).toHaveCount(0);

  const row = page.locator("tr", { hasText: keyName });
  await row.scrollIntoViewIfNeeded();
  await expect(row).toContainText("активен");
  // Revocation asks for confirmation in the UI.
  page.on("dialog", (dialog) => dialog.accept());
  await row.getByRole("button", { name: "Отозвать" }).click();
  await expect(row).toContainText("отозван", { timeout: 20_000 });
});

test.describe("admin", () => {
  test.use({ storageState: "e2e/.auth/admin.json" });

  test("admin sees reconciliation and can sync the catalog", async ({ page }) => {
    test.skip(!hasAdmin, "E2E_ADMIN_EMAIL/E2E_ADMIN_PASSWORD not provided");
    await openAccount(page);
    await expect(page.getByRole("heading", { name: "Администрирование" })).toBeVisible();
    await page.getByRole("button", { name: "Синхронизировать каталог" }).click();
    await expect(page.locator(".rb-alert--ok")).toContainText("Проверено", { timeout: 120_000 });
    await expect(page.getByRole("heading", { name: /Сверка/ })).toBeVisible();
  });
});

test("logout returns to the landing", async ({ page }) => {
  // Uses its own session: logging out revokes the token held in the shared
  // storage state, which would break later projects (mobile).
  const email = process.env.E2E_FORM_EMAIL;
  const password = process.env.E2E_FORM_PASSWORD;
  test.skip(!email || !password, "E2E_FORM_EMAIL/E2E_FORM_PASSWORD not provided");
  await page.goto("/login");
  await page.fill("#email", email!);
  await page.fill("#password", password!);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL(/\/account$/, { timeout: 25_000 });
  await page.getByRole("button", { name: "Выйти" }).click();
  await expect(page).toHaveURL(/\/$/, { timeout: 20_000 });
});
