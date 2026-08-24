import { chromium } from "playwright";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..");
const EVIDENCE_DIR = path.join(REPO_ROOT, "docs", "evidence", "ui-uat-2026-08-22");
const SCREENS_DIR = path.join(EVIDENCE_DIR, "screens");
const MUTATIONS_FILE = path.join(EVIDENCE_DIR, "browser_mutations.ndjson");
const BASE_URL = "http://127.0.0.1:5173";

const PROJECT_ID = "97c309b0-bf45-4dca-bd0c-006b76080be8";

let lastTriggerControl = "PAGE_LOAD";

function logMutation(entry) {
  const line = JSON.stringify(entry) + "\n";
  fs.appendFileSync(MUTATIONS_FILE, line, { encoding: "utf-8" });
  console.log(`[MUTATION LOGGED] ${entry.method} ${entry.request_url} -> ${entry.status}`);
}

async function main() {
  console.log(`=======================================================`);
  console.log(`纯可见浏览器 Gate 1 应用阶段：项目 ${PROJECT_ID}`);
  console.log(`=======================================================`);

  const browser = await chromium.launch({ headless: false, slowMo: 150 });
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();

  page.on("response", async (response) => {
    const request = response.request();
    const method = request.method().toUpperCase();
    if (method !== "GET" && method !== "OPTIONS") {
      let responseBody = null;
      try {
        responseBody = await response.json();
      } catch {
        try {
          responseBody = await response.text();
        } catch {}
      }
      logMutation({
        timestamp: new Date().toISOString(),
        page_url: page.url(),
        trigger_control_name: lastTriggerControl,
        method: method,
        request_url: request.url(),
        status: response.status(),
        response_body: responseBody,
      });
    }
  });

  // 1. 访问剧本页面并切换到 Review Tab
  await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/story#story-review`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);

  // 确保在审核 Tab
  const reviewTab = page.locator("a[href='#story-review'], a:has-text('剧本审核')").first();
  if (await reviewTab.isVisible()) {
    await reviewTab.click();
    await page.waitForTimeout(1000);
  }

  // 截取草稿就绪截图
  await page.screenshot({ path: path.join(SCREENS_DIR, "gate1_clean_01_draft_ready.jpg"), type: "jpeg", quality: 35 });

  // 2. 勾选“我已展开并审阅”复选框
  lastTriggerControl = "input[type='checkbox'] (我已展开并审阅)";
  const reviewCheckbox = page.locator("#story-review input[type='checkbox'], label:has-text('审阅') input[type='checkbox']").first();
  await reviewCheckbox.check();
  await page.waitForTimeout(800);

  // 3. 点击“应用到成片”
  lastTriggerControl = "button:has-text('应用到成片')";
  const applyBtn = page.locator("button:has-text('应用到成片')").first();
  await applyBtn.click();
  console.log("已点击'应用到成片'，等待服务端落库...");
  await page.waitForTimeout(3500);

  // 截取应用后截图
  await page.screenshot({ path: path.join(SCREENS_DIR, "gate1_clean_02_storyboard_applied.jpg"), type: "jpeg", quality: 35 });

  // 4. 导航至分集策划页核对
  await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/story`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  // 尝试打开分镜/策划链接
  const planLink = page.locator("a:has-text('分集策划'), a:has-text('分镜')").first();
  if (await planLink.isVisible()) {
    await planLink.click();
    await page.waitForTimeout(2000);
  }
  await page.screenshot({ path: path.join(SCREENS_DIR, "gate1_clean_03_plan_overview.jpg"), type: "jpeg", quality: 35 });

  console.log("=======================================================");
  console.log(`Gate 1 纯可见浏览器验收及应用全部完成！`);
  console.log("=======================================================");

  await browser.close();
}

main().catch((err) => {
  console.error("执行失败:", err);
  process.exit(1);
});
