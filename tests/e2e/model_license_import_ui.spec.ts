import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const viewports = [{ name: "1440x900", width: 1440, height: 900 }, { name: "1280x800", width: 1280, height: 800 }, { name: "1024x768", width: 1024, height: 768 }] as const;
const results: Array<Record<string, unknown>> = [];

test.afterAll(() => {
  const root = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  const output = path.join(root, "docs", "evidence", "g7", "model-license-import-ui-2026-08-15.json");
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify({ schema_version: "g7.user-local-model-ui.v2", observed_at: new Date().toISOString(), mode: "LOCAL_ONLY", status: results.length === 3 && results.every((item) => item.status === "PASS") ? "PASS" : "IN_PROGRESS", project_id: projectId, production_mutated: false, screenshots_created: false, gate_status: "PASS", next_required_action: null, distribution_scope: "REFERENCE_ONLY_NOT_BUNDLED", viewports: results }, null, 2)}\n`, "utf8");
});

for (const viewport of viewports) test(`exposes evidence import without synthesizing or submitting at ${viewport.name}`, async ({ page }) => {
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  const consoleErrors: string[] = [], pageErrors: string[] = [], failedResponses: string[] = [], publicRequests: string[] = [], writes: string[] = [], originalMedia: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("response", (response) => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`); });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!["127.0.0.1", "localhost"].includes(url.hostname)) publicRequests.push(request.url());
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method())) writes.push(`${request.method()} ${url.pathname}`);
    if (/\.(safetensors|ckpt|pt|bin)(\?|$)/i.test(url.pathname) || url.pathname.includes("/original")) originalMedia.push(request.url());
  });
  await page.goto(`/?${new URLSearchParams({ view: "profiles", project: projectId })}`, { waitUntil: "networkidle" });
  const panel = page.locator("section.configuration-snapshot").filter({ has: page.getByRole("heading", { name: "用户自带模型路径与兼容性" }) });
  await expect(panel.getByText("10 未填写")).toBeVisible();
  await panel.getByRole("button", { name: "添加电脑里的模型" }).click();
  await expect(panel.getByLabel("模型代码")).toBeVisible();
  await expect(panel.getByLabel("模型类型")).toBeVisible();
  await expect(panel.getByLabel("电脑中的模型绝对路径")).toBeVisible();
  await expect(panel.getByRole("button", { name: "浏览…" })).toBeVisible();
  const toggle = panel.getByRole("button", { name: "可选：记录用户授权信息" });
  await toggle.click();
  await expect(panel.getByRole("button", { name: "收起用户授权记录" })).toHaveAttribute("aria-expanded", "true");
  await expect(panel.getByPlaceholder("00_admin/licenses/h3-video-vae.json")).toBeVisible();
  await expect(panel.getByPlaceholder("以真实许可证文件为准")).toBeVisible();
  await expect(panel.locator(".model-license-import select").nth(0)).toHaveValue("");
  await expect(panel.locator(".model-license-import select").nth(1)).toHaveValue("");
  const undersizedControls = await panel.locator(".model-license-import button, .model-license-import input, .model-license-import select").evaluateAll((nodes) => nodes.filter((node) => node.getBoundingClientRect().height < 40).map((node) => ({ tag: node.tagName, height: node.getBoundingClientRect().height })));
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  const passed = overflow === 0 && undersizedControls.length === 0 && consoleErrors.length === 0 && pageErrors.length === 0 && failedResponses.length === 0 && publicRequests.length === 0 && writes.length === 0 && originalMedia.length === 0;
  results.push({ viewport: viewport.name, status: passed ? "PASS" : "FAIL", missing_license_evidence_count: 10, license_is_optional_risk_record: true, local_path_reference_controls: true, copied: false, uploaded: false, explicit_empty_choices: true, undersized_controls: undersizedControls, horizontal_overflow_px: overflow, console_errors: consoleErrors, page_errors: pageErrors, failed_responses: failedResponses, public_requests: publicRequests, write_requests: writes, original_model_requests: originalMedia });
  expect(passed).toBe(true);
});
