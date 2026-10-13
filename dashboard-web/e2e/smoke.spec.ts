import { expect, test, type Page } from "@playwright/test";

/**
 * The smoke path a first-time user actually walks: sign up, load the demo corpus,
 * find a disagreement, see what changed, read a compiled page with hoverable footnotes,
 * verify a sentence, ask a streamed question with per-sentence verdicts, capture a note,
 * upload a markdown file, create a PAT with MCP snippets, verify non-admin restrictions,
 * and verify responsive layout on populated data.
 */

const API = process.env.E2E_API_URL || "http://127.0.0.1:8797";

test.setTimeout(300_000);

async function signUp(page: Page): Promise<string> {
  const email = `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@test.local`;
  await page.goto("/login");
  await page.getByRole("button", { name: /need an account/i }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("e2e-password-1234");
  await page.getByRole("button", { name: /create account/i }).click();
  await expect(page).toHaveURL(/\/app/, { timeout: 30_000 });
  return email;
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
        const data = (await response.json()) as {
          queued: number;
          running: number;
          deferred: number;
        };
        return data.queued + data.running + data.deferred;
      },
      { timeout: 180_000, intervals: [1000, 2000, 3000] },
    )
    .toBe(0);
}

test("the full loop: sign up, seed, disagree, verify, ask, capture, upload, pat, permissions, responsive", async ({
  page,
}) => {
  const email = await signUp(page);

  // --- 1. Load the demo corpus and wait for pipeline drain
  await page.goto("/app/inbox");
  await page.getByRole("button", { name: /load demo corpus/i }).click();
  await expect(page.getByText(/demo corpus queued/i)).toBeVisible({ timeout: 30_000 });

  await waitForDrain(page);

  // --- 2. Overview shows populated corpus and outdated notes
  await page.goto("/app");
  const sourcesStat = page.getByTestId("stat-sources");
  await expect(sourcesStat).toBeVisible({ timeout: 30_000 });
  const sourceCount = await sourcesStat.locator("p").first().innerText();
  expect(Number(sourceCount)).toBeGreaterThan(0);
  await expect(page.getByText(/Outdated notes/i).first()).toBeVisible({ timeout: 30_000 });

  // --- 3. Conflicts: count matches API (>= 4) and density disagreement has two camps
  await page.goto("/app/conflicts");
  await expect(page.getByText(/open the divergence map/i).first()).toBeVisible({
    timeout: 60_000,
  });

  const confResp = await page.request.get(`${API}/v1/conflicts?limit=50`, {
    headers: await authHeaders(page),
  });
  expect(confResp.ok()).toBe(true);
  const confApiRows = (await confResp.json()) as Array<{
    cluster_id: number | null;
    a: { document_id: string; status: string };
    b: { document_id: string; status: string };
  }>;
  const seenPairs = new Set<string>();
  const deduplicatedApi = confApiRows.filter((c) => {
    if (c.cluster_id !== null && (c.a.status === "carried" || c.b.status === "carried")) {
      const docPair = [c.a.document_id, c.b.document_id].sort().join(":");
      const key = `${c.cluster_id}:${docPair}`;
      if (seenPairs.has(key)) return false;
      seenPairs.add(key);
    }
    return true;
  });
  expect(deduplicatedApi.length).toBeGreaterThanOrEqual(4);
  await expect(page.locator("ul.divide-y > li")).toHaveCount(deduplicatedApi.length);
  // Topic filter: choosing a topic asks the API for that topic's conflicts only.
  const topicSelect = page.getByTestId("k6-conflicts-topic");
  const [topicRequest] = await Promise.all([
    page.waitForRequest((req) => req.url().includes("/v1/conflicts") && req.url().includes("topic_id=")),
    topicSelect.selectOption({ index: 1 }),
  ]);
  expect(topicRequest.url()).toContain("topic_id=");
  await topicSelect.selectOption({ index: 0 });

  const densityCard = page.locator("li").filter({ hasText: "380 Wh/kg" }).first();
  await expect(densityCard).toBeVisible({ timeout: 30_000 });
  await expect(densityCard.getByText(/450 Wh\/kg/).first()).toBeVisible();
  await densityCard.getByRole("link", { name: /open the divergence map/i }).click();

  await expect(page).toHaveURL(/\/app\/clusters\/\d+/);
  try {
    await expect(page.getByText(/Camp A/).first()).toBeVisible({ timeout: 30_000 });
  } catch (error) {
    // Diagnostic for an intermittent failure: the cluster the map was built from, and the
    // conflict rows that point at clusters. Printed only when the assertion fails.
    const clusterId = page.url().match(/\/app\/clusters\/(\d+)/)?.[1];
    const cluster = await page.request.get(`${API}/v1/clusters/${clusterId}`, {
      headers: await authHeaders(page),
    });
    console.log(`camp-diagnostic cluster ${clusterId}: ${await cluster.text()}`);
    console.log(`camp-diagnostic conflicts: ${JSON.stringify(confApiRows)}`);
    throw error;
  }
  await expect(page.getByText(/Camp B/).first()).toBeVisible();
  await expect(page.getByText(/380 Wh\/kg/).first()).toBeVisible();
  await expect(page.getByText(/450 Wh\/kg/).first()).toBeVisible();

  // --- 4. What changed: generate diff and verify digest
  await page.goto("/app/changes");
  await page.getByRole("button", { name: /^Generate$/ }).click();

  await expect
    .poll(
      async () => {
        const response = await page.request.get(`${API}/v1/diffs?limit=5`, {
          headers: await authHeaders(page),
        });
        if (!response.ok()) return -1;
        const rows = (await response.json()) as Array<{ id: number }>;
        return rows.length;
      },
      { timeout: 60_000, intervals: [1000, 2000] },
    )
    .toBeGreaterThan(0);

  await page.reload();
  await expect(page.getByText(/What changed/i).first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/postponed/i).first()).toBeVisible({ timeout: 60_000 });

  // --- 5. Compiled wiki: >=3 pages, disputed tag, hover footnote quote
  await page.goto("/app/wiki");
  const wikiResp = await page.request.get(`${API}/v1/wiki/pages`, {
    headers: await authHeaders(page),
  });
  expect(wikiResp.ok()).toBe(true);
  const wikiPages = (await wikiResp.json()) as unknown[];
  expect(wikiPages.length).toBeGreaterThanOrEqual(3);

  const firstWikiLink = page.locator("a[href^='/app/wiki/']").first();
  await expect(firstWikiLink).toBeVisible({ timeout: 60_000 });
  await firstWikiLink.click();
  await expect(page).toHaveURL(/\/app\/wiki\/[a-z0-9-]+/);
  // Revision history: the sidebar lists the page's compiles.
  await expect(page.getByTestId("k6-revision-list")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("[data-testid^='k6-revision-item-']").first()).toBeVisible();

  // Footnote hover shows tooltip with source quote
  const footnoteLink = page.locator("a[href*='fn-'], a[data-footnote-ref]").first();
  await expect(footnoteLink).toBeVisible({ timeout: 30_000 });
  await footnoteLink.hover();
  const quoteTooltip = page.locator("span.serif.italic").filter({ hasText: "“" }).first();
  await expect(quoteTooltip).toBeVisible({ timeout: 10_000 });

  // --- 6. Verify: disputed claim verification
  await page.goto("/app/verify");
  const textarea = page.getByLabel("Text to verify");
  await textarea.fill("The HX-2 cell delivers an energy density of 450 Wh/kg.");
  await page.getByRole("button", { name: /verify/i }).first().click();
  await expect(page.locator('[data-verdict="disputed"]').first()).toBeVisible({
    timeout: 60_000,
  });

  // --- 7. Ask: streamed answer with verdicts and evidence
  await page.goto("/app/ask");
  await page.getByLabel("Your question").fill("What is the energy density of the HX-2?");
  await page.getByRole("button", { name: /^ask$/i }).click();
  await expect(page.locator("[data-verdict]").first()).toBeVisible({ timeout: 60_000 });
  await expect(
    page.locator("h2, h3, div").filter({ hasText: /^Evidence \([1-9]\d*\)/ }).first(),
  ).toBeVisible({ timeout: 60_000 });

  // --- 8. Quick capture note and search
  await page.goto("/app/inbox");
  await page
    .getByLabel("URL or note")
    .fill("Solid-state battery safety test demonstrated zero thermal runaway at 200C.");
  await page.getByRole("button", { name: /^Capture$/i }).click();
  await expect(page.getByTestId("k6-capture-preview")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: /^Save capture$/i }).click();
  await expect(page.getByRole("status").filter({ hasText: /^Captured$/ })).toBeVisible({ timeout: 30_000 });
  await waitForDrain(page);

  const searchInput = page.getByLabel("Search sources");
  await searchInput.fill("thermal runaway");
  await searchInput.press("Enter");
  await expect(page).toHaveURL(/\/app\/library\?q=thermal%20runaway/i);
  await expect(page.getByText(/Solid-state battery safety/i).first()).toBeVisible({ timeout: 30_000 });

  // --- 9. Upload .md file and search
  await page.goto("/app/inbox");
  const mdContent = "# Silicon Anode Test\nSilicon anode capacity reached 1950 mAh/g across 500 charge cycles.\n";
  await page.getByLabel("Upload a file").setInputFiles({
    name: "silicon-anode.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(mdContent),
  });
  await expect(page.getByTestId("k6-capture-preview")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: /^Save upload$/i }).click();
  await expect(page.getByRole("status").filter({ hasText: /^Uploaded/ })).toBeVisible({ timeout: 30_000 });
  await waitForDrain(page);

  await searchInput.fill("Silicon anode");
  await searchInput.press("Enter");
  await expect(page).toHaveURL(/\/app\/library\?q=Silicon%20anode/i);
  await expect(page.getByText(/Silicon Anode/i).first()).toBeVisible({ timeout: 30_000 });

  // --- 10. PAT creation and MCP config snippets
  await page.goto("/app/settings");
  // Token command: choosing that method shows its TTL field.
  await page.getByTestId("k6-auth-command").click();
  await expect(page.getByTestId("k6-token-ttl")).toBeVisible();
  // Fetch models fills the model list from the fake provider (this user is the first, so an admin).
  await page.getByTestId("k6-fetch-models").click();
  await expect.poll(async () => page.getByTestId("k6-model-options").locator("option").count(), { timeout: 30_000 }).toBeGreaterThan(0);
  await page.getByRole("tab", { name: /tokens & mcp/i }).click();
  await page.getByLabel("Token name").fill("smoke-test-pat");
  await page.getByRole("button", { name: /create token/i }).click();
  await expect(page.getByText(/Copy this now/i)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/claude mcp add/i)).toBeVisible();
  await expect(page.getByText(/opencode\.json/i)).toBeVisible();

  // --- 11. Shell pages render
  for (const path of [
    "/app/sources",
    "/app/library",
    "/app/timeline",
    "/app/jobs",
    "/app/evals",
    "/app/settings",
  ]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 30_000 });
  }

  // --- 12. Non-admin user sees administrator restriction on settings
  const nonAdminContext = await page.context().browser()!.newContext();
  const nonAdminPage = await nonAdminContext.newPage();
  const nonAdminEmail = `nonadmin-${Date.now()}@test.local`;
  await nonAdminPage.goto("/login");
  await nonAdminPage.getByRole("button", { name: /need an account/i }).click();
  await nonAdminPage.getByLabel("Email").fill(nonAdminEmail);
  await nonAdminPage.getByLabel("Password").fill("nonadmin-password-1234");
  await nonAdminPage.getByRole("button", { name: /create account/i }).click();
  await expect(nonAdminPage).toHaveURL(/\/app/, { timeout: 30_000 });
  await nonAdminPage.goto("/app/settings");
  await expect(
    nonAdminPage.getByText(/Administrator privileges are required to view or configure instance model settings/i),
  ).toBeVisible({ timeout: 30_000 });
  await nonAdminContext.close();

  // --- 13. Responsive 375px check on fully populated data
  await page.setViewportSize({ width: 375, height: 812 });
  for (const path of ["/app", "/app/ask", "/app/conflicts", "/app/wiki", "/app/changes", "/app/library"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(1);
  }

  expect(email).toContain("@");
});

