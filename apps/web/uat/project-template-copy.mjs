import { chromium } from "@playwright/test";
import { writeFile } from "node:fs/promises";

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const viewports = [{ width: 1440, height: 900 }, { width: 1280, height: 800 }, { width: 1024, height: 768 }];
const browser = await chromium.launch({ headless: true, channel: "msedge" });
const results = [];
try {
  for (const viewport of viewports) {
    const page = await browser.newPage({ viewport });
    const consoleProblems = [];
    const pageErrors = [];
    const failedResponses = [];
    const publicRequests = [];
    const originalMediaRequests = [];
    page.on("console", (message) => { if (["error", "warning"].includes(message.type())) consoleProblems.push(`${message.type()}: ${message.text()}`); });
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("response", (response) => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`); });
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (!["127.0.0.1", "localhost"].includes(url.hostname)) publicRequests.push(request.url());
      if (/\/media(-versions)?\//.test(url.pathname) && url.searchParams.get("size") !== "small") originalMediaRequests.push(request.url());
    });
    await page.goto(`http://127.0.0.1:5173/?view=projects&project=${projectId}`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "复制为新剧模板" }).click();
    await page.getByLabel("新项目 code").waitFor();
    const policyVisible = await page.getByText(/不复制媒体、角色授权资产、BrandKit、任务、审核或交付历史/).isVisible();
    const geometry = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    }));
    const fields = await page.locator(".template-copy input").evaluateAll((items) => items.map((item) => ({
      label: item.closest("label")?.textContent ?? "",
      height: item.getBoundingClientRect().height,
    })));
    results.push({ viewport, policyVisible, geometry, fields, consoleProblems, pageErrors, failedResponses, publicRequests, originalMediaRequests, confirmedCopy: false });
    await page.close();
  }
} finally {
  await browser.close();
}
const passed = results.every((result) => result.policyVisible && !result.geometry.horizontalOverflow && result.fields.every((field) => field.height >= 40) && result.consoleProblems.length === 0 && result.pageErrors.length === 0 && result.failedResponses.length === 0 && result.publicRequests.length === 0 && result.originalMediaRequests.length === 0 && result.confirmedCopy === false);
const evidence = { schema_version: "localdrama.project-template-copy-uat.v1", observed_at: new Date().toISOString(), status: passed ? "PASS" : "FAIL", mode: "PRODUCTION_READ_ONLY_FORM_DISCLOSURE", project_id: projectId, database_mutated: false, screenshots_captured: false, results };
await writeFile(new URL("../../../docs/evidence/g10/project-template-copy-uat-2026-08-15.json", import.meta.url), `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
if (!passed) throw new Error(JSON.stringify(evidence));
process.stdout.write(`${JSON.stringify(evidence, null, 2)}\n`);
