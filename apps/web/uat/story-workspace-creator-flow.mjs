import { chromium } from "@playwright/test";
import { writeFile } from "node:fs/promises";

const baseUrl = process.env.LOCAL_DRAMA_WEB_URL || "http://127.0.0.1:5173";
const projectId = process.env.LOCAL_DRAMA_UAT_PROJECT_ID;
if (!projectId) throw new Error("LOCAL_DRAMA_UAT_PROJECT_ID is required for read-only story workspace UAT");

const viewports = [{ width: 1440, height: 900 }, { width: 1024, height: 768 }, { width: 768, height: 900 }];
const browser = await chromium.launch({ headless: true, channel: "msedge" });
const results = [];

try {
  for (const viewport of viewports) {
    const page = await browser.newPage({ viewport });
    const consoleProblems = [], pageErrors = [], failedResponses = [], publicRequests = [];
    page.on("console", (message) => { if (["error", "warning"].includes(message.type())) consoleProblems.push(`${message.type()}: ${message.text()}`); });
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("response", (response) => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`); });
    page.on("request", (request) => { const url = new URL(request.url()); if (!["127.0.0.1", "localhost"].includes(url.hostname)) publicRequests.push(request.url()); });

    await page.goto(`${baseUrl}/projects/${projectId}/story`, { waitUntil: "networkidle" });
    const defaultStageIsImport = await page.getByRole("heading", { name: "导入小说、剧本或长文" }).isVisible();
    if (viewport.width <= 960) await page.getByRole("button", { name: "选择阶段" }).click();
    const workflowNavigationCount = await page.getByRole("navigation", { name: "故事工作流" }).count();
    const passiveInspectorCount = await page.locator('[aria-label="检查器"], .three-pane-inspector').count();
    const geometry = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }));
    const importActionVisible = await page.getByText("选择本地文档", { exact: true }).isVisible();
    const creatorCopyVisible = await page.getByText("系统不会修改原文档。请先预览并选择正文范围，确认后再建立可追溯的项目副本。").isVisible();
    const technicalRequirementVisible = await page.getByText(/FR-ING-001|确认 commit|WorkerSession/).count() > 0;
    results.push({ viewport, defaultStageIsImport, workflowNavigationCount, passiveInspectorCount, importActionVisible, creatorCopyVisible, technicalRequirementVisible, geometry, consoleProblems, pageErrors, failedResponses, publicRequests });
    await page.close();
  }
} finally {
  await browser.close();
}

const passed = results.every((item) => item.defaultStageIsImport && item.workflowNavigationCount === 1 && item.passiveInspectorCount === 0 && item.importActionVisible && item.creatorCopyVisible && !item.technicalRequirementVisible && !item.geometry.horizontalOverflow && [item.consoleProblems, item.pageErrors, item.failedResponses, item.publicRequests].every((values) => values.length === 0));
const evidence = {
  schema_version: "localdrama.story-workspace-creator-flow-uat.v1",
  observed_at: new Date().toISOString(),
  status: passed ? "PASS" : "FAIL",
  mode: "READ_ONLY",
  database_mutated: false,
  expected_flow: ["导入原稿", "审核拆解", "角色建档", "故事圣经（随时维护）"],
  results,
};
await writeFile(new URL("../../../docs/evidence/g10/story-workspace-creator-flow-uat-2026-08-24.json", import.meta.url), `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
if (!passed) throw new Error(JSON.stringify(evidence));
process.stdout.write(`${JSON.stringify(evidence, null, 2)}\n`);
