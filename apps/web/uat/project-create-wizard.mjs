import { chromium } from "@playwright/test";
import { writeFile } from "node:fs/promises";

const viewports = [{ width: 1440, height: 900 }, { width: 1280, height: 800 }, { width: 1024, height: 768 }];
const baseUrl = process.env.LOCAL_DRAMA_WEB_URL || "http://127.0.0.1:5173";
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
    await page.goto(`${baseUrl}/projects`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "新建项目" }).click();
    await page.getByLabel(/作品标题/).fill(`创作者入口验收 ${viewport.width}`);
    const technicalIdCollapsed = !await page.getByText("高级：项目技术标识").locator("..").evaluate((node) => node.hasAttribute("open"));
    await page.getByRole("button", { name: "继续选择创作方式" }).click();
    const recommendedFormatSelected = await page.getByLabel(/竖屏短剧/).isChecked();
    const writingFirstSelected = await page.getByLabel(/先开始创作/).isChecked();
    await page.getByRole("button", { name: "继续并自动检查" }).click();
    await page.getByText("创作就绪", { exact: true }).waitFor();
    const geometry = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth, horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth }));
    const minControlHeight = await page.locator(".project-create-wizard input:visible, .project-create-wizard select:visible, .project-create-wizard button:visible").evaluateAll((items) => Math.min(...items.map((item) => item.getBoundingClientRect().height)));
    results.push({ viewport, technicalIdCollapsed, recommendedFormatSelected, writingFirstSelected, automaticPreflight: true, finalCreateClicked: false, geometry, minControlHeight, consoleProblems, pageErrors, failedResponses, publicRequests });
    await page.close();
  }
} finally { await browser.close(); }
const passed = results.every((item) => item.technicalIdCollapsed && item.recommendedFormatSelected && item.writingFirstSelected && !item.finalCreateClicked && !item.geometry.horizontalOverflow && item.minControlHeight >= 40 && [item.consoleProblems, item.pageErrors, item.failedResponses, item.publicRequests].every((values) => values.length === 0));
const evidence = {
  schema_version: "localdrama.project-create-wizard-uat.v2",
  observed_at: new Date().toISOString(),
  status: passed ? "PASS" : "FAIL",
  mode: "AUTOMATIC_PREFLIGHT_NO_CREATE",
  database_mutated: false,
  screenshots_captured: false,
  expected_flow: ["作品信息", "创作方式", "确认创建"],
  automatic_fields: ["project_code", "resolution", "fps", "subtitle_language", "production_plan_code", "delivery_target_code", "delivery_path"],
  results,
};
await writeFile(new URL("../../../docs/evidence/g10/project-create-wizard-uat-2026-08-24.json", import.meta.url), `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
if (!passed) throw new Error(JSON.stringify(evidence));
process.stdout.write(`${JSON.stringify(evidence, null, 2)}\n`);
