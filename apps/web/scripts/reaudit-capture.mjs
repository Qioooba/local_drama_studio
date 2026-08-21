// 修复后重采集：按 ui-audit.mjs 相同的模块选择器与整页截图逻辑，
// 跳过重型交互循环，用于与 codex-ui-audit 旧批次做对比。
// 用法: node apps/web/scripts/reaudit-capture.mjs
import fs from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";

const BASE_URL = "http://127.0.0.1:5173";
const VIEWS = ["overview", "projects", "canvas", "reviews", "jobs", "profiles", "generation", "diagnostics"];
const OUTPUT_ROOT = path.resolve(process.cwd(), "apps/web/screenshots/codex-ui-audit");
const RUN_TS = process.env.REAUDIT_RUN || "2026-08-18T12-00-00-000Z-REAUDIT";
const RUN_DIR = path.join(OUTPUT_ROOT, RUN_TS);

const normalize = (value = "") =>
  String(value)
    .replace(/\s+/g, "_")
    .replace(/[^\w\u4e00-\u9fff-]/g, "")
    .slice(0, 64) || "item";

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true });
}

async function autoReveal(page) {
  await page.evaluate(async () => {
    const delay = 120;
    let previousHeight = 0;
    while (true) {
      const h = document.body.scrollHeight;
      window.scrollTo(0, h);
      await new Promise((r) => setTimeout(r, delay));
      const nextHeight = document.body.scrollHeight;
      if (nextHeight === previousHeight) break;
      previousHeight = nextHeight;
      if (nextHeight === h) break;
    }
    window.scrollTo(0, 0);
  });
}

async function screenshotModules(page, viewDir) {
  const moduleDir = path.join(viewDir, "modules");
  await ensureDir(moduleDir);
  const selectors = [
    "section.panel",
    "section",
    ".panel",
    ".card-grid",
    ".project-filters",
    ".hero",
    ".system-strip",
    ".production-summary",
    "form",
    "[role='region']",
  ];
  let moduleCount = 0;
  for (const selector of selectors) {
    const elements = await page.$$(selector);
    for (const el of elements) {
      const visible = await el.isVisible().catch(() => false);
      if (!visible) continue;
      const box = await el.boundingBox().catch(() => null);
      if (!box || box.width < 120 || box.height < 50) continue;
      const title = await el.evaluate((node) => {
        const header = node.querySelector("h1,h2,h3,h4,p.eyebrow");
        return (header?.textContent || node.getAttribute("aria-label") || "module").trim();
      });
      const filename = normalize(`${moduleCount + 1}_${title}`).slice(0, 80);
      const filepath = path.join(moduleDir, `${filename}.png`);
      await el.screenshot({ path: filepath, timeout: 3000 }).catch(() => {});
      moduleCount += 1;
    }
  }
  return moduleCount;
}

async function captureView(context, viewName) {
  const page = await context.newPage({ viewport: { width: 1600, height: 900 } });
  const viewDir = path.join(RUN_DIR, "pages", viewName);
  await ensureDir(viewDir);
  const consoleErrors = [];
  page.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(msg.text()); });
  page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));
  let fullOk = false;
  let moduleCount = 0;
  try {
    await page.goto(`${BASE_URL}/?view=${viewName}`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(4500);
    await page.waitForSelector(".content .hero", { timeout: 20000 }).catch(() => undefined);
    await autoReveal(page);
    const fullPath = path.join(viewDir, `${viewName}-full.png`);
    await page.screenshot({ path: fullPath, fullPage: true });
    fullOk = true;
    moduleCount = await screenshotModules(page, viewDir);
  } catch (error) {
    consoleErrors.push(`capture error: ${String(error).slice(0, 300)}`);
  }
  console.log(`CAPTURED ${viewName} full=${fullOk} modules=${moduleCount} consoleErrors=${consoleErrors.length}`);
  await page.close();
}

await ensureDir(RUN_DIR);
const browser = await chromium.launch({
  headless: true,
  executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
});
const context = await browser.newContext();
try {
  for (const view of VIEWS) {
    await captureView(context, view);
  }
} finally {
  await context.close();
  await browser.close();
}
console.log(`REAUDIT_DONE=${RUN_DIR}`);