test("an unauthenticated visit is sent to sign in", async ({ page }) => {
  await page.context().clearCookies();
  await page.goto("/app/conflicts");
  await expect(page).toHaveURL(/\/login\?next=/);
});

test("the interface is actually styled", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await signUp(page);
  await page.goto("/app");
  await page.waitForLoadState("networkidle");

  const styled = await page.evaluate(() => {
    const stylesheets = document.styleSheets.length;
    const main = document.querySelector("main");
    const sidebar = document.querySelector("aside");
    if (!main || !sidebar) return { stylesheets, laidOut: false };
    const mainBox = main.getBoundingClientRect();
    const sideBox = sidebar.getBoundingClientRect();
    return {
      stylesheets,
      laidOut: sideBox.width > 0 && sideBox.right <= mainBox.left + sideBox.width,
      background: getComputedStyle(document.body).backgroundColor,
    };
  });

  expect(styled.stylesheets, "no stylesheet was applied").toBeGreaterThan(0);
  expect(styled.laidOut, "the sidebar did not lay out beside the content").toBe(true);
  expect(styled.background, "the theme tokens were not applied").not.toBe("rgb(255, 255, 255)");
});

test("library filters survive a reload", async ({ page }) => {
  await signUp(page);
  await page.goto("/app/library");
  await page.getByTestId("k6-filter-starred").selectOption("true");
  await expect(page).toHaveURL(/starred=true/);
  await page.reload();
  await expect(page.getByTestId("k6-filter-starred")).toHaveValue("true");
});

