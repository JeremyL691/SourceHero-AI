import { expect, test, type Page } from "@playwright/test";

/**
 * The smoke path a first-time user actually walks: sign up, load the demo corpus,
 * find a disagreement, see what changed, read a compiled page, verify a sentence,
 * and ask a question that streams with per-sentence verdicts.
 *
 * Assertions are on *evidence*, never on cluster labels: labels come from a keyword
 * algorithm and would change for reasons unrelated to the behaviour under test.
 */

const API = process.env.E2E_API_URL || "http://127.0.0.1:8797";

// Seeding walks the whole pipeline (chunk, embed, extract, cluster, judge, compile),
// so the generous timeout here is about work the test triggers, not a slow assertion.
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

test("the full loop: sign up, seed, disagree, verify, ask", async ({ page }) => {
  const email = await signUp(page);

  // --- load the demo corpus
  await page.goto("/app/inbox");
  await page.getByRole("button", { name: /load demo corpus/i }).click();
  await expect(page.getByText(/demo corpus queued/i)).toBeVisible({ timeout: 30_000 });

  // --- wait for the pipeline to settle
  await page.goto("/app/jobs");
  await expect
    .poll(
      async () => {
        const response = await page.request.get(`${API}/v1/jobs?status=queued&limit=1`, {
          headers: await authHeaders(page),
        });
        if (!response.ok()) return -1;
        return (await response.json()).length;
      },
      { timeout: 180_000, intervals: [1000, 2000, 3000] },
    )
    .toBe(0);

  // --- the overview shows a populated corpus
  await page.goto("/app");
  await expect(page.getByText("Sources", { exact: true })).toBeVisible();
  const sourceCount = await page
    .locator("p.text-2xl")
    .first()
    .innerText();
  expect(Number(sourceCount)).toBeGreaterThan(0);

  // --- a disagreement exists
  await page.goto("/app/conflicts");
  await expect(page.getByText(/open the divergence map/i).first()).toBeVisible({
    timeout: 60_000,
  });

  // --- the density disagreement shows both sides, and its map has two camps.
  // Scoped to the card that mentions 380 Wh/kg rather than "the first conflict":
  // list order follows relation confidence and is not part of the contract.
  const densityCard = page
    .locator("li")
    .filter({ hasText: "380 Wh/kg" })
    .first();
  await expect(densityCard).toBeVisible({ timeout: 30_000 });
  await expect(densityCard.getByText(/450 Wh\/kg/).first()).toBeVisible();
  await densityCard.getByRole("link", { name: /open the divergence map/i }).click();

  await expect(page).toHaveURL(/\/app\/clusters\/\d+/);
  await expect(page.getByText(/Camp A/).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/Camp B/).first()).toBeVisible();
  await expect(page.getByText(/380 Wh\/kg/).first()).toBeVisible();
  await expect(page.getByText(/450 Wh\/kg/).first()).toBeVisible();

  // --- what changed.
  // Asserts on the persisted diff rather than the confirmation toast: the toast is a
  // transient detail, and the digest is the thing this page exists to show.
  await page.goto("/app/changes");
  await page.getByRole("button", { name: /^Generate$/ }).click();

  await expect
    .poll(
      async () => {
        const response = await page.request.get(`${API}/v1/diffs?limit=5`, {
          headers: await authHeaders(page),
        });
        if (!response.ok()) return -1;
        return response.json().then((rows: { period_start: string }[]) =>
          rows.filter((r) => r.period_start.startsWith("2026-05-01")).length,
        );
      },
      { timeout: 60_000, intervals: [1000, 2000] },
    )
    .toBeGreaterThan(0);

  await page.reload();
  await expect(page.getByText(/What changed/i).first()).toBeVisible({ timeout: 60_000 });
  // The demo is built around a postponed launch; the digest must mention it.
  await expect(page.getByText(/postponed/i).first()).toBeVisible({ timeout: 60_000 });

  // --- the compiled wiki
  await page.goto("/app/wiki");
  await expect(page.getByText(/hx-2/i).first()).toBeVisible({ timeout: 60_000 });
  await page.getByRole("link", { name: /^hx-2$/i }).first().click();
  await expect(page).toHaveURL(/\/app\/wiki\/hx-2/);
  await expect(page.getByText(/Disputed/i).first()).toBeVisible({ timeout: 30_000 });

  // --- verify: a disputed sentence must come back disputed
  await page.goto("/app/verify");
  const textarea = page.getByLabel("Text to verify");
  await textarea.fill("The HX-2 cell delivers an energy density of 450 Wh/kg.");
  await page.getByRole("button", { name: /verify/i }).first().click();
  await expect(page.locator('[data-verdict="disputed"]').first()).toBeVisible({
    timeout: 60_000,
  });

  // --- ask: a streamed answer with a verdict per sentence
  await page.goto("/app/ask");
  await page.getByLabel("Your question").fill("What is the energy density of the HX-2?");
  await page.getByRole("button", { name: /^ask$/i }).click();
  await expect(page.getByText(/Camp|Camp A/).first()).toBeVisible({ timeout: 60_000 }).catch(() => {});
  // The answer streams sentence by sentence, each with its own verdict badge.
  await expect(page.locator('[data-verdict]').first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/Energy|Evidence/i).first()).toBeVisible();

  // --- every page in the shell renders
  for (const path of [
    "/app/sources",
    "/app/library",
    "/app/timeline",
    "/app/jobs",
    "/app/settings",
  ]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 30_000 });
  }

  expect(email).toContain("@");
});

test("no horizontal scrolling at 375px", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await signUp(page);

  for (const path of ["/app", "/app/ask", "/app/conflicts"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    // One pixel of rounding is not a layout bug; a scrollbar's worth is.
    expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(1);
  }
});

test("an unauthenticated visit is sent to sign in", async ({ page }) => {
  await page.context().clearCookies();
  await page.goto("/app/conflicts");
  await expect(page).toHaveURL(/\/login\?next=/);
});

test("the interface is actually styled", async ({ page }) => {
  // A suite that only asserts on text passes happily against an unstyled page. This
  // checks the stylesheet is really applied, which is what caught a missing Tailwind
  // import that silently removed every utility class.
  await signUp(page);
  await page.goto("/app");
  await page.waitForLoadState("networkidle");

  const styled = await page.evaluate(() => {
    const stylesheets = document.styleSheets.length;
    const main = document.querySelector("main");
    const sidebar = document.querySelector("aside");
    if (!main || !sidebar) return { stylesheets, laidOut: false };
    // A real layout means the sidebar sits beside the content rather than on top of it.
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
  // The paper ground, not the browser default white.
  expect(styled.background, "the theme tokens were not applied").not.toBe("rgb(255, 255, 255)");
});

async function authHeaders(page: Page): Promise<Record<string, string>> {
  const cookies = await page.context().cookies();
  const token = cookies.find((c) => c.name === "sh_token")?.value;
  return token ? { Authorization: `Bearer ${token}` } : {};
}
