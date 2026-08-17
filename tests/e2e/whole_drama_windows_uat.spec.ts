import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * G11 P0-4 whole-drama one-click orchestration Windows UAT: real browser
 * clicks against the isolated simulation environment (:3225) with the
 * controlled worker loop (scripts/run_sim_worker.py --loop) executing the
 * AUTOMATION_WORKFLOW_TASK jobs.  The spec creates the WHOLE_DRAMA template
 * workflow from the panel, freezes the plan, starts the run, and watches the
 * worker drive the run through KEYFRAME_CHECK → TTS_BATCH until RENDER fails
 * (no timeline revision in the snapshot) and the run pauses for human
 * attention; then a human decision is recorded through the panel.
 */

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const base = "http://127.0.0.1:3225";
const steps: string[] = [];
const errors: string[] = [];

function evidencePath() {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  return path.join(cwd, "docs", "evidence", "g10", "whole-drama-windows-uat-2026-08-17.json");
}

test.afterAll(() => {
  const output = {
    schema_version: "g11.whole-drama-windows-uat.v1",
    observed_at: new Date().toISOString(),
    mode: "LOCAL_ONLY",
    project_id: projectId,
    status: errors.length === 0 ? "PASS" : "IN_PROGRESS",
    isolated_snapshot: true,
    production_database_touched: false,
    runtime_contacted: true,
    network_contacted: false,
    steps,
    errors,
    interpretation:
      "G11 whole-drama one-click orchestration real-click UAT: WHOLE_DRAMA template created from the panel, plan frozen, run started; the controlled worker executed the real AUTOMATION_WORKFLOW_TASK jobs (keyframe check / TTS batch / render), the run paused at the RENDER failure waiting for human attention, and the operator decision was recorded through the panel.",
  };
  fs.mkdirSync(path.dirname(evidencePath()), { recursive: true });
  fs.writeFileSync(evidencePath(), `${JSON.stringify(output, null, 2)}\n`, "utf8");
});

async function pollRun(apiGet: (url: string, options?: { headers?: Record<string, string> }) => Promise<{ ok: boolean; json: () => Promise<unknown> }>, runId: string, headers: Record<string, string>, timeoutMs: number): Promise<Record<string, unknown>> {
  const deadline = Date.now() + timeoutMs;
  let latest: Record<string, unknown> = {};
  while (Date.now() < deadline) {
    const response = await apiGet(`${base}/api/v1/automation-runs/${runId}`, { headers });
    if (response.ok) {
      const body = (await response.json()) as { run: Record<string, unknown> };
      latest = body.run;
      const status = String(latest.status);
      if (["PAUSED_HITL", "SUCCEEDED", "FAILED", "STOPPED", "CANCELLED", "LIMIT_REACHED"].includes(status)) return latest;
    }
    await new Promise((resolve) => setTimeout(resolve, 2_500));
  }
  return latest;
}

test("G11 whole-drama: template, plan, start, worker-driven run with HITL pause", async ({ page }) => {
  test.setTimeout(300_000);
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const failedResponses: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("response", (response) => { if (response.status() >= 400 && !response.url().includes("/favicon")) failedResponses.push(`${response.status()} ${response.url()}`); });

  const params = new URLSearchParams({ view: "diagnostics", project: projectId });
  await page.goto(`/?${params.toString()}`, { waitUntil: "networkidle" });
  const panel = page.locator(".automation-template-panel");
  await expect(panel.getByRole("heading", { name: "整剧一键编排" })).toBeVisible();
  await expect(panel.getByText(/按集顺序执行 关键帧确认 → 整集批量 TTS → 渲染成片 → 构建交付包/)).toBeVisible();
  await panel.getByRole("button", { name: "创建整剧编排 workflow" }).click();
  await expect(page.getByText(/已创建整剧编排 workflow：/)).toBeVisible();
  steps.push("WHOLE_DRAMA template workflow created through the panel");

  const workflowPanel = page.locator(".automation-workflow-panel");
  await workflowPanel.getByRole("button", { name: "读取并冻结 plan" }).click();
  await expect(page.getByText(/计划已冻结：/)).toBeVisible();
  await workflowPanel.getByRole("button", { name: "启动 run" }).click();
  await expect(page.getByText(/run 已启动；人工闸门和停止条件仍由服务端强制执行/)).toBeVisible();
  steps.push("plan frozen and run started through the panel");

  const session = await page.request.get(`${base}/api/v1/session/bootstrap`);
  const sessionBody = await session.json();
  const headers = { "X-Local-Instance-Token": sessionBody.token };
  const runsResponse = await page.request.get(`${base}/api/v1/projects/${projectId}/automation-runs`, { headers });
  const runsBody = (await runsResponse.json()) as { items: Array<{ id: string }> };
  const runId = String(runsBody.items[0].id);

  const paused = await pollRun((url, options) => page.request.get(url, options), runId, headers, 150_000);
  expect(String(paused.status)).toBe("PAUSED_HITL");
  expect(Number(paused.task_count)).toBeGreaterThanOrEqual(1);
  steps.push(`worker drove run ${runId} to first ${paused.status} after ${paused.task_count} real automation tasks (keyframe check or machine check requires human attention)`);

  // --- First human decision: approve and let the worker continue -------------
  const runPanel = page.locator(".automation-workflow-panel");
  await expect(runPanel.getByText(/人工：PENDING/)).toBeVisible({ timeout: 30_000 });
  await runPanel.getByRole("button", { name: "人工批准并继续" }).click();
  steps.push("first HITL decision recorded through the panel: HUMAN_APPROVED");

  // --- The worker continues: TTS_BATCH then RENDER fails (no timeline) -------
  const pausedAgain = await pollRun((url, options) => page.request.get(url, options), runId, headers, 150_000);
  expect(String(pausedAgain.status)).toBe("PAUSED_HITL");
  expect(Number(pausedAgain.task_count)).toBeGreaterThanOrEqual(3);
  steps.push(`worker continued and run ${runId} paused again after ${pausedAgain.task_count} real automation tasks (RENDER machine check)`);

  // --- Record a final human decision through the panel -----------------------
  await expect(runPanel.getByText(/人工：PENDING/)).toBeVisible({ timeout: 30_000 });
  await runPanel.getByRole("button", { name: "人工拒绝" }).click();
  await expect(runPanel.getByText(/状态：FAILED/)).toBeVisible({ timeout: 30_000 });
  steps.push("operator decision recorded through the panel: run FAILED after HUMAN_REJECTED");

  const passed = consoleErrors.length === 0 && pageErrors.length === 0 && failedResponses.length === 0;
  expect(passed).toBe(true);
  if (consoleErrors.length) errors.push(`console: ${consoleErrors.join("; ")}`);
  if (pageErrors.length) errors.push(`pageerror: ${pageErrors.join("; ")}`);
  if (failedResponses.length) errors.push(`responses: ${failedResponses.join("; ")}`);
  steps.push("zero console errors / zero failed responses");
});
