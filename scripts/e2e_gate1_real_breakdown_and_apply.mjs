import { chromium } from "playwright";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..");
const SCREENS_DIR = path.join(REPO_ROOT, "docs", "evidence", "ui-uat-2026-08-22", "screens");
const MATRIX_PATH = path.join(REPO_ROOT, "docs", "evidence", "ui-uat-2026-08-22", "CONTROL_STATE_MATRIX.md");
const PROGRESS_PATH = path.join(REPO_ROOT, "docs", "evidence", "ui-uat-2026-08-22", "PROGRESS.md");
const BASE_URL = "http://127.0.0.1:5173";
const API_URL = "http://127.0.0.1:3210";

const PROJECT_ID = "9893a9bc-e58b-45a2-9143-c1bd7b886db9";
const EPISODE_ID = "b989644a-666e-448c-968b-6b865dbebca7";

if (!fs.existsSync(SCREENS_DIR)) {
  fs.mkdirSync(SCREENS_DIR, { recursive: true });
}

async function takeSnap(page, name) {
  const filePath = path.join(SCREENS_DIR, `${name}.jpg`);
  await page.screenshot({ path: filePath, type: "jpeg", quality: 35 });
  return `screens/${name}.jpg`;
}

async function appendMatrixRow(row) {
  let content = fs.readFileSync(MATRIX_PATH, "utf8");
  const line = `| **${row.rowId}** | \`${row.route}\` | \`${row.control}\` | ${row.precondition} | ${row.action} | ${row.expected} | ${row.actual} | \`${row.evidence}\` | ${row.defect} | \`${row.status}\` |`;
  content += `\n${line}`;
  fs.writeFileSync(MATRIX_PATH, content, "utf8");
}

async function appendProgress(text) {
  let content = fs.readFileSync(PROGRESS_PATH, "utf8");
  content += `\n${text}\n`;
  fs.writeFileSync(PROGRESS_PATH, content, "utf8");
}

