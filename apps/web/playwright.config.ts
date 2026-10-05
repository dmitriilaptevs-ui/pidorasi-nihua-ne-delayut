import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  use: { baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:3001", trace: "retain-on-failure" },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 }, storageState: "e2e/.auth/user.json" },
    },
    {
      name: "mobile",
      use: { ...devices["iPhone 13"], defaultBrowserType: "chromium", storageState: "e2e/.auth/user.json" },
    },
  ],
  // Deliberately no auto-start: run against the deployed stack (E2E_BASE_URL).
});
