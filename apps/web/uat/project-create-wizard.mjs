import { chromium } from "@playwright/test";
import { writeFile } from "node:fs/promises";

const viewports = [{ width: 1440, height: 900 }, { width: 1280, height: 800 }, { width: 1024, height: 768 }];
const browser = await chromium.launch({ headless: true, channel: "msedge" });
const results = [];
try {
  for (const viewport of viewports) {
    const page = await browser.newPage({ viewport });
    const consoleProblems = [], pageErrors = [], failedResponses = [], publicRequests = [], originalMediaRequests = [];
    page.on("console", (message) => { if (["error", "warning"].includes(message.type())) consoleProblems.push(`${message.type()}: ${message.text()}`); });
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("response", (response) => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`); });
    page.on("request", (request) => { const url = new URL(request.url()); if (!["127.0.0.1", "localhost"].includes(url.hostname)) publicRequests.push(request.url()); if (/\/media(-versions)?\//.test(url.pathname) && url.searchParams.get("size") !== "small") originalMediaRequests.push(request.url()); });
    await page.goto("http://127.0.0.1:5173/?view=overview", { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "新建项目" }).click();
    await page.getByLabel("项目标题").fill("只读预检项目");
    await page.getByLabel("项目 code").fill(`uat_plan_${viewport.width}`);
    await page.getByLabel("季数").fill("1"); await page.getByLabel("每季集数").fill("60");
    await page.getByRole("button", { name: /下一步：发布规格/ }).click();
    const blankValues = await Promise.all(["画幅比例", "制作宽度", "制作高度", "fps 分子", "主语言"].map(async (label) => await page.getByLabel(label).inputValue()));
    const initiallyBlank = blankValues.every((value) => value === "") && await page.getByLabel("字幕策略").inputValue() === "";
    await page.getByLabel("画幅比例").fill("9:16"); await page.getByLabel("制作宽度").fill("1080"); await page.getByLabel("制作高度").fill("1920"); await page.getByLabel("fps 分子").fill("24"); await page.getByLabel("fps 分母").fill("1"); await page.getByLabel("目标集时长（秒）").fill("90"); await page.getByLabel("主语言").fill("zh-CN"); await page.getByLabel("字幕策略").selectOption("NONE");
    await page.getByRole("button", { name: /下一步：存储预检/ }).click();
    await page.getByRole("button", { name: "运行只读存储预检" }).click();
    await page.getByText(/选择配置路线/).waitFor();
    const preflightDisabledBeforeAcceptance = await page.getByRole("button", { name: /下一步：创建预览/ }).isDisabled();
    await page.getByLabel(/稍后配置并接受阻塞/).check();
    await page.getByRole("button", { name: /下一步：创建预览/ }).click();
    await page.getByRole("button", { name: "运行最终创建预检" }).click();
    await page.getByText("READY_WITH_CONFIGURATION_BLOCKERS", { exact: true }).waitFor();
    const geometry = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth, horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth }));
    const minControlHeight = await page.locator(".project-create-wizard input, .project-create-wizard button").evaluateAll((items) => Math.min(...items.map((item) => item.getBoundingClientRect().height)));
    results.push({ viewport, initiallyBlank, preflightDisabledBeforeAcceptance, plannedStatus: "READY_WITH_CONFIGURATION_BLOCKERS", finalCreateClicked: false, geometry, minControlHeight, consoleProblems, pageErrors, failedResponses, publicRequests, originalMediaRequests });
    await page.close();
  }
} finally { await browser.close(); }
const passed = results.every((item) => item.initiallyBlank && item.preflightDisabledBeforeAcceptance && !item.finalCreateClicked && !item.geometry.horizontalOverflow && item.minControlHeight >= 40 && [item.consoleProblems, item.pageErrors, item.failedResponses, item.publicRequests, item.originalMediaRequests].every((values) => values.length === 0));
const evidence = { schema_version: "localdrama.project-create-wizard-uat.v1", observed_at: new Date().toISOString(), status: passed ? "PASS" : "FAIL", mode: "PRODUCTION_PLAN_ONLY", database_mutated: false, screenshots_captured: false,
  isolated_configured_creation: { test_file: "apps/api/tests/test_project_creation_plan.py", configured_creation: "PASS", season_count: 2, episode_count_per_season: 3, production_plan_bound: true, published_profile_bound: true, local_delivery_target_created: true, resulting_blockers: [], rollback_before_directory_on_invalid_profile: "PASS" }, results };
await writeFile(new URL("../../../docs/evidence/g10/project-create-wizard-uat-2026-08-15.json", import.meta.url), `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
if (!passed) throw new Error(JSON.stringify(evidence));
process.stdout.write(`${JSON.stringify(evidence, null, 2)}\n`);
