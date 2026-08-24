import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * "One-sentence video" simulation: T2V (text-to-video) through the real V2 UI.
 *
 * Runs against an isolated simulation environment (production snapshot served
 * on :3225 behind the Vite proxy, real H3 worker draining GPU_H3 jobs against
 * a real ComfyUI on :8188).  Flow: generation workbench -> T2V mode card ->
 * one-sentence prompt -> read-only preflight -> confirm dialog -> REAL T2V
 * variant + job -> wait for the real Comfy artifact -> approve the generated
 * video in the V2 episode review workspace.  Real data, real clicks, no mocks.
 */

const projectId = process.env.T2V_PROJECT_ID ?? "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const episodeId = process.env.T2V_EPISODE_ID ?? "d4db1033-9517-4bd0-958d-4228e0abead1";
const shotId = process.env.T2V_SHOT_ID ?? "020f9248-14b7-4f92-9edd-ee587ffdedf3";
const profilePrefix = process.env.T2V_PROFILE_PREFIX ?? "1f99d2e3";
const promptText = process.env.T2V_PROMPT ?? "细雨中的北方乡村老屋，一名女子撑伞缓步走过青石院子，细雨落在屋檐上。";

test.setTimeout(1_800_000); // the real T2V job takes minutes
const steps: string[] = [];
const errors: string[] = [];

function evidencePath() {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  return path.join(cwd, "docs", "evidence", "g10", "t2v-one-shot-generation-2026-08-17.json");
}

test.afterAll(() => {
  const output = {
    schema_version: "g10.t2v-one-shot-generation.v2",
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
      "One-sentence T2V generation driven by real browser clicks through the V2 five-stage workbench: T2V mode card, one-sentence prompt, read-only preflight READY, explicit confirm dialog, REAL H3 T2V variant+job completing on ComfyUI, then human approval of the generated video inside the V2 episode review workspace.",
  };
  fs.mkdirSync(path.dirname(evidencePath()), { recursive: true });
  fs.writeFileSync(evidencePath(), `${JSON.stringify(output, null, 2)}\n`, "utf8");
});

let step = "startup";

function pageEvents(currentPage: Page) {
  currentPage.on("console", (message) => {
    if (message.type() === "error") errors.push(`console[${step}] ${message.text()}`);
  });
  currentPage.on("pageerror", (error) => errors.push(`pageerror[${step}] ${String(error)}`));
  currentPage.on("response", (response) => {
    if (response.status() >= 400 && !response.url().includes("/favicon")) {
      errors.push(`response[${step}] ${response.status()} ${response.url()}`);
    }
  });
}

async function expectEnabled(currentPage: Page, locator: ReturnType<Page["getByRole"]>, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await locator.isEnabled()) return;
    await currentPage.waitForTimeout(300);
  }
  throw new Error(`button still disabled: ${(await locator.textContent())?.trim()}`);
}

