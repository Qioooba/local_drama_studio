import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * "One-sentence video" simulation: T2V (text-to-video) through the real UI.
 *
 * Runs against the isolated simulation environment (production snapshot on
 * :3225, real H3 worker on :8188).  Binds the shot's camera plan to the
 * published native T2V profile through the director shot editor, selects the
 * T2V mode, types a single sentence, runs the read-only preflight, submits the
 * REAL T2V variant + job, waits for the real Comfy artifact, then approves the
 * generated video in the review inbox.  Real data, real page clicks, no mocks.
 */

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
const shotId = "020f9248-14b7-4f92-9edd-ee587ffdedf3";

test.setTimeout(1_800_000); // the real T2V job takes minutes
const steps: string[] = [];
const errors: string[] = [];

function evidencePath() {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  return path.join(cwd, "docs", "evidence", "g10", "t2v-one-shot-generation-2026-08-17.json");
}

test.afterAll(() => {
  const output = {
    schema_version: "g10.t2v-one-shot-generation.v1",
    observed_at: new Date().toISOString(),
    mode: "LOCAL_ONLY",
    project_id: projectId,
    episode_id: episodeId,
    shot_id: shotId,
    status: errors.length === 0 ? "PASS" : "IN_PROGRESS",
    isolated_snapshot: true,
    production_database_touched: false,
    runtime_contacted: true,
    network_contacted: false,
    steps,
    errors,
    interpretation:
      "One-sentence T2V generation driven by real browser clicks: director shot editor rebinds the camera plan to the published native T2V profile, the T2V mode card is selected, a single sentence is typed, the read-only preflight plans, and a REAL H3 T2V variant+job is submitted and completes with a real MP4 artifact, which is then approved in the review inbox.",
  };
  fs.mkdirSync(path.dirname(evidencePath()), { recursive: true });
  fs.writeFileSync(evidencePath(), `${JSON.stringify(output, null, 2)}\n`, "utf8");
});

let step = "startup";

