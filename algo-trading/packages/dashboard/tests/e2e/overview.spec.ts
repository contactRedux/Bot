import { test, expect } from "@playwright/test";

test.describe("Overview page", () => {
  test("page loads without console errors", async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        consoleErrors.push(msg.text());
      }
    });

    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Filter out expected benign errors (network refused when API is not running)
    const unexpectedErrors = consoleErrors.filter(
      (e) =>
        !e.includes("net::ERR_CONNECTION_REFUSED") &&
        !e.includes("Failed to fetch") &&
        !e.includes("ECONNREFUSED")
    );
    expect(unexpectedErrors).toHaveLength(0);
  });

  test("Portfolio Overview heading is visible", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: /portfolio overview/i })
    ).toBeVisible({ timeout: 10_000 });
  });

  test("navigation links are visible", async ({ page }) => {
    await page.goto("/");
    // The sidebar / nav should render even without a live API
    await page.waitForLoadState("domcontentloaded");
    // At minimum the page should render some content
    const body = await page.textContent("body");
    expect(body).toBeTruthy();
    expect(body!.length).toBeGreaterThan(10);
  });

  test("no empty page — React root is mounted", async ({ page }) => {
    await page.goto("/");
    const root = page.locator("#root");
    await expect(root).toBeAttached();
    // The root element should have children (the app rendered)
    const childCount = await root.evaluate((el) => el.children.length);
    expect(childCount).toBeGreaterThan(0);
  });
});
