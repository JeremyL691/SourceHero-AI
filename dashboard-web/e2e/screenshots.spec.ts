import { expect, test, type Page } from "@playwright/test";

/**
 * Documentation screenshots, driven against the real stack.
 *
 * Excluded from the default run (`--grep-invert @screenshots`) so the smoke suite
 * stays fast; `scripts/screenshots.sh` runs these alone.
 */

const SHOTS: { name: string; path: string; theme?: "light" | "dark" }[] = [
  { name: "overview", path: "/app" },
  { name: "conflicts", path: "/app/conflicts" },
  { name: "cluster", path: "/app/clusters/first" },
  { name: "changes", path: "/app/changes" },
  { name: "wiki", path: "/app/wiki" },
  { name: "ask", path: "/app/ask" },
  { name: "verify", path: "/app/verify" },
  { name: "library", path: "/app/library" },
  { name: "settings", path: "/app/settings" },
];

const API = process.env.E2E_API_URL || "http://127.0.0.1:8797";
// Absolute: Playwright resolves screenshot paths against its own cwd, and a relative
// path silently writes somewhere else when the suite is invoked from another directory.
const OUT = process.env.SCREENSHOT_DIR || "../../docs/assets";
const VIEWPORT = { width: 1440, height: 900 };

test.describe("@screenshots the reading surfaces", () => {
  test.use({ viewport: VIEWPORT });

  for (const theme of ["light", "dark"] as const) {
    for (const shot of SHOTS) {
      test(`capture ${shot.name} (${theme})`, async ({ page }) => {
        await authenticate(page);
        await page.emulateMedia({ colorScheme: theme });

        if (shot.name === "cluster") {
          const clusterId = await firstConflictedCluster(page);
          await page.goto(`/app/clusters/${clusterId}`);
        } else {
          await page.goto(shot.path);
        }

        // Settle: fonts, charts and the layout shift after first paint.
        await page.waitForLoadState("networkidle");
        await page.waitForTimeout(600);

        const file = `${OUT}/v2-${shot.name}${theme === "dark" ? "-dark" : ""}.png`;
        await page.screenshot({ path: file, fullPage: false });
        console.log(`screenshot: ${file}`);
        expect(await page.title()).toBeTruthy();
      });
    }
  }
});

async function authenticate(page: Page): Promise<void> {
  const email = `shot-${Date.now()}-${Math.floor(Math.random() * 1e6)}@test.local`;
  await page.goto("/login");
  await page.getByRole("button", { name: /need an account/i }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("screenshot-password-1234");
  await page.getByRole("button", { name: /create account/i }).click();
  await expect(page).toHaveURL(/\/app/, { timeout: 30_000 });

  // Seed so the screens have something to show.
  await page.goto("/app/inbox");
  const seed = page.getByRole("button", { name: /load demo corpus/i });
  if (await seed.isVisible().catch(() => false)) {
    await seed.click();
    const cookies = await page.context().cookies();
    const token = cookies.find((c) => c.name === "sh_token")?.value;
    await expect
      .poll(
        async () => {
          const r = await page.request.get(`${API}/v1/jobs?status=queued&limit=1`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          });
          return r.ok() ? (await r.json()).length : -1;
        },
        { timeout: 180_000, intervals: [1000, 2000, 3000] },
      )
      .toBe(0);
  }
}

async function firstConflictedCluster(page: Page): Promise<string> {
  const cookies = await page.context().cookies();
  const token = cookies.find((c) => c.name === "sh_token")?.value;
  const response = await page.request.get(`${API}/v1/clusters?conflicted=true`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  const rows = await response.json();
  return rows[0]?.id ?? "1";
}