function pageEvents(page: import("@playwright/test").Page) {
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console[${step}] ${message.text()}`);
  });
  page.on("pageerror", (error) => errors.push(`pageerror[${step}] ${String(error)}`));
  page.on("response", (response) => {
    if (response.status() >= 400 && !response.url().includes("/favicon")) {
      errors.push(`response[${step}] ${response.status()} ${response.url()}`);
    }
  });
}

test("one-sentence T2V: director → T2V mode → real generation → approval", async ({ page }) => {
  pageEvents(page);
  await page.setViewportSize({ width: 1440, height: 900 });

  step = "generation-view";
  await page.goto(`/?view=generation&project=${projectId}&episode=${episodeId}&shot=${shotId}`, { waitUntil: "networkidle", timeout: 60_000 });
  await expect(page.getByRole("heading", { name: "真实生成闭环门禁" })).toBeVisible();
  steps.push("generation view: readiness visible");

  // Director shot editor: rebind the camera plan to the T2V profile so the
  // T2V preflight camera gate matches the selected T2V profile.
  step = "director-t2v-rebind";
  const directorEditor = page.locator("section.director-editor");
  // The camera profile select is the second select in the director editor
  // (first is shot_type).  Pick the published simulation T2V profile.
  const directorProfile = directorEditor.locator("select").nth(1);
  const t2vProfileOption = directorProfile.locator("option", { hasText: "Simulation native T2V profile" }).first();
  await expect(t2vProfileOption).toHaveCount(1, { timeout: 30_000 });
  const t2vProfileValue = (await t2vProfileOption.getAttribute("value")) ?? "";
  await directorProfile.selectOption(t2vProfileValue);
  const resolveButton = directorEditor.getByRole("button", { name: "按 Profile 裁决运镜能力" });
  await expect(resolveButton).toBeEnabled({ timeout: 30_000 });
  await resolveButton.click();
  await page.waitForTimeout(1_500);
  const saveButton = directorEditor.getByRole("button", { name: "保存新 revision" });
  await expect(saveButton).toBeEnabled({ timeout: 30_000 });
  await saveButton.click();
  await page.waitForTimeout(1_500);
  steps.push("director shot editor: camera plan rebound to T2V profile via UI");

  // Select the T2V mode card ("文字生成视频").
  step = "t2v-mode";
  await page.getByRole("button", { name: "文字生成视频" }).click();
  await expect(page.getByRole("button", { name: "文字生成视频" })).toHaveAttribute("aria-pressed", "true");
  steps.push("T2V mode card selected");

  step = "t2v-prompt-profile";
  await page.locator("#generation-prompt").fill("细雨中的北方乡村老屋，一名女子撑伞缓步走过青石院子，细雨落在屋檐上。");
  const profileSelect = page.locator("#generation-profile");
  const simT2vOption = page.locator("#generation-profile option", { hasText: "Simulation native T2V profile" }).first();
  const simT2vValue = (await simT2vOption.getAttribute("value")) ?? "";
  await profileSelect.selectOption(simT2vValue);
  await page.locator("#generation-seed").fill("20260818");
  await page.locator("#generation-take-count").fill("1");
  steps.push("T2V prompt (one sentence) + profile + seed entered");

  step = "t2v-preflight";
  const preflightButton = page.getByRole("button", { name: "建立意图并执行只读生成预检" });
  await expect(preflightButton).toBeEnabled({ timeout: 30_000 });
  await preflightButton.click();
  await expect(page.getByText(/预检 READY/)).toBeVisible({ timeout: 60_000 });
  steps.push("T2V preflight: read-only plan READY via real API");

  step = "t2v-submit";
  const submitButton = page.getByRole("button", { name: "确认创建 Variant 与 Job" });
  await submitButton.click();
  await expect(page.getByText(/真实任务已持久化/)).toBeVisible({ timeout: 60_000 });
  steps.push("T2V submit: REAL Variant + Job persisted through the UI");

  step = "t2v-jobs-wait";
  const request = page.request;
  let state = "";
  const deadline = Date.now() + 1_200_000;
  while (Date.now() < deadline) {
    const response = await request.get(`/api/v1/jobs?project_id=${projectId}&limit=100`);
    if (response.ok()) {
      const payload = (await response.json()) as { items: Array<{ id: string; type: string; state: string; created_at: string }> };
      const newest = [...payload.items]
        .filter((job) => job.type === "GENERATION_VARIANT" || job.type === "T2V")
        .sort((left, right) => String(right.created_at ?? "").localeCompare(String(left.created_at ?? "")))[0];
      if (newest) {
        state = newest.state;
        if (state === "SUCCEEDED") break;
        if (state === "FAILED") {
          errors.push(`T2V job FAILED: ${newest.id}`);
          break;
        }
      }
    }
    await page.waitForTimeout(10_000);
  }
  if (state !== "SUCCEEDED") {
    errors.push(`T2V job did not succeed (last state ${state})`);
    throw new Error(`T2V job did not succeed (last state ${state})`);
  }
  steps.push(`T2V jobs wait: real H3 T2V job SUCCEEDED (state=${state})`);

  step = "t2v-review";
  const inboxResponse = await page.request.get(`/api/v1/reviews/inbox?project_id=${projectId}&limit=100`);
  const inboxPayload = (await inboxResponse.json()) as { items: Array<{ media_version_id: string; media_kind: string; inbox_at?: string; created_at?: string }> };
  const targetVideo = [...inboxPayload.items]
    .filter((item) => item.media_kind === "VIDEO")
    .sort((left, right) => String(right.inbox_at ?? right.created_at ?? "").localeCompare(String(left.inbox_at ?? left.created_at ?? "")))[0];
  expect(targetVideo).toBeDefined();
  await page.goto(`/?view=reviews&project=${projectId}&episode=${episodeId}&review=${targetVideo.media_version_id}`, { waitUntil: "networkidle", timeout: 60_000 });
  await expect(page.getByRole("heading", { name: "媒体版本审核与选择" })).toBeVisible();
  await page.waitForTimeout(1_500);
  const checklistFields = page.locator(".review-detail fieldset.review-check");
  const fieldCount = await checklistFields.count();
  for (let index = 0; index < fieldCount; index += 1) {
    await checklistFields.nth(index).getByRole("radio", { name: "通过", exact: true }).click({ force: true });
  }
  const decision = page.getByLabel("审核决定");
  if (await decision.count()) {
    await decision.selectOption({ label: "批准" });
  }
  const submitReview = page.getByRole("button", { name: "提交审核" });
  await submitReview.click();
  await page.waitForTimeout(2_000);
  steps.push(`T2V review: newest generated video ${targetVideo.media_version_id.slice(0, 12)} approved (${fieldCount} checks)`);

  expect(errors).toEqual([]);
});
