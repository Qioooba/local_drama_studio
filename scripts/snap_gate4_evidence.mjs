import { chromium } from "playwright";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..");
const SCREENS_DIR = path.join(REPO_ROOT, "docs", "evidence", "ui-uat-2026-08-22", "screens");
const BASE_URL = "http://127.0.0.1:5173";

const PROJECT_ID = "9893a9bc-e58b-45a2-9143-c1bd7b886db9";
const EPISODE_ID = "b989644a-666e-448c-968b-6b865dbebca7";

async function main() {
  const browser = await chromium.launch({ headless: false, slowMo: 100 });
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });

  // 1. Generation Workbench
  await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/generation`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(SCREENS_DIR, "gate4_01_generation_workbench_proxy_winner.jpg"), type: "jpeg", quality: 35 });

  // 2. Director Desk with Video Winner
  await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/direct`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(SCREENS_DIR, "gate4_02_director_desk_video_winner.jpg"), type: "jpeg", quality: 35 });

  await browser.close();
}

main().catch(console.error);
