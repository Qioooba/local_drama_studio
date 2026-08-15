import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
const shotId = "020f9248-14b7-4f92-9edd-ee587ffdedf3";
const reviewId = "bf6f2151-a104-48d4-9c85-b89a7bad68c9";
const viewports = [
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1280x800", width: 1280, height: 800 },
  { name: "1024x768", width: 1024, height: 768 },
] as const;
const results: Array<Record<string, unknown>> = [];

test.setTimeout(90_000);
test.afterAll(() => {
  const root = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  const output = path.join(root, "docs", "evidence", "g10", "video-annotations-uat-2026-08-15.json");
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify({
    schema_version: "fr-vid-008-production-readonly-uat.v1",
    observed_at: new Date().toISOString(),
    mode: "LOCAL_ONLY",
    status: results.length === viewports.length && results.every((item) => item.status === "PASS") ? "PASS" : "IN_PROGRESS",
    project_id: projectId,
    media_version_id: reviewId,
    production_mutated: false,
    screenshots_created: false,
    visual_source_policy: "UI requests only size=small thumbnails; no original media",
    viewports: results,
  }, null, 2)}\n`, "utf8");
});

for (const viewport of viewports) {
  test(`shows the immutable video annotation form safely at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    const failedResponses: string[] = [];
    const publicRequests: string[] = [];
    const originalMediaRequests: string[] = [];
    const annotationWrites: string[] = [];
    page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    page.on("response", (response) => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`); });
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (!['127.0.0.1', 'localhost'].includes(url.hostname)) publicRequests.push(request.url());
      if (/\.(mp4|mov|mkv|webm|wav|flac)(\?|$)/i.test(url.pathname) || url.pathname.includes("/original")) originalMediaRequests.push(request.url());
      if (request.method() !== "GET" && /\/media-versions\/[^/]+\/annotations$/.test(url.pathname)) annotationWrites.push(`${request.method()} ${url.pathname}`);
    });
    const params = new URLSearchParams({ view: "reviews", project: projectId, episode: episodeId, shot: shotId, review: reviewId });
    await page.goto(`/?${params.toString()}`, { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { name: "时间码问题标记" })).toBeVisible();
    await expect(page.getByLabel("时间码（毫秒）")).toHaveAttribute("max", "4457");
    await expect(page.getByRole("button", { name: "保存标记" })).toBeDisabled();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    const shortControls = await page.locator(".video-annotations").evaluate((root) => Array.from(root.querySelectorAll<HTMLElement>("button, input, select, textarea")).filter((element) => {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0 && rect.height < 40;
    }).map((element) => ({ tag: element.tagName, height: element.getBoundingClientRect().height })));
    const passed = overflow === 0 && shortControls.length === 0 && consoleErrors.length === 0 && pageErrors.length === 0 && failedResponses.length === 0 && publicRequests.length === 0 && originalMediaRequests.length === 0 && annotationWrites.length === 0;
    results.push({ viewport: viewport.name, status: passed ? "PASS" : "FAIL", horizontal_overflow_px: overflow, controls_below_40px: shortControls, console_errors: consoleErrors, page_errors: pageErrors, failed_responses: failedResponses, public_requests: publicRequests, original_media_requests: originalMediaRequests, annotation_writes: annotationWrites });
    expect(passed).toBe(true);
  });
}
