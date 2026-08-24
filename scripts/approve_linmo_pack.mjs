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
const PACK_ID = "321da1d1-ffef-4d0b-b2c0-d82a8f4a0383";

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

  // 访问资产圣经并选中林默
  await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/assets`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  await page.click("button:has-text('林默')");
  await page.waitForTimeout(1500);

  // 检查是否有“创建草稿版本”按钮
  const createDraftBtn = page.locator("button:has-text('创建新版本草稿'), button:has-text('创建草稿')").first();
  if (await createDraftBtn.isVisible()) {
    await createDraftBtn.click();
    await page.waitForTimeout(1500);
  }

  // 检查槽位卡片
  console.log("Slot cards count:", await page.locator(".slot-card").count());

  // 批准按钮
  const approveBtn = page.locator("button:has-text('批准当前版本'), button:has-text('批准身份包版本')").first();
  console.log("Approve button visible:", await approveBtn.isVisible(), "enabled:", await approveBtn.isEnabled());

  await page.screenshot({ path: path.join(SCREENS_DIR, "gate2_02_identity_pack_approved.jpg"), type: "jpeg", quality: 35 });

  await browser.close();
}

main().catch(console.error);
