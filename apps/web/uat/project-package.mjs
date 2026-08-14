import { chromium } from "@playwright/test";
import { writeFile } from "node:fs/promises";

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const viewports = [{ width: 1440, height: 900 }, { width: 1280, height: 800 }, { width: 1024, height: 768 }];
const browser = await chromium.launch({ headless: true, channel: "msedge" });
const results = [];
try {
  for (const viewport of viewports) {
    const page = await browser.newPage({ viewport });
    const consoleProblems = [], pageErrors = [], failedResponses = [], publicRequests = [], originalMediaRequests = [], packageMutations = [];
    page.on("console", (message) => { if (["error", "warning"].includes(message.type())) consoleProblems.push(`${message.type()}: ${message.text()}`); });
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("response", (response) => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`); });
    page.on("request", (request) => { const url = new URL(request.url()); if (!["127.0.0.1", "localhost"].includes(url.hostname)) publicRequests.push(request.url()); if (/packages:(export|dry-run)/.test(url.pathname)) packageMutations.push(request.url()); if (/\/media(-versions)?\//.test(url.pathname) && url.searchParams.get("size") !== "small") originalMediaRequests.push(request.url()); });
    await page.goto(`http://127.0.0.1:5173/?view=projects&project=${projectId}`, { waitUntil: "networkidle" });
    const section = page.locator(".project-package-action");
    const visible = await section.isVisible();
    const policyVisible = await page.getByText(/dry-run 只校验 schema、hash、磁盘和 identity，不导入或修改数据库/).isVisible();
    const buttonHeight = await section.getByRole("button", { name: "导出并 dry-run" }).evaluate((item) => item.getBoundingClientRect().height);
    const geometry = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth, horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth }));
    results.push({ viewport, visible, policyVisible, buttonHeight, geometry, packageMutations, consoleProblems, pageErrors, failedResponses, publicRequests, originalMediaRequests, exportClicked: false });
    await page.close();
  }
} finally { await browser.close(); }
const passed = results.every((item) => item.visible && item.policyVisible && item.buttonHeight >= 40 && !item.geometry.horizontalOverflow && !item.exportClicked && [item.packageMutations, item.consoleProblems, item.pageErrors, item.failedResponses, item.publicRequests, item.originalMediaRequests].every((values) => values.length === 0));
const evidence = { schema_version: "localdrama.project-package-uat.v1", observed_at: new Date().toISOString(), status: passed ? "PASS" : "FAIL", mode: "PRODUCTION_UI_READ_ONLY", production_database_mutated: false, production_files_mutated: false, screenshots_captured: false, results };
await writeFile(new URL("../../../docs/evidence/g10/project-package-uat-2026-08-15.json", import.meta.url), `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
if (!passed) throw new Error(JSON.stringify(evidence));
process.stdout.write(`${JSON.stringify(evidence, null, 2)}\n`);
