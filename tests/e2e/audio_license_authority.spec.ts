import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7", episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1", shotId = "020f9248-14b7-4f92-9edd-ee587ffdedf3";
const viewports = [{ name: "1440x900", width: 1440, height: 900 }, { name: "1280x800", width: 1280, height: 800 }, { name: "1024x768", width: 1024, height: 768 }] as const;
const results: Array<Record<string, unknown>> = [];

test.afterAll(() => {
  const root = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  const output = path.join(root, "docs", "evidence", "g10", "audio-license-authority-uat-2026-08-15.json");
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify({ schema_version: "fr-aud-002-license-readonly-uat.v1", observed_at: new Date().toISOString(), mode: "LOCAL_ONLY", status: results.length === 3 && results.every((item) => item.status === "PASS") ? "PASS" : "IN_PROGRESS", project_id: projectId, episode_id: episodeId, legacy_binding_count: 4, verified_evidence_count: 0, screenshots_created: false, viewports: results }, null, 2)}\n`, "utf8");
});

for (const viewport of viewports) test(`does not count legacy license labels at ${viewport.name}`, async ({ page }) => {
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  const consoleErrors: string[] = [], pageErrors: string[] = [], failedResponses: string[] = [], publicRequests: string[] = [], writes: string[] = [], originalMedia: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("response", (response) => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`); });
  page.on("request", (request) => { const url = new URL(request.url()); if (!["127.0.0.1", "localhost"].includes(url.hostname)) publicRequests.push(request.url()); if (request.method() !== "GET" && url.pathname.includes("audio-bindings")) writes.push(`${request.method()} ${url.pathname}`); if (/\.(wav|flac|mp3|mp4)(\?|$)/i.test(url.pathname) || url.pathname.includes("/original")) originalMedia.push(request.url()); });
  await page.goto(`/?${new URLSearchParams({ view: "projects", project: projectId, episode: episodeId, shot: shotId })}`, { waitUntil: "networkidle" });
  const timeline = page.locator(".timeline-status-panel");
  await expect(timeline.getByText("本地授权音频：0")).toBeVisible();
  const tracks = page.locator(".audio-track-panel");
  await expect(tracks.getByRole("heading", { name: "音效、环境与音乐绑定" })).toBeVisible();
  await expect(tracks.getByText("遗留授权证据不完整")).toHaveCount(4);
  await expect(tracks.locator("audio[preload='none']")).toHaveCount(4);
  const toggle = tracks.getByRole("button", { name: "导入并绑定本地音频" });
  await expect(toggle).toBeVisible();
  await toggle.click();
  await expect(tracks.getByRole("button", { name: "收起本地音频绑定" })).toHaveAttribute("aria-expanded", "true");
  await expect(tracks.getByLabel("本地音频绝对路径")).toBeVisible();
  await expect(tracks.getByLabel("项目内授权证据相对路径")).toBeVisible();
  await expect(tracks.locator(".audio-import-binding select").nth(0)).toHaveValue("");
  await expect(tracks.locator(".audio-import-binding select").nth(1)).toHaveValue("");
  await expect(tracks.getByRole("button", { name: "校验、导入并绑定" })).toBeVisible();
  const undersizedControls = await tracks.locator(".audio-import-binding button, .audio-import-binding input:not([type='checkbox']), .audio-import-binding select").evaluateAll((nodes) => nodes.filter((node) => node.getBoundingClientRect().height < 40).map((node) => ({ tag: node.tagName, height: node.getBoundingClientRect().height })));
  const gate = page.locator(".gate-readiness");
  await expect(gate.getByText("IN_PROGRESS")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  const passed = overflow === 0 && undersizedControls.length === 0 && consoleErrors.length === 0 && pageErrors.length === 0 && failedResponses.length === 0 && publicRequests.length === 0 && writes.length === 0 && originalMedia.length === 0;
  results.push({ viewport: viewport.name, status: passed ? "PASS" : "FAIL", legacy_binding_count: 4, verified_evidence_count: 0, g8_audio_check: "FAIL", form_explicit_choices: true, undersized_controls: undersizedControls, horizontal_overflow_px: overflow, console_errors: consoleErrors, page_errors: pageErrors, failed_responses: failedResponses, public_requests: publicRequests, write_requests: writes, original_media_requests: originalMedia });
  expect(passed).toBe(true);
});
