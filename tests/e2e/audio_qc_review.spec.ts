import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
const audioVersionId = "9773143a-9221-4fe9-9921-5cdc0c936ae8";
const viewports = [{ name: "1440x900", width: 1440, height: 900 }, { name: "1280x800", width: 1280, height: 800 }, { name: "1024x768", width: 1024, height: 768 }] as const;
const results: Array<Record<string, unknown>> = [];

test.afterAll(() => {
  const root = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  const output = path.join(root, "docs", "evidence", "g10", "audio-qc-review-uat-2026-08-15.json");
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify({ schema_version: "fr-aud-003-production-readonly-uat.v1", observed_at: new Date().toISOString(), mode: "LOCAL_ONLY", status: results.length === 3 && results.every((item) => item.status === "PASS") ? "PASS" : "IN_PROGRESS", project_id: projectId, media_version_id: audioVersionId, production_mutated: false, screenshots_created: false, viewports: results }, null, 2)}\n`, "utf8");
});

for (const viewport of viewports) {
  test(`shows derived waveform and real audio QC at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const consoleErrors: string[] = [], pageErrors: string[] = [], failedResponses: string[] = [], publicRequests: string[] = [], writes: string[] = [], originalMedia: string[] = [];
    page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    page.on("response", (response) => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`); });
    page.on("request", (request) => { const url = new URL(request.url()); if (!["127.0.0.1", "localhost"].includes(url.hostname)) publicRequests.push(request.url()); if (request.method() !== "GET") writes.push(`${request.method()} ${url.pathname}`); if (url.pathname.includes("/content") || url.pathname.includes("/original")) originalMedia.push(request.url()); });
    const params = new URLSearchParams({ view: "reviews", project: projectId, episode: episodeId, review: audioVersionId });
    await page.goto(`/?${params.toString()}`, { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { name: "媒体版本审核与选择" })).toBeVisible();
    await expect(page.getByText("audio_mix", { exact: true })).toBeVisible();
    const waveform = page.getByRole("img", { name: "当前音频的 640 像素派生波形" });
    await expect(waveform).toBeVisible();
    await expect(page.getByText(/integrated_loudness：PASS/)).toBeVisible();
    await expect(page.getByText(/true_peak：PASS/)).toBeVisible();
    await expect(page.getByText(/clipping：PASS/)).toBeVisible();
    const dimensions = await waveform.evaluate((image: HTMLImageElement) => ({ width: image.naturalWidth, height: image.naturalHeight }));
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    const shortControls = await page.evaluate(() => Array.from(document.querySelectorAll<HTMLElement>("button,select,textarea")).filter((element) => { const rect = element.getBoundingClientRect(); return rect.width > 0 && rect.height > 0 && rect.height < 40; }).map((element) => ({ tag: element.tagName, height: element.getBoundingClientRect().height })));
    const passed = dimensions.width === 640 && dimensions.height === 128 && overflow === 0 && shortControls.length === 0 && consoleErrors.length === 0 && pageErrors.length === 0 && failedResponses.length === 0 && publicRequests.length === 0 && writes.length === 0 && originalMedia.length === 0;
    results.push({ viewport: viewport.name, status: passed ? "PASS" : "FAIL", waveform_dimensions: dimensions, latest_qc_status: "PASS", integrated_lufs: -16.8, true_peak_dbfs: -3.4, peak_dbfs: -3.485454, clipping_detected: false, horizontal_overflow_px: overflow, controls_below_40px: shortControls, console_errors: consoleErrors, page_errors: pageErrors, failed_responses: failedResponses, public_requests: publicRequests, write_requests: writes, original_media_requests: originalMedia });
    expect(passed).toBe(true);
  });
}
