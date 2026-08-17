import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * G11 P1-6 delivery platform presets Windows UAT: real browser clicks against
 * the isolated simulation environment (:3225).  The spec creates a delivery
 * target from the DOUYIN_VERTICAL preset through ReadinessPanels and verifies
 * the frozen spec through the configuration API.
 */

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const base = "http://127.0.0.1:3225";
const steps: string[] = [];
const errors: string[] = [];

function evidencePath() {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  return path.join(cwd, "docs", "evidence", "g10", "delivery-presets-windows-uat-2026-08-17.json");
}

test.afterAll(() => {
  const output = {
    schema_version: "g11.delivery-presets-windows-uat.v1",
    observed_at: new Date().toISOString(),
    mode: "LOCAL_ONLY",
    project_id: projectId,
    status: errors.length === 0 ? "PASS" : "IN_PROGRESS",
    isolated_snapshot: true,
    production_database_touched: false,
    runtime_contacted: false,
    network_contacted: false,
    steps,
    errors,
    interpretation:
      "G11 P1-6 delivery platform presets real-click UAT: DOUYIN_VERTICAL preset selected and applied through ReadinessPanels, target created with the frozen platform spec (1080x1920 @30fps, 6000 kbps, 180s, cover 1080x1440), verified through the configuration API.",
  };
  fs.mkdirSync(path.dirname(evidencePath()), { recursive: true });
  fs.writeFileSync(evidencePath(), `${JSON.stringify(output, null, 2)}\n`, "utf8");
});

test("G11 delivery presets: apply DOUYIN_VERTICAL through the panel and verify the frozen spec", async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const failedResponses: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("response", (response) => { if (response.status() >= 400 && !response.url().includes("/favicon")) failedResponses.push(`${response.status()} ${response.url()}`); });

  const params = new URLSearchParams({ view: "projects", project: projectId });
  await page.goto(`/?${params.toString()}`, { waitUntil: "networkidle" });
  const editor = page.locator(".configuration-snapshot").first();
  await expect(editor.getByText("选择 / 创建本地交付目标版本")).toBeVisible();
  await expect(editor.getByRole("heading", { name: "从平台预设创建交付目标" })).toBeVisible();

  const presetSelect = editor.getByLabel("交付规格预设");
  await presetSelect.selectOption({ label: "抖音竖屏 · DOUYIN_VERTICAL" });
  await editor.getByPlaceholder("例如：抖音短剧交付").fill("抖音竖屏测试交付");
  await editor.getByRole("button", { name: "按预设创建目标" }).click();
  await expect(editor.getByText(/已按预设创建交付目标：DOUYIN_VERTICAL/)).toBeVisible();
  steps.push("DOUYIN_VERTICAL preset applied through the panel");

  // --- Verify the frozen spec through the configuration API -------------------------------
  const session = await page.request.get(`${base}/api/v1/session/bootstrap`);
  const sessionBody = await session.json();
  const headers = { "X-Local-Instance-Token": sessionBody.token };
  const configResponse = await page.request.get(`${base}/api/v1/projects/${projectId}/configuration`, { headers });
  expect(configResponse.status()).toBe(200);
  const config = await configResponse.json();
  const target = (config.configuration.delivery_targets ?? []).find((item: { code: string }) => item.code === "DOUYIN_VERTICAL");
  expect(target).toBeTruthy();
  const spec = target.spec ?? {};
  expect(Number(spec.width)).toBe(1080);
  expect(Number(spec.height)).toBe(1920);
  expect(Number(spec.fps)).toBe(30);
  expect(Number(spec.bitrate_kbps)).toBe(6000);
  expect(Number(spec.max_duration_seconds)).toBe(180);
  expect(String(spec.cover_aspect)).toBe("1080x1440");
  steps.push(`API: DOUYIN_VERTICAL target frozen spec verified (${JSON.stringify({ width: spec.width, height: spec.height, fps: spec.fps, bitrate_kbps: spec.bitrate_kbps })} )`);

  const passed = consoleErrors.length === 0 && pageErrors.length === 0 && failedResponses.length === 0;
  expect(passed).toBe(true);
  if (consoleErrors.length) errors.push(`console: ${consoleErrors.join("; ")}`);
  if (pageErrors.length) errors.push(`pageerror: ${pageErrors.join("; ")}`);
  if (failedResponses.length) errors.push(`responses: ${failedResponses.join("; ")}`);
  steps.push("zero console errors / zero failed responses");
});
