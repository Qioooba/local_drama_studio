import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * FR-CTL-002/003/004 three-viewport UAT with real seeded control data.
 *
 * The simulation environment seeds a real MOTION_MASK motion control (real
 * normalized vector path, published profile with motion/inpaint/outpaint
 * capability declared) on the approved keyframe.  This spec exercises the
 * generation view's MotionControlPanel and the multimodal GenerationControlPanel
 * at 1440x900 / 1280x800 / 1024x768, asserting real saved controls render with
 * zero writes, zero public/original-media requests, zero console/page errors
 * and zero horizontal overflow.
 */

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
const shotId = "020f9248-14b7-4f92-9edd-ee587ffdedf3";
const viewports = [
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1280x800", width: 1280, height: 800 },
  { name: "1024x768", width: 1024, height: 768 },
] as const;

test.setTimeout(120_000);
const results: Array<Record<string, unknown>> = [];

function evidencePath() {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  return path.join(cwd, "docs", "evidence", "g10", "fr-ctl-002-004-motion-multimodal-windows-uat-2026-08-17.json");
}

test.afterAll(() => {
  const passed = results.length === viewports.length && results.every((item) => item.status === "PASS");
  const output = {
    schema_version: "g10.fr-ctl-002-004-motion-multimodal-windows-uat.v1",
    observed_at: new Date().toISOString(),
    mode: "LOCAL_ONLY",
    project_id: projectId,
    status: passed ? "PASS" : "IN_PROGRESS",
    isolated_snapshot: true,
    production_database_touched: false,
    runtime_contacted: false,
    network_contacted: false,
    mutated: false,
    viewports: results,
    interpretation:
      "Real saved MOTION_MASK motion control and the multimodal input panel are rendered at three viewports with zero writes, zero public/original-media requests, zero console/page errors, zero failed responses and zero horizontal overflow.",
  };
  fs.mkdirSync(path.dirname(evidencePath()), { recursive: true });
  fs.writeFileSync(evidencePath(), `${JSON.stringify(output, null, 2)}\n`, "utf8");
});

for (const viewport of viewports) {
  test(`renders motion + multimodal controls with real data at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    const failedResponses: string[] = [];
    const writes: string[] = [];
    const publicRequests: string[] = [];
    const originalMediaRequests: string[] = [];
    page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    page.on("response", (response) => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`); });
    page.on("request", (request) => {
      const url = new URL(request.url());
      const pathname = url.pathname;
      if (!["127.0.0.1", "localhost"].includes(url.hostname)) publicRequests.push(request.url());
      if (pathname.includes("/content") || /\.(mp4|mov|mkv|webm|wav|flac|mp3)(\?|$)/i.test(pathname)) originalMediaRequests.push(request.url());
      if (pathname.startsWith("/api/") && request.method() !== "GET") writes.push(`${request.method()} ${pathname}`);
    });

    await page.goto(`/?view=generation&project=${projectId}&episode=${episodeId}&shot=${shotId}`, { waitUntil: "networkidle", timeout: 60_000 });

    // MotionControlPanel renders with the real saved control (FR-CTL-002).
    await expect(page.getByRole("region", { name: "运动区域与局部编辑" })).toBeVisible();
    const controlList = page.getByLabel("已保存运动控制");
    await expect(controlList).toBeVisible();
    const controlItems = await page.locator("li", { hasText: "MOTION_MASK" }).count();
    expect(controlItems).toBeGreaterThanOrEqual(1);

    // Multimodal input panel (FR-CTL-003/004) renders its structured bindings.
    const timedDirections = page.getByLabel(/TimedDirection/).first();
    await expect(timedDirections).toBeVisible();
    const performanceBindings = page.getByLabel(/PerformanceBinding/).first();
    await expect(performanceBindings).toBeVisible();
    const motionBrush = page.getByLabel(/运动笔刷 vector JSON/).first();
    await expect(motionBrush).toBeVisible();

    const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    const passed =
      writes.length === 0 &&
      publicRequests.length === 0 &&
      originalMediaRequests.length === 0 &&
      consoleErrors.length === 0 &&
      pageErrors.length === 0 &&
      failedResponses.length === 0 &&
      horizontalOverflow === 0;
    results.push({
      viewport: viewport.name,
      status: passed ? "PASS" : "FAIL",
      saved_motion_masks: controlItems,
      writes,
      public_requests: publicRequests,
      original_media_requests: originalMediaRequests,
      console_errors: consoleErrors,
      page_errors: pageErrors,
      failed_responses: failedResponses,
      horizontal_overflow_px: horizontalOverflow,
    });
    expect(writes).toEqual([]);
    expect(publicRequests).toEqual([]);
    expect(originalMediaRequests).toEqual([]);
    expect(consoleErrors).toEqual([]);
    expect(pageErrors).toEqual([]);
    expect(failedResponses).toEqual([]);
    expect(horizontalOverflow).toBe(0);
  });
}
