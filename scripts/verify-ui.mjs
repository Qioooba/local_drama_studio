// 轻量 UI 验证脚本：加载每个视图、测加载时间、检测横向溢出/表单重叠、截图。
// 用法: node scripts/verify-ui.mjs [--out test-results/ui-verify]
import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const BASE = "http://127.0.0.1:5173";
const outDir = process.argv.includes("--out")
  ? process.argv[process.argv.indexOf("--out") + 1]
  : "test-results/ui-verify";
mkdirSync(outDir, { recursive: true });

const views = [
  ["overview", "overview"],
  ["projects", "projects"],
  ["generation", "generation"],
  ["reviews", "reviews"],
  ["canvas", "canvas"],
  ["jobs", "jobs"],
  ["profiles", "profiles"],
  ["diagnostics", "diagnostics"],
];

function intersection(a, b) {
  const x = Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x));
  const y = Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y));
  return x * y;
}

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
  headless: true,
});
const results = [];
try {
  for (const [label, view] of views) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const consoleErrors = [];
    const pageErrors = [];
    page.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(msg.text().slice(0, 200)); });
    page.on("pageerror", (err) => pageErrors.push(String(err).slice(0, 200)));
    const t0 = Date.now();
    try {
      await page.goto(`${BASE}/?view=${view}`, { waitUntil: "domcontentloaded", timeout: 30000 });
      await page.waitForTimeout(3500); // 等待查询渲染
      await page.waitForSelector(".content .hero", { timeout: 10000 }).catch(() => undefined);
    } catch (err) {
      results.push({ view, label, loadMs: Date.now() - t0, loadError: String(err).slice(0, 300), consoleErrors, pageErrors, overflow: null, notes: [] });
      await page.screenshot({ path: path.join(outDir, `${view}__error.png`), fullPage: true }).catch(() => undefined);
      await page.close();
      continue;
    }
    const loadMs = Date.now() - t0;

    // 横向溢出检测
    const overflow = await page.evaluate(() => {
      const doc = document.documentElement;
      return { scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth, overflowed: doc.scrollWidth > doc.clientWidth + 2 };
    });

    const notes = [];
    // 字段网格重叠检测（同一 field-grid 内相邻 label 是否相交）
    const overlaps = await page.evaluate(() => {
      const found = [];
      document.querySelectorAll(".field-grid").forEach((grid, gi) => {
        const labels = Array.from(grid.querySelectorAll("label")).filter((el) => el.offsetWidth > 0);
        for (let i = 0; i < labels.length; i += 1) {
          for (let j = i + 1; j < labels.length; j += 1) {
            const a = labels[i].getBoundingClientRect();
            const b = labels[j].getBoundingClientRect();
            const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
            const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
            if (x * y > 40) found.push({ grid: gi, labels: [labels[i].textContent?.slice(0, 18), labels[j].textContent?.slice(0, 18)], area: Math.round(x * y) });
          }
        }
      });
      return found;
    });
    if (overlaps.length) notes.push(`field-grid 重叠 ${overlaps.length} 处: ${JSON.stringify(overlaps.slice(0, 3))}`);

    if (view === "generation") {
      const jsonRows = await page.evaluate(() => document.querySelectorAll(".generation-control-panel textarea").length);
      notes.push(`导演控制 JSON 文本域数=${jsonRows}`);
    }
    if (view === "jobs") {
      const emptyRows = await page.evaluate(() => Array.from(document.querySelectorAll(".job-row")).filter((r) => (r.textContent ?? "").trim().length < 4).length);
      notes.push(`空任务行=${emptyRows}`);
      const pills = await page.evaluate(() => Array.from(document.querySelectorAll(".job-row .status-pill")).map((p) => p.textContent?.trim()).slice(0, 12));
      notes.push(`任务状态徽章=${JSON.stringify(pills)}`);
    }
    if (view === "reviews") {
      const rows = await page.evaluate(() => document.querySelectorAll(".review-row").length);
      const brokenImgs = await page.evaluate(() => Array.from(document.images).filter((img) => img.complete && img.naturalWidth === 0 && img.src.includes("thumbnail")).length);
      notes.push(`审核行=${rows} 失败缩略图=${brokenImgs}`);
    }
    if (view === "canvas") {
      const nodeButtons = await page.evaluate(() => document.querySelectorAll(".canvas-node-list button").length);
      notes.push(`画布节点=${nodeButtons}`);
    }

    results.push({ view, label, loadMs, loadError: null, consoleErrors, pageErrors, overflow, notes });
    await page.screenshot({ path: path.join(outDir, `${view}__viewport.png`) });
    await page.screenshot({ path: path.join(outDir, `${view}__full.png`), fullPage: true });
    await page.close();
  }
} finally {
  await browser.close();
}

const report = { generatedAt: new Date().toISOString(), results };
writeFileSync(path.join(outDir, "verify-report.json"), JSON.stringify(report, null, 2));
for (const r of results) {
  const status = r.loadError ? "LOAD-ERROR" : r.overflow?.overflowed ? "OVERFLOW" : "OK";
  console.log(`[${status}] ${r.view.padEnd(12)} load=${r.loadMs}ms overflow=${r.overflow ? r.overflow.scrollWidth + "/" + r.overflow.clientWidth : "-"} ${r.notes.join(" | ")}`);
  if (r.consoleErrors.length) console.log(`    consoleErrors: ${r.consoleErrors.slice(0, 5).join(" ;; ")}`);
  if (r.pageErrors.length) console.log(`    pageErrors: ${r.pageErrors.slice(0, 3).join(" ;; ")}`);
  if (r.loadError) console.log(`    loadError: ${r.loadError}`);
}
console.log(`report: ${path.resolve(outDir, "verify-report.json")}`);
