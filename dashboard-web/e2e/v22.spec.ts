import { expect, test, type Page } from "@playwright/test";

/**
 * The v2.2 flows the smoke path does not cover: the bookmarklet capture page (pre-fills, saves
 * only on confirm), the OPML import with reliability on its source, and a wiki page that is
 * exported as a brief, shared, read without signing in, and then revoked.
 */

const API = process.env.E2E_API_URL || "http://127.0.0.1:8797";

test.setTimeout(300_000);

async function signUp(page: Page): Promise<void> {
  const email = `e2e-v22-${Date.now()}-${Math.floor(Math.random() * 1e6)}@test.local`;
  await page.goto("/login");
  await page.getByRole("button", { name: /need an account/i }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("e2e-password-1234");
  await page.getByRole("button", { name: /create account/i }).click();
  await expect(page).toHaveURL(/\/app/, { timeout: 30_000 });
}

async function authHeaders(page: Page): Promise<Record<string, string>> {
  const cookies = await page.context().cookies();
  const token = cookies.find((c) => c.name === "sh_token")?.value;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function waitForDrain(page: Page): Promise<void> {
  await expect
    .poll(
      async () => {
        const headers = await authHeaders(page);
        const response = await page.request.get(`${API}/v1/jobs/summary`, { headers });
        if (!response.ok()) return -1;
        const data = (await response.json()) as { queued: number; running: number; deferred: number };
        return data.queued + data.running + data.deferred;
      },
      { timeout: 180_000, intervals: [1000, 2000, 3000] },
    )
    .toBe(0);
}

test("the bookmarklet capture page pre-fills the note and saves only when confirmed", async ({ page }) => {
  await signUp(page);
  const note = "Bookmarked note about HX-2 cooling.";
  const captures: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().includes("/v1/captures")) captures.push(req.url());
  });

  await page.goto(`/app/capture?text=${encodeURIComponent(note)}`);
  await expect(page.locator("#capture-text")).toHaveValue(note, { timeout: 30_000 });
  expect(captures).toHaveLength(0);

  await page.getByRole("button", { name: /^Save capture$/i }).click();
  await expect(page.getByRole("status").filter({ hasText: /^Captured$/ })).toBeVisible({ timeout: 30_000 });
  expect(captures).toHaveLength(1);
});

test("an OPML import creates feeds, skips duplicates, and reliability can be set on a source", async ({ page }) => {
  await signUp(page);
  await page.goto("/app/inbox");

  // A public address literal: the import validates the URL without a DNS lookup.
  const opml =
    '<?xml version="1.0"?><opml version="2.0"><body>' +
    '<outline text="Feed A" xmlUrl="http://93.184.216.34/feed.xml"/>' +
    '<outline text="Feed A again" xmlUrl="http://93.184.216.34/feed.xml"/>' +
    "</body></opml>";
  await page.getByTestId("opml-file").setInputFiles({
    name: "feeds.opml",
    mimeType: "text/xml",
    buffer: Buffer.from(opml),
  });
  await page.getByTestId("opml-submit").click();
  await expect(page.getByText(/Imported 1 new, 1 skipped/)).toBeVisible({ timeout: 30_000 });

  const headers = await authHeaders(page);
  const sources = (await (await page.request.get(`${API}/v1/sources`, { headers })).json()) as Array<{
    id: string;
    source_type: string;
  }>;
  const feed = sources.find((s) => s.source_type === "rss");
  expect(feed).toBeTruthy();

  const patched = await page.request.patch(`${API}/v1/sources/${feed!.id}`, {
    headers,
    data: { reliability: "high" },
  });
  expect(patched.ok()).toBe(true);
  expect(((await patched.json()) as { reliability: string }).reliability).toBe("high");
});

test("a wiki page exports as a brief, is shared, and the link stops working once revoked", async ({
  page,
  browser,
}) => {
  await signUp(page);
  await page.goto("/app/inbox");
  await page.getByRole("button", { name: /load demo corpus/i }).click();
  await expect(page.getByText(/demo corpus queued/i)).toBeVisible({ timeout: 30_000 });
  await waitForDrain(page);

  await page.goto("/app/wiki");
  await page.locator("a[href^='/app/wiki/']").first().click();
  await expect(page).toHaveURL(/\/app\/wiki\/[a-z0-9-]+/);

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: /^Export brief$/ }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/\.md$/);

  await page.getByTestId("share-button").click();
  const link = ((await page.getByTestId("share-link").textContent()) ?? "").trim();
  expect(link).toContain("/share/");

  // Read the shared page as someone who is not signed in.
  const anonymous = await browser.newContext();
  const shared = await anonymous.newPage();
  await shared.goto(link);
  await expect(shared.getByTestId("shared-view")).toBeVisible({ timeout: 30_000 });
  await expect(shared.getByTestId("shared-title")).not.toBeEmpty();

  // Revoke every share of this user, then the same link must say it is gone.
  const headers = await authHeaders(page);
  const shares = (await (await page.request.get(`${API}/v1/shares`, { headers })).json()) as Array<{ id: number }>;
  expect(shares.length).toBeGreaterThan(0);
  for (const share of shares) {
    await page.request.delete(`${API}/v1/shares/${share.id}`, { headers });
  }
  await shared.reload();
  await expect(shared.getByText("This link is invalid, expired, or was revoked.")).toBeVisible({ timeout: 30_000 });
  await anonymous.close();
});
