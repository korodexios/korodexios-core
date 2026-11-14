import { expect, test } from "@playwright/test";

// Needs the daemon and the built app: playwright.config.ts starts both.
test("the app shows the live daemon and its module tabs", async ({ page }) => {
  await page.goto("/#token=e2e-token");
  await expect(page.getByTestId("state")).toContainText("running");
  await expect(page.getByRole("tab").first()).toBeVisible();
});
