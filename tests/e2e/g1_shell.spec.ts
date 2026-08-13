import { test, expect } from "@playwright/test";

test("G1 shell exposes local-only contract", async ({ page }) => {
  await page.goto("http://127.0.0.1:5173");
  await expect(page.getByText("LOCAL_ONLY").first()).toBeVisible();
  await expect(page.getByText("G1 工程骨架进行中")).toBeVisible();
});

