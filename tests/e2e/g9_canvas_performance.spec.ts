import { expect, test } from "@playwright/test";

const viewports = [
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1280x800", width: 1280, height: 800 },
  { name: "1024x768", width: 1024, height: 768 },
] as const;

for (const viewport of viewports) {
  test(`renders the real 300-node G9 fixture without browser errors at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const consoleErrors: string[] = [];
    const consoleWarnings: string[] = [];
    const pageErrors: string[] = [];
    const failedResponses: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
      if (message.type() === "warning") consoleWarnings.push(message.text());
    });
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    page.on("response", (response) => {
      if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`);
    });

    const started = Date.now();
    await page.goto("/?view=canvas", { waitUntil: "domcontentloaded" });
    await expect(page.getByText("显示：300/300 节点")).toBeVisible({ timeout: 60_000 });
    const canvasReadyMs = Date.now() - started;
    const nodeCount = await page.locator(".react-flow__node").count();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

    expect(nodeCount).toBe(300);
    expect(overflow).toBe(0);
    expect(consoleErrors).toEqual([]);
    expect(consoleWarnings).toEqual([]);
    expect(pageErrors).toEqual([]);
    expect(failedResponses).toEqual([]);
    expect(canvasReadyMs).toBeLessThan(60_000);
  });
}
