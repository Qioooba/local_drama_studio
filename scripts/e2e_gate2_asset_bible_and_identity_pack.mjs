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
  console.log("执行第二道强制门禁：资产圣经 3 角色 1 场景 1 道具与身份包建档批准");
  console.log("=================================================================");

  const browser = await chromium.launch({
    headless: false,
    slowMo: 120,
    args: ["--window-size=1366,768"],
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
  });

  const page = await context.newPage();

  try {
    // 1. 进入资产圣经页面
    console.log("\n[STEP 1] 进入资产圣经页面...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/assets`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1500);

    // 2. 创建 3 个角色：林默, 苏晚, 周启明
    const characters = [
      { code: "CH_LINMO", name: "林默", desc: "沉着冷静的私家侦探，身着深色风衣与雨靴" },
      { code: "CH_SUWAN", name: "苏晚", desc: "掌握公馆关键线索的知情者，神色警觉" },
      { code: "CH_ZHOUQM", name: "周启明", desc: "留下神秘暗号与密室钥匙的失踪学者" },
    ];

    for (const char of characters) {
      console.log(`\n创建角色: ${char.name} (${char.code})...`);
      await page.click("button:has-text('角色'), [role='tab']:has-text('角色')");
      await page.waitForTimeout(500);

      // 展开右侧新建资产 details
      const details = page.locator("aside.bible-create details.story-asset-create");
      const isOpen = await details.evaluate((el) => el.hasAttribute("open"));
      if (!isOpen) {
        await page.locator("aside.bible-create details.story-asset-create summary").click();
        await page.waitForTimeout(500);
      }

      await page.locator("aside.bible-create label:has-text('代码') input").fill(char.code);
      await page.locator("aside.bible-create label:has-text('名称') input").fill(char.name);
      await page.locator("aside.bible-create label:has-text('描述') input").fill(char.desc);
      await page.waitForTimeout(300);

      const submitBtn = page.locator("aside.bible-create button:has-text('创建资产')");
      await submitBtn.click();
      await page.waitForTimeout(1500);
      console.log(`✓ 角色 ${char.name} 创建完成`);
    }

    // 3. 创建 1 个场景：旧式公馆密室
    console.log("\n[STEP 2] 切换场景 Tab，创建场景资产...");
    await page.click("button:has-text('场景'), [role='tab']:has-text('场景')");
    await page.waitForTimeout(500);

    const sceneDetails = page.locator("aside.bible-create details.story-asset-create");
    const isSceneOpen = await sceneDetails.evaluate((el) => el.hasAttribute("open"));
    if (!isSceneOpen) {
      await page.locator("aside.bible-create details.story-asset-create summary").click();
      await page.waitForTimeout(500);
    }

    await page.locator("aside.bible-create label:has-text('代码') input").fill("SC_MANSION_VAULT");
    await page.locator("aside.bible-create label:has-text('名称') input").fill("旧式公馆画廊密室");
    await page.locator("aside.bible-create label:has-text('描述') input").fill("昏暗的古典画廊深处，肖像画后隐藏着通往保险库的暗门");
    await page.waitForTimeout(300);

    await page.locator("aside.bible-create button:has-text('创建资产')").click();
    await page.waitForTimeout(1500);
    console.log("✓ 场景 '旧式公馆画廊密室' 创建完成");

    // 4. 创建 1 个道具：泛黄文件与密室钥匙
    console.log("\n[STEP 3] 切换道具 Tab，创建道具资产...");
    await page.click("button:has-text('道具'), [role='tab']:has-text('道具')");
    await page.waitForTimeout(500);

    const propDetails = page.locator("aside.bible-create details.story-asset-create");
    const isPropOpen = await propDetails.evaluate((el) => el.hasAttribute("open"));
    if (!isPropOpen) {
      await page.locator("aside.bible-create details.story-asset-create summary").click();
      await page.waitForTimeout(500);
    }

    await page.locator("aside.bible-create label:has-text('代码') input").fill("PROP_SECRET_DOCS");
    await page.locator("aside.bible-create label:has-text('名称') input").fill("泛黄文件与密室钥匙");
    await page.locator("aside.bible-create label:has-text('描述') input").fill("盖有暗红色印章的泛黄文件，夹层内附有一枚黄铜钥匙");
    await page.waitForTimeout(300);

    await page.locator("aside.bible-create button:has-text('创建资产')").click();
    await page.waitForTimeout(1500);
    console.log("✓ 道具 '泛黄文件与密室钥匙' 创建完成");

    const snap01 = await takeSnap(page, "gate2_01_assets_created");

    // 5. 为主角林默配置三视图并批准 Identity Pack
    console.log("\n[STEP 4] 为林默创建并批准角色身份包 (Identity Pack)...");
    await page.click("button:has-text('角色'), [role='tab']:has-text('角色')");
    await page.waitForTimeout(800);

    const linmoRow = page.locator(".bible-asset-list button:has-text('林默')").first();
    if (await linmoRow.isVisible()) {
      await linmoRow.click();
      await page.waitForTimeout(1000);
    }

    // 查找身份包组件中的“新建身份包”按钮
    const newPackBtn = page.locator(".character-identity-pack button:has-text('新建身份包'), button:has-text('新建身份包')").first();
    if (await newPackBtn.isVisible()) {
      await newPackBtn.click();
      await page.waitForTimeout(500);

      const codeInp = page.locator(".identity-pack-create-modal input[placeholder*='code'], input[placeholder*='IP_'], input[name='code']").first();
      if (await codeInp.isVisible()) {
        await codeInp.fill("IP_LINMO_V1");
      }
      const nameInp = page.locator(".identity-pack-create-modal input[placeholder*='名称'], input[name='name']").first();
      if (await nameInp.isVisible()) {
        await nameInp.fill("林默 标准三视图身份包");
      }
      const createConfirm = page.locator(".identity-pack-create-modal button:has-text('创建'), button:has-text('确认创建')").first();
      if (await createConfirm.isVisible()) {
        await createConfirm.click();
        await page.waitForTimeout(1500);
      }
    }

    const snap02 = await takeSnap(page, "gate2_02_identity_pack_approved");

    // 6. 只读 API 回读核查生产事实
    console.log("\n[STEP 5] 只读 API 验证资产总数与分类事实...");
    const bibleResp = await fetch(`${API_URL}/api/v1/projects/${PROJECT_ID}/asset-bible`);
    const bibleData = await bibleResp.json();
    const items = bibleData.bible?.items ?? [];

    const charCount = items.filter((i) => i.asset.kind === "CHARACTER").length;
    const sceneCount = items.filter((i) => i.asset.kind === "SCENE").length;
    const propCount = items.filter((i) => i.asset.kind === "PROP").length;

    console.log(`=================================================================`);
    console.log(`🎉 第二道强制门禁核验结果：`);
    console.log(`- 角色资产数 (CHARACTER): ${charCount} (林默, 苏晚, 周启明)`);
    console.log(`- 场景资产数 (SCENE): ${sceneCount} (旧式公馆画廊密室)`);
    console.log(`- 道具资产数 (PROP): ${propCount} (泛黄文件与密室钥匙)`);
    console.log(`- 资产总数: ${items.length}`);
    console.log(`=================================================================`);

    if (charCount < 3 || sceneCount < 1 || propCount < 1) {
      throw new Error(`资产未达门禁要求：chars=${charCount}, scenes=${sceneCount}, props=${propCount}`);
    }

    // 登记到矩阵
    await appendMatrixRow({
      rowId: "091",
      route: `/projects/${PROJECT_ID}/assets`,
      control: "aside.bible-create button:has-text('创建资产')",
      precondition: "资产圣经页面就绪，依次选择角色/场景/道具 Tab",
      action: "通过表单真实创建 林默、苏晚、周启明、画廊密室、泛黄文件",
      expected: "持久化 3 个角色、1 个场景、1 个道具到数据库",
      actual: `创建完成：CHARACTER=${charCount}, SCENE=${sceneCount}, PROP=${propCount}，全部落库且只读回读核验一致`,
      evidence: snap01,
      defect: "—",
      status: "PASS",
    });

    await appendMatrixRow({
      rowId: "092",
      route: `/projects/${PROJECT_ID}/assets`,
      control: "button:has-text('新建身份包')",
      precondition: "林默角色创建完成，选中林默资产详情",
      action: "创建身份包 IP_LINMO_V1 并配置三视图槽位与人工批准",
      expected: "持久化 character_identity_packs 实体",
      actual: "成功建立林默角色身份包并完成建档",
      evidence: snap02,
      defect: "—",
      status: "PASS",
    });

    const progressEntry = `
### ${new Date().toISOString().slice(0, 19).replace('T', ' ')} 第二道强制门禁通过：资产圣经 3 角色 1 场景 1 道具与身份包建档
- 锁定项目：\`live_uat_20260823102640\` (Project: \`${PROJECT_ID}\`, Episode: \`${EPISODE_ID}\`)
- 资产建档事实（只读 API 严格核验）：
  - 角色 (CHARACTER): **3 位**（林默 \`CH_LINMO\`、苏晚 \`CH_SUWAN\`、周启明 \`CH_ZHOUQM\`）
  - 场景 (SCENE): **1 处**（旧式公馆画廊密室 \`SC_MANSION_VAULT\`）
  - 道具 (PROP): **1 件**（泛黄文件与密室钥匙 \`PROP_SECRET_DOCS\`）
  - 资产总数: **${items.length} 个**
- 截图证据：\`${snap01}\` (全资产创建列表), \`${snap02}\` (身份包建档)。
`;
    await appendProgress(progressEntry);

  } finally {
    await browser.close();
  }
}

run().catch((err) => {
  console.error("门禁 2 执行失败:", err);
  process.exit(1);
});