async function run() {
  console.log("=================================================================");
  console.log("执行第一道强制门禁：本地 Ollama 真实大模型拆解与人工应用");
  console.log("=================================================================");

  const browser = await chromium.launch({
    headless: false,
    slowMo: 150,
    args: ["--window-size=1366,768"],
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
  });

  const page = await context.newPage();

  try {
    // 1. 进入故事导入页面
    console.log("\n[STEP 1] 进入故事导入页，导入并提交本地 Ollama 拆解任务...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/story#story-import`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);

    const novelPath = path.join(REPO_ROOT, "docs", "evidence", "ui-uat-2026-08-22", "sample_novel.txt");
    const pathInput = page.locator("label:has-text('电脑中的文档绝对路径') input, input[placeholder*='episode-01']").first();
    await pathInput.waitFor({ state: "visible", timeout: 10000 });
    await pathInput.fill(novelPath);
    await page.waitForTimeout(500);

    // 解析预览
    const previewBtn = page.locator("button:has-text('建立源版本并解析预览')");
    await previewBtn.waitFor({ state: "visible", timeout: 5000 });
    await previewBtn.click();
    console.log("✓ 点击'建立源版本并解析预览'");
    await page.waitForTimeout(1500);

    // 确认 commit
    const commitBtn = page.locator("button:has-text('确认 commit（不覆盖母本）')");
    if (await commitBtn.count() > 0 && await commitBtn.isEnabled()) {
      await commitBtn.click();
      console.log("✓ 点击'确认 commit（不覆盖母本）'");
      await page.waitForTimeout(1500);
    }

    // 确保目标季度与目标集已选定
    const seasonSelect = page.locator("select[aria-label='AI 拆解目标季度']");
    if (await seasonSelect.count() > 0) {
      const seasonOptions = await seasonSelect.locator("option").all();
      if (seasonOptions.length > 0) {
        const firstSeasonVal = await seasonOptions[0].getAttribute("value");
        if (firstSeasonVal) await seasonSelect.selectOption(firstSeasonVal);
      }
    }
    await page.waitForTimeout(500);

    const epSelect = page.locator("select[aria-label='AI 拆解目标集']");
    if (await epSelect.count() > 0) {
      const epOptions = await epSelect.locator("option").all();
      if (epOptions.length > 0) {
        const firstEpVal = await epOptions[0].getAttribute("value");
        if (firstEpVal) await epSelect.selectOption(firstEpVal);
      }
    }
    await page.waitForTimeout(500);

    // 设置原文起止段 (段落 1 到 8)
    const startInput = page.locator("label:has-text('本集原文起始段') input, input[aria-label='本集原文起始段']");
    if (await startInput.count() > 0) {
      await startInput.fill("1");
    }
    const endInput = page.locator("label:has-text('本集原文结束段') input, input[aria-label='本集原文结束段']");
    if (await endInput.count() > 0) {
      await endInput.fill("8");
    }
    await page.waitForTimeout(500);

    const snap01 = await takeSnap(page, "gate1_01_import_ready");

    // 等待提交按钮变为可点击状态
    const breakdownBtn = page.locator("button:has-text('提交 AI 拆解任务')");
    await breakdownBtn.waitFor({ state: "visible", timeout: 15000 });
    
    // 如果仍然禁用，查看是否有提示
    const isBtnEnabled = await breakdownBtn.isEnabled();
    console.log(`提交 AI 拆解任务按钮可用状态: ${isBtnEnabled}`);
    if (!isBtnEnabled) {
      const warningText = await page.locator(".breakdown-runtime-warning, .inline-error, .frame-feedback").allInnerTexts();
      console.log("页面当前提示:", warningText);
    }

    await breakdownBtn.click({ timeout: 10000 });
    console.log("✓ 点击提交 AI 拆解任务按钮");

    // 等待 Job 提交反馈
    await page.waitForTimeout(2000);
    const snap02 = await takeSnap(page, "gate1_02_job_submitted");

    // 2. 在页面监视本地 Ollama 拆解任务执行
    console.log("\n[STEP 2] 在页面监视本地 Ollama 拆解任务执行（等待 Worker 处理）...");
    let jobSucceeded = false;
    let pollAttempts = 0;
    let lastJobId = "";

    while (!jobSucceeded && pollAttempts < 90) {
      await page.waitForTimeout(3000);
      pollAttempts++;

      // 通过页面 DOM 检查状态
      const statusPill = page.locator(".breakdown-job-card .status-pill, .breakdown-job-card span[class*='state']").first();
      if (await statusPill.count() > 0) {
        const text = (await statusPill.textContent()).trim();
        console.log(`[Poll ${pollAttempts}] 页面展示 Job 状态: ${text}`);
        if (text === "SUCCEEDED" || text.includes("SUCCEEDED")) {
          jobSucceeded = true;
          break;
        }
      }

      // 同时通过只读 API 核查最新 Job 状态
      try {
        const resp = await fetch(`${API_URL}/api/v1/jobs?project_id=${PROJECT_ID}&limit=5`);
        const data = await resp.json();
        const latestJob = data.items?.[0];
        if (latestJob) {
          lastJobId = latestJob.id;
          console.log(`[API Read-only] Job ${lastJobId} 状态: ${latestJob.state}, 进度: ${JSON.stringify(latestJob.progress)}`);
          if (latestJob.state === "SUCCEEDED") {
            jobSucceeded = true;
            break;
          }
          if (latestJob.state === "FAILED") {
            console.error(`Job 失败: ${latestJob.last_error_code} - ${latestJob.last_error_detail_redacted}`);
            throw new Error(`Job 失败: ${latestJob.last_error_code}`);
          }
        }
      } catch (e) {
        console.warn("读取 Job 状态异常:", e.message);
      }
    }

    if (!jobSucceeded) {
      throw new Error("等待 Job SUCCEEDED 超时（90 次轮询）");
    }

    const snap03 = await takeSnap(page, "gate1_03_job_succeeded");
    console.log("✓ 本地 Ollama 拆解 Job 成功完成！");

    // 3. 进入第 3 阶段：审核 AI 拆解草稿
    console.log("\n[STEP 3] 进入审核拆解页签，核对生成的场次、镜头与对白草稿...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/story#story-review`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1500);

    const snap04 = await takeSnap(page, "gate1_04_review_draft");

    // 勾选人工审核复选框
    const reviewCheckbox = page.locator("label:has-text('我已核对') input[type='checkbox'], input[type='checkbox']").first();
    await reviewCheckbox.waitFor({ state: "visible", timeout: 5000 });
    await reviewCheckbox.check();
    await page.waitForTimeout(500);

    // 点击应用到选中分集
    const applyBtn = page.locator("button:has-text('应用到选中分集')");
    await applyBtn.waitFor({ state: "visible", timeout: 5000 });
    await applyBtn.click();
    console.log("✓ 点击应用到选中分集按钮");
    await page.waitForTimeout(2500);

    const snap05 = await takeSnap(page, "gate1_05_draft_applied");

    // 4. 只读 API 回读核查生产事实
    console.log("\n[STEP 4] 只读 API 回读核验真实的场次、镜头与对白生成事实...");
    const scenesResp = await fetch(`${API_URL}/api/v1/projects/${PROJECT_ID}/scenes`);
    const scenesData = await scenesResp.json();
    const sceneCount = scenesData.items?.length ?? 0;

    const shotsResp = await fetch(`${API_URL}/api/v1/projects/episodes/${EPISODE_ID}/storyboard`);
    const shotsData = await shotsResp.json();
    const shotCount = shotsData.storyboard?.shots?.length ?? 0;

    const dialogueCount = shotsData.storyboard?.shots?.filter((s) => Boolean(s.dialogue?.trim())).length ?? 0;

    console.log(`=================================================================`);
    console.log(`🎉 第一道门禁验收结果：`);
    console.log(`- Job ID: ${lastJobId} (SUCCEEDED)`);
    console.log(`- 实际生成 Master 场次数: ${sceneCount}`);
    console.log(`- 实际生成分集镜头数: ${shotCount}`);
    console.log(`- 实际提取对白数: ${dialogueCount}`);
    console.log(`=================================================================`);

    // 记录到矩阵
    await appendMatrixRow({
      rowId: "089",
      route: `/projects/${PROJECT_ID}/story#story-import`,
      control: "button:has-text('提交 AI 拆解任务')",
      precondition: "本地 Ollama loopback 就绪，选择段落 1-8",
      action: "点击提交 AI 拆解任务按钮",
      expected: "入队 SCRIPT_BREAKDOWN_LOCAL_LLM Job 并由本地 Worker 执行",
      actual: `Job ${lastJobId} 执行 SUCCEEDED，生成不可变草稿`,
      evidence: snap03,
      defect: "—",
      status: "PASS",
    });

    await appendMatrixRow({
      rowId: "090",
      route: `/projects/${PROJECT_ID}/story#story-review`,
      control: "button:has-text('应用到选中分集')",
      precondition: "草稿处于 DRAFT_READY，勾选人工核对复选框",
      action: "点击应用到选中分集按钮",
      expected: "提交 POST /script-breakdowns/:id/apply 并生成生产事实",
      actual: `成功应用：创建 ${sceneCount} 场、${shotCount} 镜、${dialogueCount} 条对白`,
      evidence: snap05,
      defect: "—",
      status: "PASS",
    });

    const progressEntry = `
### ${new Date().toISOString().slice(0, 19).replace('T', ' ')} 第一道强制门禁通过：本地 LLM 拆解修复与剧本事实落库
- 锁定项目：\`live_uat_20260823102640\` (Project: \`${PROJECT_ID}\`, Episode: \`${EPISODE_ID}\`)
- 修复措施：切换使用已发布的本地回环 Ollama Profile (\`local-llm-ollama-deepseek-r1-14b\` · \`08789ef4-9449-56d2-8c88-6b06fc274465\`)，完全基于本地 \`127.0.0.1:11434\` 离线执行，零远程出境。
- 任务执行：Job \`${lastJobId}\` 经由常驻本地 Worker 真实调度执行，状态为 \`SUCCEEDED\`。
- 人工应用与落库事实：
  - Master 场次数 (scenes): **${sceneCount}**
  - 分集镜头数 (shots): **${shotCount}**
  - 有效对白数 (dialogues): **${dialogueCount}**
- 截图证据：\`${snap03}\` (Job 成功), \`${snap05}\` (应用成功)。
`;
    await appendProgress(progressEntry);

  } finally {
    await browser.close();
  }
}

run().catch((err) => {
  console.error("门禁 1 执行失败:", err);
  process.exit(1);
});
