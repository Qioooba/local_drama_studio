import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE_URL = process.env.BASE_URL || "http://localhost:5173";
const PROJECT_ID = "33af2fba-4bb3-4e18-87a1-41aca8251475";
const EVIDENCE_DIR = path.resolve("docs/evidence/prison_break_e2e");
const SCREENSHOT_DIR = path.join(EVIDENCE_DIR, "screenshots");

fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });

function log(msg) {
  const ts = new Date().toISOString().substring(11, 19);
  console.log(`[${ts}] ${msg}`);
}

async function run() {
  log("Starting Playwright Real Browser E2E Test with Full Network Inspection...");
  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });

  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });

  const page = await context.newPage();

  page.on("request", (req) => {
    if (req.url().includes("/api/")) {
      log(`--> ${req.method()} ${req.url()}`);
    }
  });

  page.on("response", async (res) => {
    if (res.url().includes("/api/")) {
      const status = res.status();
      if (status >= 400) {
        let errBody = "";
        try {
          errBody = await res.text();
        } catch {}
        log(`<-- ERROR ${status} ${res.url()}: ${errBody}`);
      } else {
        log(`<-- OK ${status} ${res.url()}`);
      }
    }
  });

  try {
    const storyboardUrl = `${BASE_URL}/explainers/${PROJECT_ID}/storyboard`;
    log(`Navigating to Storyboard: ${storyboardUrl}`);
    await page.goto(storyboardUrl, { waitUntil: "networkidle", timeout: 30000 });
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "01_storyboard_init.png"), fullPage: true });

    await page.waitForTimeout(2000);
    const backfillBtn = page.locator("button", { hasText: /补齐缺失画面/ }).first();
    const isBackfillVisible = await backfillBtn.isVisible().catch(() => false);
    log(`Backfill button visible: ${isBackfillVisible}`);

    if (isBackfillVisible) {
      log("Clicking '补齐缺失画面'...");
      await backfillBtn.click();
      log("Waiting 30 seconds for all backfill submissions to finish...");
      await page.waitForTimeout(30000);
      await page.screenshot({ path: path.join(SCREENSHOT_DIR, "02_storyboard_after_backfill.png"), fullPage: true });
    }

  } catch (err) {
    log(`Test error: ${err.message}`);
  } finally {
    await browser.close();
    log("Test run finished.");
  }
}

run();
