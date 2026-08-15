import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
const shotId = "020f9248-14b7-4f92-9edd-ee587ffdedf3";
const viewports = [
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1280x800", width: 1280, height: 800 },
  { name: "1024x768", width: 1024, height: 768 },
] as const;
const results: Array<Record<string, unknown>> = [];

test.afterAll(() => {
  const root = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  const output = path.join(root, "docs", "evidence", "g10", "ai-draft-review-uat-2026-08-15.json");
  fs.mkdirSync(path.dirname(output), { recursive: true });
  const passed = results.length === viewports.length && results.every((item) => item.status === "PASS");
  fs.writeFileSync(output, `${JSON.stringify({
    schema_version: "fr-wrt-007-production-readonly-uat.v1",
    observed_at: new Date().toISOString(),
    mode: "LOCAL_ONLY",
    status: passed ? "PASS" : "IN_PROGRESS",
    project_id: projectId,
    production_mutated: false,
    automatic_apply: false,
    screenshots_created: false,
    viewports: results,
  }, null, 2)}\n`, "utf8");
});

for (const viewport of viewports) {
  test(`reviews persisted AI draft without applying it at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    const failedResponses: string[] = [];
    const publicRequests: string[] = [];
    const writes: string[] = [];
    const originalMedia: string[] = [];
    page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    page.on("response", (response) => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`); });
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (!["127.0.0.1", "localhost"].includes(url.hostname)) publicRequests.push(request.url());
      if (request.method() !== "GET") writes.push(`${request.method()} ${url.pathname}`);
      if (url.pathname.includes("/content") || url.pathname.includes("/original")) originalMedia.push(request.url());
    });
    const params = new URLSearchParams({ view: "projects", project: projectId, episode: episodeId, shot: shotId });
    await page.goto(`/?${params.toString()}`, { waitUntil: "networkidle" });

    const panel = page.locator(".ai-draft-panel");
    await expect(panel.getByRole("heading", { name: "AI 辅助提取草稿" })).toBeVisible();
    await expect(panel.getByText(/不会自动创建或覆盖母本场次、镜头或创作资料/)).toBeVisible();
    await expect(panel.getByText("DRAFT_READY · NOT_APPLIED", { exact: true }).first()).toBeVisible();
    await expect(panel.getByText(/证据完整 · 置信度/)).toBeVisible();
    await expect(panel.getByText(/Profile 08789ef4-9449-56d2-8c88-6b06fc274465/)).toBeVisible();
    await expect(panel.locator("button")).toHaveCount(0);

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    const panelOverflow = await panel.evaluate((root) => root.scrollWidth - root.clientWidth);
    const shortControls = await panel.evaluate((root) => Array.from(root.querySelectorAll<HTMLElement>("button,input,select,textarea,summary")).filter((element) => {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && rect.height < 40;
    }).map((element) => ({ tag: element.tagName, height: element.getBoundingClientRect().height })));
    const passed = overflow === 0 && panelOverflow === 0 && shortControls.length === 0 && consoleErrors.length === 0 && pageErrors.length === 0 && failedResponses.length === 0 && publicRequests.length === 0 && writes.length === 0 && originalMedia.length === 0;
    results.push({
      viewport: viewport.name,
      status: passed ? "PASS" : "FAIL",
      persisted_draft_visible: true,
      structured_evidence_visible: true,
      application_status: "NOT_APPLIED",
      apply_controls: 0,
      horizontal_overflow_px: overflow,
      panel_overflow_px: panelOverflow,
      controls_below_40px: shortControls,
      console_errors: consoleErrors,
      page_errors: pageErrors,
      failed_responses: failedResponses,
      public_requests: publicRequests,
      write_requests: writes,
      original_media_requests: originalMedia,
    });
    expect(passed).toBe(true);
  });
}
