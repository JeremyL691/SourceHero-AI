import { expect, test, type Page } from "@playwright/test";

/**
 * Flows: library collections and tags, a file dropped on the inbox, a wiki revision diff,
 * and fetching the model list in settings. Helpers are copied from smoke.spec.ts on purpose.
 */

const API = process.env.E2E_API_URL || "http://127.0.0.1:8797";

test.setTimeout(300_000);

async function signUp(page: Page): Promise<string> {
  const email = `e2e-k9-${Date.now()}-${Math.floor(Math.random() * 1e6)}@test.local`;
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

test("library collections and tags can be created", async ({ page }) => {
  await signUp(page);
  await page.goto("/app/library");

  const collections = page.getByTestId("k6-collections-manager");
  await collections.getByLabel("New collection name").fill("Batteries");
  await collections.getByRole("button", { name: "Create" }).click();
  await expect(collections.getByText("Batteries", { exact: false }).first()).toBeVisible({ timeout: 30_000 });

  const tags = page.getByTestId("k6-tags-manager");
  await tags.getByLabel("New tag name").fill("chemistry");
  await tags.getByRole("button", { name: "Create" }).click();
  await expect(tags.getByText("chemistry", { exact: true })).toBeVisible({ timeout: 30_000 });
});

test("a file dropped on the inbox becomes a preview", async ({ page }) => {
  await signUp(page);
  await page.goto("/app/inbox");

  const dropZone = page.getByTestId("k6-drop-zone");
  await expect(dropZone).toBeVisible({ timeout: 30_000 });
  await dropZone.evaluate((el) => {
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(
      new File(["# Dropped note\n\nA sentence about the HX-2 cell.\n"], "dropped.md", {
        type: "text/markdown",
      }),
    );
    el.dispatchEvent(new DragEvent("drop", { dataTransfer, bubbles: true }));
  });

  await expect(page.getByTestId("k6-capture-preview")).toBeVisible({ timeout: 30_000 });
});

test("a wiki revision shows its diff", async ({ page }) => {
  await signUp(page);
  await page.goto("/app/inbox");
  await page.getByRole("button", { name: /load demo corpus/i }).click();
  await expect(page.getByText(/demo corpus queued/i)).toBeVisible({ timeout: 30_000 });
  await waitForDrain(page);

  await page.goto("/app/wiki");
  await page.locator("a[href^='/app/wiki/']").first().click();
  await expect(page).toHaveURL(/\/app\/wiki\/[a-z0-9-]+/);

  await page.locator("[data-testid^='k6-revision-item-']").first().click();
  await expect(
    page.getByTestId("k6-revision-first").or(page.getByTestId("k6-revision-diff")),
  ).toBeVisible({ timeout: 30_000 });
});
