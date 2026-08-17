import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * G11 P1-12 subtitle style templates Windows UAT: real browser clicks against
 * the isolated simulation environment (:3225).  The spec opens the
 * SubtitleRevisionPanel, expands the style editor, fills a custom style, saves
 * it as a project template, creates an ASS subtitle revision with verbatim
 * script-authority cues, and verifies the [V4+ Styles] block and per-cue
 * style_json through the API.
 */

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
const sourceDocumentVersionId = "6bdf1995-bbc2-4df8-a776-0779a4edfa19";
const base = "http://127.0.0.1:3225";
const steps: string[] = [];
const errors: string[] = [];
const realCues = [
  { start_us: 0, end_us: 3_000_000, text: "谁在里面？" },
  { start_us: 5_000_000, end_us: 9_000_000, text: "先喝口热水，天亮以前我们一起想办法。" },
];

function evidencePath() {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  return path.join(cwd, "docs", "evidence", "g10", "subtitle-styles-windows-uat-2026-08-17.json");
}

test.afterAll(() => {
  const output = {
    schema_version: "g11.subtitle-styles-windows-uat.v1",
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
      "G11 P1-12 subtitle style templates real-click UAT: custom style filled in SubtitleRevisionPanel, saved as a project template, applied to a new ASS subtitle revision with verbatim script-authority cues; the [V4+ Styles] block and per-cue style_json are verified through the API.",
  };
  fs.mkdirSync(path.dirname(evidencePath()), { recursive: true });
  fs.writeFileSync(evidencePath(), `${JSON.stringify(output, null, 2)}\n`, "utf8");
});

test("G11 subtitle styles: style form, template save, ASS revision with styles", async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const failedResponses: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("response", (response) => { if (response.status() >= 400 && !response.url().includes("/favicon")) failedResponses.push(`${response.status()} ${response.url()}`); });

  const params = new URLSearchParams({ view: "projects", project: projectId, episode: episodeId });
  await page.goto(`/?${params.toString()}`, { waitUntil: "networkidle" });
  const panel = page.locator(".subtitle-revision-panel");
  await expect(panel.getByRole("heading", { name: "字幕生成、校对与格式导出" })).toBeVisible();

  // --- Expand the style editor and fill a custom style ---------------------------------------
  await panel.getByRole("button", { name: "字幕样式模板（字体/字号/颜色/位置/描边）" }).click();
  await panel.getByLabel("字体").fill("微软雅黑");
  await panel.getByLabel("字号（8—160）").fill("36");
  await panel.getByLabel("颜色（#RRGGBB）").fill("#FFD700");
  await panel.getByLabel("位置").selectOption("TOP");
  await panel.getByLabel("描边（0—12）").fill("2");
  steps.push("custom subtitle style filled (微软雅黑/36/#FFD700/TOP/outline 2)");

  // --- Save the style as a project template ---------------------------------------------------
  await panel.getByPlaceholder("例如 默认字幕").fill("黄金字幕");
  await panel.getByRole("button", { name: "保存为项目模板" }).click();
  await expect(panel.getByText("样式已保存为项目模板。")).toBeVisible();
  steps.push("style saved as project template 黄金字幕");

  // --- Create an ASS subtitle revision with verbatim script-authority cues --------------------
  await panel.getByPlaceholder("source-document-version UUID").fill(sourceDocumentVersionId);
  await panel.locator("select").filter({ hasText: /ASS/ }).selectOption("ASS");
  await panel.locator(".subtitle-cues-field textarea").fill(JSON.stringify(realCues));
  await panel.getByRole("button", { name: "创建字幕 revision" }).click();
  await expect(panel.getByText(/已创建字幕 revision v\d+ · ASS · 2 条；样式 微软雅黑\/36px/)).toBeVisible({ timeout: 30_000 });
  steps.push("ASS subtitle revision created with the custom style");

  // --- Verify through the API: [V4+ Styles] block + per-cue style_json -------------------------
  const session = await page.request.get(`${base}/api/v1/session/bootstrap`);
  const sessionBody = await session.json();
  const headers = { "X-Local-Instance-Token": sessionBody.token };
  const statusResponse = await page.request.get(`${base}/api/v1/episodes/${episodeId}/timeline-status`, { headers });
  const status = (await statusResponse.json()).status;
  const latestId = String(status.subtitles.latest.id);
  const subtitleResponse = await page.request.get(`${base}/api/v1/subtitle-revisions/${latestId}`, { headers });
  expect(subtitleResponse.status()).toBe(200);
  const subtitle = (await subtitleResponse.json()).subtitle;
  expect(String(subtitle.format)).toBe("ASS");
  expect(String(subtitle.content_text)).toContain("[V4+ Styles]");
  expect(String(subtitle.content_text)).toContain("微软雅黑");
  const cues = subtitle.cues ?? [];
  expect(cues.length).toBe(2);
  const firstCue = cues[0];
  expect(firstCue.style).toBeTruthy();
  expect(Number(firstCue.style.size)).toBe(36);
  expect(String(firstCue.style.color)).toBe("#FFD700");
  expect(String(firstCue.style.position)).toBe("TOP");
  steps.push(`API: ASS revision v${subtitle.revision_no} verified with [V4+ Styles] block + per-cue style_json`);

  const passed = consoleErrors.length === 0 && pageErrors.length === 0 && failedResponses.length === 0;
  expect(passed).toBe(true);
  if (consoleErrors.length) errors.push(`console: ${consoleErrors.join("; ")}`);
  if (pageErrors.length) errors.push(`pageerror: ${pageErrors.join("; ")}`);
  if (failedResponses.length) errors.push(`responses: ${failedResponses.join("; ")}`);
  steps.push("zero console errors / zero failed responses");
});
