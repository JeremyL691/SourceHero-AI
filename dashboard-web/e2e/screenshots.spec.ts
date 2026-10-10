import { expect, test, type Page } from "@playwright/test";

/**
 * Documentation screenshots, driven against the real stack.
 *
 * Excluded from the default run (`--grep-invert @screenshots`) so the smoke suite
 * stays fast; `scripts/screenshots.sh` runs these alone.
 */

const SHOTS: { name: string; path: string }[] = [
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
const OUT = process.env.SCREENSHOT_DIR || "../../docs/assets";
const VIEWPORT = { width: 1440, height: 900 };

type Cookie = Parameters<ReturnType<Page["context"]>["addCookies"]>[0][number];
let sharedCookies: Cookie[] = [];
let sharedToken = "";

test.describe("@screenshots the reading surfaces", () => {
  test.use({ viewport: VIEWPORT });
  test.setTimeout(300_000);

  test.beforeAll(async ({ browser }) => {
    const context = await browser.newContext({
      baseURL: process.env.E2E_WEB_URL || "http://127.0.0.1:3797",
    });
    const page = await context.newPage();

    // 1. Sign up a fresh user
    const email = `shot-${Date.now()}-${Math.floor(Math.random() * 1e6)}@test.local`;
    await page.goto("/login");
    await page.getByRole("button", { name: /need an account/i }).click();
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill("screenshot-password-1234");
    await page.getByRole("button", { name: /create account/i }).click();
    await expect(page).toHaveURL(/\/app/, { timeout: 30_000 });

    // 2. Load the demo corpus
    await page.goto("/app/inbox");
    const seed = page.getByRole("button", { name: /load demo corpus/i });
    await expect(seed).toBeVisible({ timeout: 30_000 });
    await seed.click();
    await expect(page.getByText(/demo corpus queued/i)).toBeVisible({ timeout: 30_000 });

    // 3. Wait for the queue to fully drain (queued + running + deferred == 0)
    const cookies = await context.cookies();
    sharedToken = cookies.find((c) => c.name === "sh_token")?.value ?? "";
    await expect
      .poll(
        async () => {
          const r = await page.request.get(`${API}/v1/jobs/summary`, {
            headers: sharedToken ? { Authorization: `Bearer ${sharedToken}` } : {},
          });
          if (!r.ok()) return -1;
          const data = (await r.json()) as { queued: number; running: number; deferred: number };
          return data.queued + data.running + data.deferred;
        },
        { timeout: 180_000, intervals: [1000, 2000, 3000] },
      )
      .toBe(0);

    // 4. Generate changes diff so /app/changes has rich content
    await page.goto("/app/changes");
    await page.getByRole("button", { name: /^Generate$/ }).click();
    await expect
      .poll(
        async () => {
          const response = await page.request.get(`${API}/v1/diffs?limit=5`, {
            headers: sharedToken ? { Authorization: `Bearer ${sharedToken}` } : {},
          });
          if (!response.ok()) return -1;
          return (await response.json()).length;
        },
        { timeout: 60_000, intervals: [1000, 2000] },
      )
      .toBeGreaterThan(0);

    // 5. Ask a question so /app/ask has a full answer with per-sentence verdicts
    await page.goto("/app/ask");
    await page.getByLabel("Your question").fill("What is the energy density of the HX-2?");
    await page.getByRole("button", { name: /^ask$/i }).click();
    await expect(page.locator("[data-verdict]").first()).toBeVisible({ timeout: 60_000 });

    sharedCookies = await context.cookies();
    await context.close();
  });

  for (const theme of ["light", "dark"] as const) {
    for (const shot of SHOTS) {
      test(`capture ${shot.name} (${theme})`, async ({ page }) => {
        await page.context().addCookies(sharedCookies);
        await page.addInitScript((token) => {
          window.localStorage.setItem("sh_token", token);
        }, sharedToken);
        await page.emulateMedia({ colorScheme: theme });

        if (shot.name === "overview") {
          await page.goto(shot.path);
          await expect(page.getByTestId("stat-sources")).toBeVisible({ timeout: 30_000 });
        } else if (shot.name === "cluster") {
          const clusterId = await firstConflictedCluster(page);
          await page.goto(`/app/clusters/${clusterId}`);
          await expect(page.getByText(/Camp A/).first()).toBeVisible({ timeout: 30_000 });
        } else if (shot.name === "ask") {
          await page.goto(shot.path);
          // Load previous answer to show populated state
          const prevAnswer = page.locator("button.serif").first();
          if (await prevAnswer.isVisible()) {
            await prevAnswer.click();
            await expect(page.locator("[data-verdict]").first()).toBeVisible({ timeout: 30_000 });
          } else {
            await page.getByLabel("Your question").fill("What is the energy density of the HX-2?");
            await page.getByRole("button", { name: /^ask$/i }).click();
            await expect(page.locator("[data-verdict]").first()).toBeVisible({ timeout: 60_000 });
          }
          // Assert Ask is NOT in empty state
          await expect(page.locator("[data-verdict]").first()).toBeVisible();
        } else if (shot.name === "wiki") {
          await page.goto(shot.path);
          await expect(page.locator("a[href^='/app/wiki/']").first()).toBeVisible({ timeout: 60_000 });
          // Assert wiki pages count is not 0
          const wikiLinks = await page.locator("a[href^='/app/wiki/']").count();
          expect(wikiLinks).toBeGreaterThanOrEqual(3);
        } else if (shot.name === "conflicts") {
          await page.goto(shot.path);
          await expect(page.getByText(/open the divergence map/i).first()).toBeVisible({
            timeout: 60_000,
          });
          // Assert conflicts count matches API
          const confResp = await page.request.get(`${API}/v1/conflicts?limit=50`, {
            headers: sharedToken ? { Authorization: `Bearer ${sharedToken}` } : {},
          });
          expect(confResp.ok()).toBe(true);
          const confApiRows = (await confResp.json()) as Array<{
            cluster_id: number | null;
            a: { document_id: string; status: string };
            b: { document_id: string; status: string };
          }>;
          const seen = new Set<string>();
          const deduplicated = confApiRows.filter((c) => {
            if (c.cluster_id !== null && (c.a.status === "carried" || c.b.status === "carried")) {
              const pair = [c.a.document_id, c.b.document_id].sort().join(":");
              const k = `${c.cluster_id}:${pair}`;
              if (seen.has(k)) return false;
              seen.add(k);
            }
            return true;
          });
          expect(deduplicated.length).toBeGreaterThanOrEqual(4);
          await expect(page.locator("ul.divide-y > li")).toHaveCount(deduplicated.length);
        } else if (shot.name === "changes") {
          await page.goto(shot.path);
          await expect(page.getByText(/What changed/i).first()).toBeVisible({ timeout: 30_000 });
        } else if (shot.name === "library") {
          await page.goto(shot.path);
          await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 30_000 });
        } else if (shot.name === "verify") {
          await page.goto(shot.path);
          await expect(page.getByLabel("Text to verify")).toBeVisible({ timeout: 30_000 });
        } else {
          await page.goto(shot.path);
        }

        // Settle fonts and layout
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

async function firstConflictedCluster(page: Page): Promise<string> {
  const response = await page.request.get(`${API}/v1/clusters?conflicted=true`, {
    headers: sharedToken ? { Authorization: `Bearer ${sharedToken}` } : {},
  });
  const rows = await response.json();
  return rows[0]?.id ?? "1";
}
