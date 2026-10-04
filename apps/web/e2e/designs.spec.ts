import { expect, test, type Page } from "@playwright/test";

// Browser contract tests ONLY. Every backend API is intercepted; these fixtures
// never exist in the application runtime and do not prove real OAuth/inference.
const models = Array.from({ length: 9 }, (_, i) => ({
  id: `test/model-${i + 1}:free`, name: `Тестовая модель ${i + 1}`, provider: "test", contextLength: 8192,
}));

async function fixtureApi(page: Page, signedIn = false) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/models") return route.fulfill({ json: { models } });
    if (path === "/api/status") return route.fulfill({ json: { providers: { vk: true, yandex: true }, inference: true } });
    if (path === "/api/auth/session") return route.fulfill({ json: { viewer: signedIn ? { name: "Тестовый пользователь", provider: "yandex" } : null } });
    if (path === "/api/auth/logout") return route.fulfill({ json: { ok: true } });
    if (path === "/api/auth/start") return route.fulfill({ status: 503, json: { error: { code: "fixture_auth_error", message: "Тест: провайдер недоступен" } } });
    return route.fulfill({ status: 503, json: { error: { code: "unexpected_fixture_request", message: "Непредусмотренный тестовый запрос" } } });
  });
}

for (const variant of ["canvas"] as const) {
  test(`${variant}: landing, real controls, model selection and keyboard-safe login`, async ({ page }) => {
    await fixtureApi(page);
    await page.goto(`/${variant}`);
    await expect(page.locator("h1")).toBeVisible();
    await expect(page.getByRole("radio")).toHaveCount(6);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.locator(".rb-cta--xl").click();
    await expect(page.getByRole("dialog")).toBeVisible();
    const vk = page.getByRole("button", { name: /Продолжить с VK ID/ });
    await expect(vk).toBeEnabled();
    await expect(page.getByRole("button", { name: /Продолжить с Яндекс ID/ })).toBeEnabled();
    const authRequest = page.waitForRequest((req) => req.url().endsWith("/api/auth/start"));
    await vk.click();
    expect((await authRequest).postDataJSON()).toEqual({ provider: "vk", variant });
    await expect(page.getByRole("dialog").getByRole("alert")).toContainText("Тест: провайдер недоступен");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await expect(page.locator(".rb-cta--xl")).toBeFocused();
    await page.getByRole("button", { name: "Все модели (9)" }).click();
    await expect(page.getByRole("radio")).toHaveCount(9);
    await page.getByRole("radio").nth(8).click();
    await expect(page.getByRole("radio").nth(8)).toHaveAttribute("aria-checked", "true");
    await page.keyboard.press("Home");
    await expect(page.getByRole("radio").first()).toHaveAttribute("aria-checked", "true");
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("radio").nth(1)).toHaveAttribute("aria-checked", "true");
    await page.getByRole("button", { name: "Свернуть", exact: true }).click();
    await expect(page.getByRole("radio")).toHaveCount(6);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });

  test(`${variant}: first-request form, consent, response text and logout`, async ({ page }) => {
    await fixtureApi(page, true);
    await page.route("**/api/chat", async (route) => route.fulfill({ json: {
      text: "<script>throw new Error('must not execute')</script> Тестовый ответ",
      model: models[0].id, requestId: "test-request-001", inputTokens: 5, outputTokens: 12,
    } }));
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`/${variant}#workspace`);
    await page.getByLabel("Текст запроса", { exact: true }).fill("Объясни радугу простыми словами");
    const send = page.getByRole("button", { name: "Отправить запрос", exact: true });
    await expect(send).toBeDisabled();
    await page.getByRole("checkbox").check();
    await expect(send).toBeEnabled();
    const outbound = page.waitForRequest((req) => req.url().endsWith("/api/chat"));
    await send.click();
    expect((await outbound).postDataJSON()).toEqual({ model: models[0].id, prompt: "Объясни радугу простыми словами", consent: true });
    await expect(page.locator(".rb-result__text")).toContainText("<script>");
    await expect(page.getByText("test-request-001")).toBeVisible();
    expect(errors).toEqual([]);
    await page.locator(".rb-workspace").getByRole("button", { name: "Выйти", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Сначала войдите" })).toBeVisible();
    await expect(page.locator(".rb-result__text")).toHaveCount(0);
  });
}

test("callback errors are visible immediately, not hidden behind another click", async ({ page }) => {
  await fixtureApi(page);
  await page.goto("/canvas?auth_error=state#workspace");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText("Начните вход заново");
  await expect(page).toHaveURL(/\/canvas#workspace$/);
});

test("missing configuration is explicit and never turns into fake login", async ({ page }) => {
  await fixtureApi(page);
  await page.route("**/api/status", (route) => route.fulfill({ json: { providers: { vk: false, yandex: false }, inference: false } }));
  await page.goto("/canvas");
  await page.locator(".rb-cta--xl").click();
  await expect(page.getByRole("button", { name: /Продолжить с VK ID/ })).toBeDisabled();
  await expect(page.getByRole("button", { name: /Продолжить с Яндекс ID/ })).toBeDisabled();
  await expect(page.getByRole("dialog")).toContainText(".env.local");
});

test("removed designs are gone: routes 404 and no design switcher", async ({ page }) => {
  await fixtureApi(page);
  for (const path of ["/flow", "/pulse"]) {
    const response = await page.goto(path);
    expect(response?.status()).toBe(404);
  }
  await page.goto("/canvas");
  await expect(page.locator(".rb-selector")).toHaveCount(0);
});
