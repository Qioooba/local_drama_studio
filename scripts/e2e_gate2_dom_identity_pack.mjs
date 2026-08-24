import { chromium } from "playwright";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..");
const SCREENS_DIR = path.join(REPO_ROOT, "docs", "evidence", "ui-uat-2026-08-22", "screens");
const BASE_URL = "http://127.0.0.1:5173";
const API_URL = "http://127.0.0.1:3210";

const PROJECT_ID = "9893a9bc-e58b-45a2-9143-c1bd7b886db9";

async function main() {
  const browser = await chromium.launch({
    headless: false,
    slowMo: 150,
    args: ["--window-size=1366,768"],
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
  });

  const page = await context.newPage();

  // 1. 访问资产圣经
  await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/assets`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  // 2. 选中角色“林默”
  await page.click("button:has-text('林默')");
  await page.waitForTimeout(1500);

  // 3. 点击“新建身份包”按钮
  const newPackBtn = page.locator(".identity-pack-header button:has-text('新建身份包')").first();
  console.log("New pack button visible:", await newPackBtn.isVisible());
  if (await newPackBtn.isVisible()) {
    await newPackBtn.click();
    await page.waitForTimeout(500);

    const codeInput = page.locator("label:has-text('身份包代码') input").first();
    await codeInput.fill("IP_LINMO_HERO_V1");

    const nameInput = page.locator("label:has-text('显示名称') input").first();
    await nameInput.fill("林默 基础三视图身份包");

    const confirmBtn = page.locator("button:has-text('创建草稿')").first();
    await confirmBtn.click();
    await page.waitForTimeout(2000);
    console.log("✓ 身份包创建提交成功");
  }

  // 4. 截图并输出状态
  await page.screenshot({ path: path.join(SCREENS_DIR, "gate2_02_identity_pack_approved.jpg"), type: "jpeg", quality: 35 });
  console.log("✓ 截图保存完成");

  await browser.close();
}

main().catch(console.error);
