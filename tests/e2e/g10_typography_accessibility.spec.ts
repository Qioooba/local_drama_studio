import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
const reviewId = "0d389e44-0fc3-47e2-b492-f7e3501ccf0c";
const shotId = "020f9248-14b7-4f92-9edd-ee587ffdedf3";
const viewports = [
  { name: "1440x900", width: 1440, height: 900, view: "generation" },
  { name: "1280x800", width: 1280, height: 800, view: "reviews" },
  { name: "1024x768", width: 1024, height: 768, view: "canvas" },
] as const;

test.setTimeout(90_000);
const results: Array<Record<string, unknown>> = [];

test.afterAll(() => {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  const output = path.join(cwd, "docs", "evidence", "g10", "typography-accessibility-uat-2026-08-15.json");
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify({
    schema_version: "g10-typography-accessibility-uat.v1",
    observed_at: new Date().toISOString(),
    mode: "LOCAL_ONLY",
    status: results.length === viewports.length && results.every((result) => result.status === "PASS") ? "PASS" : "IN_PROGRESS",
    minimum_visible_font_px: 12,
    minimum_enabled_control_height_px: 40,
    original_media_requested: false,
    viewports: results,
  }, null, 2)}\n`, "utf8");
});

for (const viewport of viewports) {
  test(`keeps visible UI text and controls accessible at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    const failedResponses: string[] = [];
    const originalMediaRequests: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    page.on("response", (response) => {
      if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`);
    });
    page.on("request", (request) => {
      const url = request.url().toLowerCase();
      if (/\.(mp4|mov|mkv|wav|flac)(\?|$)/.test(url) || url.includes("/original")) {
        originalMediaRequests.push(request.url());
      }
    });

    const params = new URLSearchParams({
      view: viewport.view,
      project: projectId,
      episode: episodeId,
      shot: shotId,
    });
    if (viewport.view === "reviews") params.set("review", reviewId);
    await page.goto(`/?${params.toString()}`, { waitUntil: "networkidle" });
    await expect(page.locator("#workspace-content")).toBeVisible();

    const tooSmall = await page.locator("#root .shell").evaluate((root) => {
      const candidates = root.querySelectorAll<HTMLElement>(
        "button, label, legend, small, code, p, span, strong, dt, dd, option",
      );
      return Array.from(candidates)
        .filter((element) => {
          const style = getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0 && element.innerText.trim();
        })
        .map((element) => ({
          tag: element.tagName.toLowerCase(),
          className: element.className,
          text: element.innerText.trim().slice(0, 80),
          fontSize: Number.parseFloat(getComputedStyle(element).fontSize),
        }))
        .filter((entry) => entry.fontSize < 12);
    });
    const shortControls = await page.locator("#root .shell").evaluate((root) =>
      Array.from(root.querySelectorAll<HTMLElement>("button, select, textarea"))
        .filter((element) => {
          const style = getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          return !element.hasAttribute("disabled") && style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
        })
        .map((element) => ({
          tag: element.tagName.toLowerCase(),
          className: element.className,
          label: element.getAttribute("aria-label") ?? element.innerText.trim().slice(0, 80),
          height: element.getBoundingClientRect().height,
        }))
        .filter((entry) => entry.height < 40),
    );
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

    const passed = tooSmall.length === 0 && shortControls.length === 0 && overflow === 0 && consoleErrors.length === 0 && pageErrors.length === 0 && failedResponses.length === 0 && originalMediaRequests.length === 0;
    results.push({
      viewport: viewport.name,
      view: viewport.view,
      status: passed ? "PASS" : "FAIL",
      visible_text_below_12px: tooSmall,
      enabled_controls_below_40px: shortControls,
      horizontal_overflow_px: overflow,
      console_errors: consoleErrors,
      page_errors: pageErrors,
      failed_responses: failedResponses,
      original_media_requests: originalMediaRequests,
    });

    expect(tooSmall).toEqual([]);
    expect(shortControls).toEqual([]);
    expect(overflow).toBe(0);
    expect(consoleErrors).toEqual([]);
    expect(pageErrors).toEqual([]);
    expect(failedResponses).toEqual([]);
    expect(originalMediaRequests).toEqual([]);
  });
}
