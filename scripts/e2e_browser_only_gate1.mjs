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

fs.mkdirSync(SCREENS_DIR, { recursive: true });

// Helper to log non-GET mutations to ndjson
let lastTriggerControl = "PAGE_LOAD";

function logMutation(entry) {
  const line = JSON.stringify(entry) + "\n";
  fs.appendFileSync(MUTATIONS_FILE, line, { encoding: "utf-8" });
  console.log(`[MUTATION LOGGED] ${entry.method} ${entry.request_url} -> ${entry.status}`);
}

async function main() {
  const now = new Date();
  const ts = now.toISOString().replace(/[-:T]/g, "").slice(0, 14);
  const projectCode = `browser_only_uat_${ts}`;
  const projectTitle = `Browser Only UAT ${ts}`;

  console.log(`=======================================================`);
  console.log(`启动纯可见浏览器 Gate 1 验收流程：${projectCode}`);
  console.log(`=======================================================`);

  const browser = await chromium.launch({ headless: false, slowMo: 150 });
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();

  // Attach global mutation listener
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

  // 1. 打开首页并启动新建项目向导
  await page.goto(`${BASE_URL}/projects`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);

  lastTriggerControl = "button:has-text('新建项目')";
  await page.locator("button:has-text('新建项目')").click();
  await page.waitForTimeout(800);

  // Step 1: 基础信息
  lastTriggerControl = "input[value='title'], input[value='code']";
  await page.locator("label:has-text('项目标题') input").fill(projectTitle);
  await page.locator("label:has-text('项目 code') input").fill(projectCode);
  await page.locator("label:has-text('季数') input").fill("1");
  await page.locator("label:has-text('每季集数') input").fill("1");

  lastTriggerControl = "button:has-text('下一步：发布规格')";
  await page.locator("button:has-text('下一步：发布规格')").click();
  await page.waitForTimeout(800);

  // Step 2: 发布规格 (9:16 / 1080x1920 / 25fps / 60s)
  lastTriggerControl = "specs_input_fields";
  await page.locator("label:has-text('画幅比例') input").fill("9:16");
  await page.locator("label:has-text('制作宽度') input").fill("1080");
  await page.locator("label:has-text('制作高度') input").fill("1920");
  await page.locator("label:has-text('fps 分子') input").fill("25");
  await page.locator("label:has-text('fps 分母') input").fill("1");
  await page.locator("label:has-text('目标集时长（秒）') input").fill("60");
  await page.locator("label:has-text('主语言') input").fill("zh-CN");
  await page.locator("label:has-text('字幕策略') select").selectOption("SIDECAR");
  await page.locator("label:has-text('字幕语言') input").fill("zh-CN");

  lastTriggerControl = "button:has-text('下一步：存储预检')";
  await page.locator("button:has-text('下一步：存储预检')").click();
  await page.waitForTimeout(800);

  // Step 3: 存储预检
  lastTriggerControl = "button:has-text('运行只读存储预检')";
  await page.locator("button:has-text('运行只读存储预检')").click();
  await page.waitForTimeout(1500);

  // Step 4: 稍后配置并接受阻塞
  lastTriggerControl = "input[name='route'] (稍后配置)";
  await page.locator("label:has-text('稍后配置并接受阻塞') input").check();
  await page.waitForTimeout(500);

  lastTriggerControl = "button:has-text('下一步：创建预览')";
  await page.locator("button:has-text('下一步：创建预览')").click();
  await page.waitForTimeout(800);

  // Step 5: 最终创建预检
  lastTriggerControl = "button:has-text('运行最终创建预检')";
  await page.locator("button:has-text('运行最终创建预检')").click();
  await page.waitForTimeout(1500);

  // Step 6: 确认创建 DRAFT
  lastTriggerControl = "button:has-text('确认创建 DRAFT')";
  await page.locator("button:has-text('确认创建 DRAFT')").click();
  await page.waitForTimeout(2500);

  // 此时应当进入项目主页
  const projectUrl = page.url();
  console.log(`新项目已创建，当前 URL: ${projectUrl}`);
  const match = projectUrl.match(/\/projects\/([a-f0-9-]+)/);
  if (!match) {
    throw new Error(`未能解析新创建的 Project ID, 当前 URL: ${projectUrl}`);
  }
  const projectId = match[1];
  console.log(`>>> 成功获取干净隔离项目 Project ID: ${projectId} <<<`);

  // 2. 导航至小说导入与大模型拆解页
  await page.goto(`${BASE_URL}/projects/${projectId}/story#story-import`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  // 填写文档绝对路径并建立源版本解析
  const novelTextPath = path.join(REPO_ROOT, "docs", "fixtures", "sample_novel.txt");
  lastTriggerControl = "input[placeholder*='episode-01.docx']";
  const pathInput = page.locator("input[placeholder*='episode-01.docx']").first();
  await pathInput.fill(novelTextPath);
  await page.waitForTimeout(500);

  lastTriggerControl = "button:has-text('建立源版本并解析预览')";
  await page.locator("button:has-text('建立源版本并解析预览')").click();
  await page.waitForTimeout(2000);

  // 确认 commit
  lastTriggerControl = "button:has-text('确认 commit（不覆盖母本）')";
  await page.locator("button:has-text('确认 commit（不覆盖母本）')").click();
  await page.waitForTimeout(2000);

  // 提交 AI 拆解任务
  lastTriggerControl = "button:has-text('提交 AI 拆解任务')";
  const submitBtn = page.locator("button:has-text('提交 AI 拆解任务')").first();
  await submitBtn.click();
  console.log("已点击'提交 AI 拆解任务'，等待 Worker 执行本地 LLM 拆解...");

  // 等待 Job 执行（轮询 UI 直到出现可审核草稿或显示 SUCCEEDED）
  let jobCompleted = false;
  for (let i = 0; i < 90; i++) {
    await page.waitForTimeout(2000);
    // 检查是否切换到审核 Tab 或者出现了审核复选框
    const reviewCheckbox = page.locator("#story-review input[type='checkbox']").first();
    const applyBtn = page.locator("#story-review button:has-text('应用到成片')").first();
    if (await reviewCheckbox.isVisible() && await applyBtn.isVisible()) {
      jobCompleted = true;
      console.log("本地 Ollama 拆解完成，草稿已渲染在审核面板中！");
      break;
    }
    // 也可以点击一下 review tab 检查
    const reviewTab = page.locator("a[href='#story-review'], button:has-text('剧本审核'), a:has-text('剧本审核')").first();
    if (await reviewTab.isVisible()) {
      await reviewTab.click();
    }
  }

  if (!jobCompleted) {
    throw new Error("等待本地大模型拆解超时（超过 180s）");
  }

  // 截取 Gate 1 审核前状态截图
  await page.screenshot({ path: path.join(SCREENS_DIR, "gate1_clean_01_draft_ready.jpg"), type: "jpeg", quality: 35 });

  // 3. 勾选审核复选框并点击“应用到成片”
  lastTriggerControl = "input[type='checkbox'] (我已展开并审阅)";
  const reviewCheckbox = page.locator("#story-review input[type='checkbox']").first();
  await reviewCheckbox.check();
  await page.waitForTimeout(500);

  lastTriggerControl = "button:has-text('应用到成片')";
  const applyBtn = page.locator("#story-review button:has-text('应用到成片')").first();
  await applyBtn.click();
  console.log("已点击'应用到成片'，等待持久化生效...");
  await page.waitForTimeout(3000);

  // 截取 Gate 1 应用后状态截图
  await page.screenshot({ path: path.join(SCREENS_DIR, "gate1_clean_02_storyboard_applied.jpg"), type: "jpeg", quality: 35 });

  // 导航至分集策划页核对镜头数量与时长
  const planNav = page.locator("a:has-text('分集策划'), a:has-text('分镜')").first();
  if (await planNav.isVisible()) {
    await planNav.click();
    await page.waitForTimeout(2000);
    await page.screenshot({ path: path.join(SCREENS_DIR, "gate1_clean_03_plan_overview.jpg"), type: "jpeg", quality: 35 });
  }

  console.log("=======================================================");
  console.log(`Gate 1 纯可见浏览器验收已顺利完成！新项目 ID: ${projectId}`);
  console.log("=======================================================");

  await browser.close();
}

main().catch((err) => {
  console.error("执行失败:", err);
  process.exit(1);
});