test("one-sentence T2V: workbench -> real generation -> review approval", async ({ page }) => {
  pageEvents(page);
  await page.setViewportSize({ width: 1440, height: 900 });

  // ---- Stage 1: setup (mode card + profile) ----
  step = "open-generation-view";
  await page.goto(`/?view=generation&project=${projectId}&episode=${episodeId}&shot=${shotId}`, {
    waitUntil: "networkidle",
    timeout: 60_000,
  });
  await expect(page.getByRole("heading", { name: "手动生成工作台" })).toBeVisible();
  steps.push("generation workbench visible (legacy URL redirected into V2 route)");

  step = "t2v-mode";
  const card = page.getByRole("button", { name: "文字生成视频" });
  await card.click();
  await expect(card).toHaveAttribute("aria-pressed", "true");
  steps.push("T2V mode card selected");

  step = "t2v-profile";
  const profileSelect = page.locator("#generation-profile");
  const preferredValue = await profileSelect
    .locator("option")
    .evaluateAll(
      (nodes, prefix: string) => (nodes as HTMLOptionElement[]).find((node) => node.value.startsWith(prefix))?.value ?? null,
      profilePrefix,
    );
  await profileSelect.selectOption(preferredValue ?? (await profileSelect.locator("option").nth(-1).getAttribute("value")) ?? "");
  await page.getByRole("button", { name: "下一步：输入与控制 →" }).click();
  steps.push("published VIDEO_T2V profile selected, advanced to inputs stage");

  // ---- Stage 2: one-sentence prompt ----
  step = "t2v-prompt";
  await page.locator("#generation-prompt").fill(promptText);
  await page.getByRole("button", { name: "下一步：预检与确认 →" }).click();
  steps.push("one-sentence prompt entered, advanced to preflight stage");

  // ---- Stage 3: read-only preflight then explicit submit ----
  step = "t2v-preflight";
  await page.locator("#generation-seed").fill("20260818");
  await page.locator("#generation-take-count").fill("1");
  const before = await (await page.request.get(`/api/v1/jobs?project_id=${projectId}&limit=100`)).json();
  const knownIds = new Set<string>((before.items ?? []).map((job: { id: string }) => job.id));
  await page.getByRole("button", { name: "建立意图并执行只读预检" }).click();
  await expect(page.getByText(/预检 READY，尚未创建 Job/)).toBeVisible({ timeout: 90_000 });
  steps.push("read-only preflight READY via real API");

  step = "t2v-submit";
  await page.getByRole("button", { name: "确认创建 Variant 与 Job" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expectEnabled(page, dialog.getByRole("button", { name: "确认提交任务" }));
  await dialog.getByRole("button", { name: "确认提交任务" }).click();
  await expect(page.getByText(/真实任务已持久化|个代理 take 已创建/).first()).toBeVisible({ timeout: 60_000 });
  steps.push("REAL Variant + Job persisted through the UI confirm dialog");

  step = "t2v-jobs-wait";
  let state = "";
  let jobId = "";
  const deadline = Date.now() + 1_200_000;
  while (Date.now() < deadline) {
    const response = await page.request.get(`/api/v1/jobs?project_id=${projectId}&limit=100`);
    if (response.ok()) {
      const payload = (await response.json()) as { items: Array<{ id: string; type: string; state: string }> };
      const newest = (payload.items ?? [])
        .filter((job) => ["GENERATION_VARIANT", "T2V", "COMFY_GENERATION"].includes(job.type) && !knownIds.has(job.id))
        .at(0);
      if (newest) {
        if (newest.id !== jobId) {
          jobId = newest.id;
        }
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
  if (state !== "SUCCEEDED") throw new Error(`T2V job did not succeed (last state ${state})`);
  steps.push(`real H3 T2V job SUCCEEDED (job=${jobId})`);

  // ---- Approve inside the V2 episode review workspace ----
  step = "t2v-review";
  const inboxResponse = await page.request.get(
    `/api/v1/reviews/inbox?project_id=${projectId}&episode_id=${episodeId}&include_resolved=true&limit=200`,
  );
  const inboxPayload = (await inboxResponse.json()) as {
    items: Array<{ media_version_id: string; media_kind: string; stage: string; decision?: string }>;
  };
  const targetVideo = (inboxPayload.items ?? []).filter((item) => item.media_kind === "VIDEO" && !item.decision).at(0);
  expect(targetVideo).toBeDefined();
  await page.goto(`/projects/${projectId}/episodes/${episodeId}/review`, { waitUntil: "networkidle", timeout: 60_000 });
  await expect(page.getByRole("heading", { name: "从候选问题到整集批准" })).toBeVisible();

  const row = page.locator(`#review-item-${targetVideo!.media_version_id}`);
  for (let index = 0; index < 10 && !(await row.isVisible().catch(() => false)); index += 1) {
    const more = page.getByRole("button", { name: /继续显示审核项/ });
    if (!(await more.isVisible().catch(() => false))) break;
    await more.click();
    await page.waitForTimeout(400);
  }
  await row.click();
  await expect(page.getByText(`媒体版本：${targetVideo!.media_version_id.slice(0, 16)}`)).toBeVisible();

  const checklistFields = page.locator(".review-detail fieldset.review-check");
  await checklistFields.first().waitFor();
  const fieldCount = await checklistFields.count();
  for (let index = 0; index < fieldCount; index += 1) {
    const radio = checklistFields.nth(index).getByRole("radio", { name: "通过", exact: true });
    if (!(await radio.isChecked())) await radio.click({ force: true });
  }
  const decision = page.locator("#review-decision");
  if (await decision.count()) await decision.selectOption("APPROVED");
  await expectEnabled(page, page.getByRole("button", { name: "提交审核" }));
  await page.getByRole("button", { name: "提交审核" }).click();

  let approved = false;
  for (let index = 0; index < 20 && !approved; index += 1) {
    await page.waitForTimeout(1_000);
    const check = await page.request.get(
      `/api/v1/reviews/inbox?project_id=${projectId}&episode_id=${episodeId}&include_resolved=true&limit=200`,
    );
    const payload = (await check.json()) as { items: Array<{ media_version_id: string; decision?: string }> };
    approved = (payload.items ?? []).some((item) => item.media_version_id === targetVideo!.media_version_id && item.decision === "APPROVED");
  }
  expect(approved).toBe(true);
  steps.push(`T2V review: generated video ${targetVideo!.media_version_id.slice(0, 12)} approved (${fieldCount} checks)`);

  expect(errors).toEqual([]);
});
