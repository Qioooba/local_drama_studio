import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
const shotId = "020f9248-14b7-4f92-9edd-ee587ffdedf3";
const viewports = [{ name: "1440x900", width: 1440, height: 900 }, { name: "1280x800", width: 1280, height: 800 }, { name: "1024x768", width: 1024, height: 768 }] as const;
const results: Array<Record<string, unknown>> = [];

test.afterAll(() => {
  const root = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  const output = path.join(root, "docs", "evidence", "g10", "continuity-panel-uat-2026-08-15.json");
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify({ schema_version: "fr-wrt-006-production-readonly-uat.v1", observed_at: new Date().toISOString(), mode: "LOCAL_ONLY", status: results.length === 3 && results.every((item) => item.status === "PASS") ? "PASS" : "IN_PROGRESS", project_id: projectId, episode_id: episodeId, shot_id: shotId, production_mutated: false, screenshots_created: false, visual_source_policy: "Only <=320px size=small thumbnails; no original media", viewports: results }, null, 2)}\n`, "utf8");
});

for (const viewport of viewports) {
  test(`compares adjacent continuity context read-only at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const consoleErrors: string[] = [], pageErrors: string[] = [], failedResponses: string[] = [], publicRequests: string[] = [], writes: string[] = [], originalMedia: string[] = [], unsafeThumbnails: string[] = [];
    page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    page.on("response", (response) => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`); });
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (!["127.0.0.1", "localhost"].includes(url.hostname)) publicRequests.push(request.url());
      if (request.method() !== "GET") writes.push(`${request.method()} ${url.pathname}`);
      if (/\.(mp4|mov|mkv|webm|wav|flac)(\?|$)/i.test(url.pathname) || url.pathname.includes("/content") || url.pathname.includes("/original")) originalMedia.push(request.url());
      if (url.pathname.includes("/thumbnail") && url.searchParams.get("size") !== "small") unsafeThumbnails.push(request.url());
    });
    const params = new URLSearchParams({ view: "generation", project: projectId, episode: episodeId, shot: shotId });
    await page.goto(`/?${params.toString()}`, { waitUntil: "networkidle" });
    const panel = page.locator(".continuity-panel");
    await expect(panel.getByRole("heading", { name: "跨镜头连续性" })).toBeVisible();
    await expect(panel.getByText("SHOT_001", { exact: true })).toBeVisible();
    await expect(panel.getByText("G8_SHOT_001", { exact: true })).toBeVisible();
    await expect(panel.getByText("无相邻镜头")).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    const panelOverflow = await panel.evaluate((root) => root.scrollWidth - root.clientWidth);
    const passed = overflow === 0 && panelOverflow === 0 && consoleErrors.length === 0 && pageErrors.length === 0 && failedResponses.length === 0 && publicRequests.length === 0 && writes.length === 0 && originalMedia.length === 0 && unsafeThumbnails.length === 0;
    results.push({ viewport: viewport.name, status: passed ? "PASS" : "FAIL", horizontal_overflow_px: overflow, panel_overflow_px: panelOverflow, console_errors: consoleErrors, page_errors: pageErrors, failed_responses: failedResponses, public_requests: publicRequests, write_requests: writes, original_media_requests: originalMedia, unsafe_thumbnail_requests: unsafeThumbnails });
    expect(passed).toBe(true);
  });
}
