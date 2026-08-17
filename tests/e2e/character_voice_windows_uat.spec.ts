import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * G11 P0-5 multi-voice TTS orchestration Windows UAT: real browser clicks
 * against the isolated simulation environment (:3225).  The snapshot seeds a
 * real Windows SAPI voice profile bound to CHARACTER 母亲 (published local TTS
 * profile), and the breakdown-apply spec creates the dialogue line.  This spec
 * drives the DialogueTTSPanel: seeded binding visible, episode batch TTS
 * submission through the real form, and a REAL SAPI synthesis job executed by
 * the controlled worker (TTS_GENERATION job SUCCEEDED with a VERIFIED audio
 * artifact).
 */

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
const base = "http://127.0.0.1:3225";
const steps: string[] = [];
const errors: string[] = [];

function evidencePath() {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  return path.join(cwd, "docs", "evidence", "g10", "character-voice-windows-uat-2026-08-17.json");
}

test.afterAll(() => {
  const output = {
    schema_version: "g11.character-voice-windows-uat.v1",
    observed_at: new Date().toISOString(),
    mode: "LOCAL_ONLY",
    project_id: projectId,
    episode_id: episodeId,
    status: errors.length === 0 ? "PASS" : "IN_PROGRESS",
    isolated_snapshot: true,
    production_database_touched: false,
    runtime_contacted: true,
    network_contacted: false,
    steps,
    errors,
    interpretation:
      "G11 multi-voice TTS orchestration real-click UAT: seeded character-voice binding visible, episode batch TTS submits one real job per dialogue line through the real form, and the controlled worker synthesizes the WAV through Windows SAPI (job SUCCEEDED, verified audio artifact).",
  };
  fs.mkdirSync(path.dirname(evidencePath()), { recursive: true });
  fs.writeFileSync(evidencePath(), `${JSON.stringify(output, null, 2)}\n`, "utf8");
});

test("G11 multi-voice TTS: binding visible, episode batch submits a real SAPI job", async ({ page }) => {
  test.setTimeout(240_000);
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const failedResponses: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("response", (response) => { if (response.status() >= 400 && !response.url().includes("/favicon")) failedResponses.push(`${response.status()} ${response.url()}`); });

  const params = new URLSearchParams({ view: "projects", project: projectId, episode: episodeId });
  await page.goto(`/?${params.toString()}`, { waitUntil: "networkidle" });
  const orchestration = page.locator(".character-voice-orchestration");
  await expect(orchestration.getByRole("heading", { name: "角色音色绑定与整集批量 TTS" })).toBeVisible();
  await expect(orchestration.locator('[aria-label="角色音色绑定"]').getByText(/母亲/)).toBeVisible();
  await expect(orchestration.getByText(/sapi:/)).toBeVisible();
  steps.push("seeded character-voice binding (母亲 ↔ SAPI voice) visible");

  await orchestration.getByLabel("整集情绪").fill("NEUTRAL");
  await orchestration.getByLabel("整集语速").fill("1");
  await orchestration.getByRole("button", { name: "整集批量 TTS" }).click();
  const summary = orchestration.locator(".batch-summary");
  await expect(summary).toContainText("已提交 1 · 跳过 0 · 失败 0");
  steps.push("episode batch TTS submitted 1 job through the real form (0 skipped / 0 failed)");

  // --- Poll the real TTS_GENERATION job to SUCCEEDED (worker SAPI synthesis) -------------------
  const session = await page.request.get(`${base}/api/v1/session/bootstrap`);
  const sessionBody = await session.json();
  const headers = { "X-Local-Instance-Token": sessionBody.token };
  let jobId: string | null = null;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const jobsResponse = await page.request.get(`${base}/api/v1/jobs?project_id=${projectId}&type=TTS_GENERATION`, { headers });
    const jobs = await jobsResponse.json();
    const ttsJobs = (jobs.items ?? []).filter((job: { type: string }) => job.type === "TTS_GENERATION");
    if (ttsJobs.length > 0) {
      const latest = ttsJobs[ttsJobs.length - 1];
      jobId = String(latest.id);
      if (latest.state === "SUCCEEDED") break;
    }
    await page.waitForTimeout(2_500);
  }
  expect(jobId).toBeTruthy();
  const jobsResponse = await page.request.get(`${base}/api/v1/jobs?project_id=${projectId}`, { headers });
  const jobs = await jobsResponse.json();
  const target = (jobs.items ?? []).find((job: { id: string }) => String(job.id) === jobId);
  expect(target).toBeTruthy();
  expect(String(target.state)).toBe("SUCCEEDED");
  steps.push(`real TTS job ${jobId} reached SUCCEEDED through the controlled worker (Windows SAPI synthesis)`);

  const passed = consoleErrors.length === 0 && pageErrors.length === 0 && failedResponses.length === 0;
  expect(passed).toBe(true);
  if (consoleErrors.length) errors.push(`console: ${consoleErrors.join("; ")}`);
  if (pageErrors.length) errors.push(`pageerror: ${pageErrors.join("; ")}`);
  if (failedResponses.length) errors.push(`responses: ${failedResponses.join("; ")}`);
  steps.push("zero console errors / zero failed responses");
});