test("the jobs page has a schedules tab", async ({ page }) => {
  await signUp(page);
  await page.goto("/app/jobs");
  await page.getByText("Schedules", { exact: true }).first().click();
  await expect(page.getByText("No schedules").or(page.getByTestId("k6-schedules-table"))).toBeVisible({
    timeout: 30_000,
  });
});

test("the timeline keeps its date range in the URL", async ({ page }) => {
  await signUp(page);
  await page.goto("/app/timeline?from=2026-01-01");
  await expect(page.locator("label", { hasText: "From" }).locator("input")).toHaveValue("2026-01-01");
});

test("a source can be put on a refresh schedule", async ({ page }) => {
  await signUp(page);
  await page.goto("/app/inbox");
  await page.getByLabel("Upload a file").setInputFiles({
    name: "schedule-me.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Schedule me\n\nA sentence about the HX-2 cell.\n"),
  });
  await expect(page.getByTestId("k6-capture-preview")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: /^Save upload$/i }).click();
  await expect(page.getByRole("status").filter({ hasText: /^Uploaded/ })).toBeVisible({ timeout: 30_000 });
  await page.goto("/app/sources");
  await page.locator("a[href^='/app/sources/']").first().click();
  await expect(page.getByTestId("k6-schedule-card")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: /^Create schedule$/i }).click();
  await expect(page.getByTestId("k6-schedule-next-run")).toBeVisible({ timeout: 30_000 });
});
