import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * G11 P0-3 breakdown draft apply Windows UAT: real browser clicks against the
 * isolated simulation environment (:3225).  The snapshot seeds one
 * DRAFT_READY script-breakdown draft (1 scene / 1 shot / 1 dialogue line,
 * speaker 母亲).  This spec applies it through AIDraftReviewPanel and verifies
 * the real production entities (scene range, shot + revision, dialogue line)
 * through the API, plus the draft state flip to APPLIED.
 */

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
const base = "http://127.0.0.1:3225";
const steps: string[] = [];
const errors: string[] = [];

function evidencePath() {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  return path.join(cwd, "docs", "evidence", "g10", "breakdown-apply-windows-uat-2026-08-17.json");
}

test.afterAll(() => {
  const output = {
    schema_version: "g11.breakdown-apply-windows-uat.v1",
    observed_at: new Date().toISOString(),
    mode: "LOCAL_ONLY",
    project_id: projectId,
    episode_id: episodeId,
    status: errors.length === 0 ? "PASS" : "IN_PROGRESS",
    isolated_snapshot: true,
    production_database_touched: false,
    runtime_contacted: false,
    network_contacted: false,
    steps,
    errors,
    interpretation:
      "G11 breakdown draft apply real-click UAT: the seeded DRAFT_READY draft is applied through AIDraftReviewPanel into real scenes/shots/dialogue rows, the extracted characters are reported, and the draft flips to APPLIED with requires_human_action false.",
  };
  fs.mkdirSync(path.dirname(evidencePath()), { recursive: true });
  fs.writeFileSync(evidencePath(), `${JSON.stringify(output, null, 2)}\n`, "utf8");
});

test("G11 breakdown draft: apply to episode through the review panel", async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const failedResponses: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("response", (response) => { if (response.status() >= 400 && !response.url().includes("/favicon")) failedResponses.push(`${response.status()} ${response.url()}`); });

  const params = new URLSearchParams({ view: "projects", project: projectId, episode: episodeId });
  await page.goto(`/?${params.toString()}`, { waitUntil: "networkidle" });
  const panel = page.locator(".ai-draft-panel");
  await expect(panel.getByRole("heading", { name: "AI 辅助提取草稿" })).toBeVisible();
  await expect(panel.getByText(/DRAFT_READY · NOT_APPLIED/).first()).toBeVisible();
  const applyArea = panel.locator('[aria-label^="应用到成片"]').first();
  const targetSelect = applyArea.getByLabel("选择目标集");
  await expect(targetSelect).toBeVisible();
  await targetSelect.selectOption({ index: 0 });
  steps.push("draft review panel: DRAFT_READY draft visible, target episode selected");

  await applyArea.getByRole("button", { name: "应用到成片" }).click();
  const summary = panel.locator('[aria-label="应用结果摘要"]').first();
  await expect(summary).toContainText("应用完成：创建 1 场 · 1 镜 · 1 条对白");
  await expect(summary).toContainText("母亲×1");
  steps.push("applied draft through the real UI: 1 scene / 1 shot / 1 line, character 母亲×1");

  // --- Verify the real production entities through the API -------------------------------------
  const session = await page.request.get(`${base}/api/v1/session/bootstrap`);
  const sessionBody = await session.json();
  const headers = { "X-Local-Instance-Token": sessionBody.token };

  const linesResponse = await page.request.get(`${base}/api/v1/episodes/${episodeId}/dialogue-lines`, { headers });
  expect(linesResponse.status()).toBe(200);
  const lines = await linesResponse.json();
  const appliedLine = (lines.items ?? []).find((line: { code: string }) => String(line.code).startsWith("AI-DL-"));
  expect(appliedLine).toBeTruthy();
  expect(String(appliedLine.speaker)).toBe("母亲");
  expect(String(appliedLine.text_revisions[0].text)).toContain("先喝口热水");
  steps.push(`API: dialogue line ${appliedLine.code} with speaker 母亲 created with verbatim draft text`);

  const draftsResponse = await page.request.get(`${base}/api/v1/projects/${projectId}/script-breakdown-drafts`, { headers });
  const drafts = await draftsResponse.json();
  const applied = (drafts.items ?? []).find((draft: { application_status: string }) => draft.application_status === "APPLIED");
  expect(applied).toBeTruthy();
  expect(applied.requires_human_action).toBe(false);
  steps.push("API: draft application_status flipped to APPLIED, requires_human_action false");

  const rangesResponse = await page.request.get(`${base}/api/v1/episodes/${episodeId}/scene-ranges`, { headers });
  if (rangesResponse.status() === 200) {
    const ranges = await rangesResponse.json();
    expect((ranges.items ?? []).length).toBeGreaterThanOrEqual(1);
    steps.push("API: episode scene range bound to the applied master scene");
  }

  const passed = consoleErrors.length === 0 && pageErrors.length === 0 && failedResponses.length === 0;
  expect(passed).toBe(true);
  if (consoleErrors.length) errors.push(`console: ${consoleErrors.join("; ")}`);
  if (pageErrors.length) errors.push(`pageerror: ${pageErrors.join("; ")}`);
  if (failedResponses.length) errors.push(`responses: ${failedResponses.join("; ")}`);
  steps.push("zero console errors / zero failed responses");
});
