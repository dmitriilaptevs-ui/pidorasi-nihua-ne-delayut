import { expect, test as setup } from "@playwright/test";

/**
 * Sign in once per run and store the session for the other tests, so the
 * login endpoint's per-email throttle is not consumed by every test.
 * Credentials come from the operator environment; the files stay gitignored.
 */

async function signInAndSave(
  page: import("@playwright/test").Page,
  email: string | undefined,
  password: string | undefined,
  path: string,
  label: string,
): Promise<void> {
  if (!email || !password) {
    console.warn(`[auth-setup] ${label} credentials missing; writing an empty state`);
    // Keep the file present so dependent projects still start (their tests skip).
    await page.context().storageState({ path });
    return;
  }
  await page.goto("/login");
  await page.fill("#email", email);
  await page.fill("#password", password);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL(/\/account$/, { timeout: 25_000 });
  await page.context().storageState({ path });
}

setup("sign in the acceptance user and admin", async ({ page }) => {
  await signInAndSave(page, process.env.E2E_USER_EMAIL, process.env.E2E_USER_PASSWORD, "e2e/.auth/user.json", "user");
  await signInAndSave(page, process.env.E2E_ADMIN_EMAIL, process.env.E2E_ADMIN_PASSWORD, "e2e/.auth/admin.json", "admin");
});
