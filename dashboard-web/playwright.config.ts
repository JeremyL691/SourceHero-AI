import { defineConfig, devices } from "@playwright/test";

/** Chromium only: the app's own layout is verified in a real browser, not across engines. */
export default defineConfig({
  testDir: "./e2e",
  // The e2e stack is built first, so a cold start is normal rather than a failure.
  timeout: 60_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  // Documentation screenshots live in a @screenshots-tagged file and are excluded by
  // default, so the smoke suite stays fast. scripts/screenshots.sh sets
  // E2E_SCREENSHOTS=1 to opt back in -- otherwise its `--grep` would be cancelled by
  // this very filter.
  grepInvert: process.env.E2E_SCREENSHOTS ? undefined : /@screenshots/,
  outputDir: "test-results",
  use: {
    baseURL: process.env.E2E_WEB_URL || "http://127.0.0.1:3797",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    actionTimeout: 20_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
