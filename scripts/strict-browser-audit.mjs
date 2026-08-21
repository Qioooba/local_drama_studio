// 严格浏览器实测：每视图 加载→滚动→交互，全程捕获
// console.error / pageerror / 未捕获 rejection / 网络 4xx/5xx / 资源加载失败，
// 以及视口内元素重叠与横向溢出。
// 用法: node scripts/strict-browser-audit.mjs [--no-interact]
import { chromium } from "playwright";
import { writeFileSync } from "node:fs";

const BASE = "http://127.0.0.1:5173";
const VIEWS = ["overview", "projects", "canvas", "reviews", "jobs", "profiles", "generation", "diagnostics"];
const INTERACT = !process.argv.includes("--no-interact");
const EXEC = process.env.PLAYWRIGHT_CHROMIUM_PATH || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

const browser = await chromium.launch({ executablePath: EXEC, headless: true });
const report = { generatedAt: new Date().toISOString(), views: [] };

for (const view of VIEWS) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = { console: [], pageerror: [], rejection: [], http4xx: [], http5xx: [], requestfailed: [], late: [] };
  const seen = new Set();
  const push = (bucket, text) => { const k = String(text).slice(0, 160); if (!seen.has(k + bucket)) { seen.add(k + bucket); errors[bucket].push(k); } };

  page.on("console", (msg) => { if (msg.type() === "error") push("console", msg.text()); });
  page.on("pageerror", (e) => push("pageerror", String(e).slice(0, 200)));
  page.on("requestfailed", (r) => {
    const errText = r.failure()?.errorText || "";
    // 导航/页面关闭导致的请求中止属正常现象，不算故障
    if (errText.includes("ERR_ABORTED")) return;
    push("requestfailed", `${r.url().slice(0, 90)} :: ${errText.slice(0, 60)}`);
  });
  page.on("response", (r) => {
    if (r.status() >= 500) push("http5xx", `${r.status()} ${r.url().replace(BASE, "").slice(0, 90)}`);
    else if (r.status() >= 400 && !r.url().includes("/api/")) push("http4xx", `${r.status()} ${r.url().replace(BASE, "").slice(0, 90)}`);
  });
  page.on("pageerror", (e) => { /* 已在 pageerror 收集 */ });

  // 捕获未处理 promise rejection（通过注入）
  await page.addInitScript(() => {
    window.addEventListener("unhandledrejection", (e) => {
      // @ts-ignore
      window.__rejections = window.__rejections || [];
      // @ts-ignore
      window.__rejections.push(String(e.reason).slice(0, 200));
    });
  });

  const stage = (name) => {
    // 读取注入的 rejection 列表
    return page.evaluate((n) => window.__rejections || [], name).catch(() => []);
  };

  // --- 阶段 1: 加载 ---
  await page.goto(`${BASE}/?view=${view}`, { waitUntil: "domcontentloaded", timeout: 40000 });
  await page.waitForTimeout(5000);
  for (const r of await stage("load")) push("late", `unhandledrejection: ${r}`);

  // --- 阶段 2: 滚动全页（触发懒加载）---
  await page.evaluate(async () => {
    const delay = 90;
    let prev = 0;
    while (true) {
      window.scrollTo(0, document.body.scrollHeight);
      await new Promise((r) => setTimeout(r, delay));
      const h = document.body.scrollHeight;
      if (h === prev) break;
      prev = h;
    }
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(2500);
  for (const r of await stage("scroll")) push("late", `unhandledrejection(after scroll): ${r}`);

  // --- 阶段 3: 视口内重叠 + 横向溢出检测 ---
  const layout = await page.evaluate(() => {
    const out = { overflowX: false, overlaps: [] };
    out.overflowX = document.documentElement.scrollWidth > document.documentElement.clientWidth + 2;
    const visible = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); const inClosed = el.closest("details") && !el.closest("details")?.open; return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none" && !inClosed; };
    // 视口内（第一屏）元素两两重叠检测：只对 input/select/textarea/button 类
    const els = Array.from(document.querySelectorAll("input:not([type=hidden]):not([type=checkbox]):not([type=radio]), select, textarea, button, a")).filter(visible);
    const vh = window.innerHeight;
    for (let i = 0; i < els.length; i += 1) {
      const a = els[i].getBoundingClientRect();
      if (a.top > vh || a.bottom < 0) continue;
      for (let j = i + 1; j < els.length; j += 1) {
        const b = els[j].getBoundingClientRect();
        const ix = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
        const iy = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
        if (ix * iy > 160) {
          const name = (el) => (el.getAttribute("aria-label") || el.textContent?.trim().slice(0, 16) || el.className.toString().slice(0, 16) || el.tagName);
          out.overlaps.push({ a: name(els[i]), b: name(els[j]), area: Math.round(ix * iy) });
        }
      }
    }
    return out;
  });

  // --- 阶段 4: 交互（可选）---
  const interact = [];
  let interactSummary = "skip";
  if (INTERACT) {
    const btns = page.locator("button:not([disabled])");
    const total = await btns.count();
    const limit = Math.min(total, 15);
    for (let i = 0; i < limit; i += 1) {
      const btn = btns.nth(i);
      const label = await btn.textContent().catch(() => "");
      try {
        await btn.click({ timeout: 900 });
        await page.waitForTimeout(120);
      } catch (e) {
        interact.push({ btn: (label || "").trim().slice(0, 30), err: String(e).slice(0, 90) });
      }
    }
    await page.waitForTimeout(1000);
    for (const r of await stage("interact")) push("late", `unhandledrejection(after interact): ${r}`);
    // 交互后再次滚动
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight)).catch(() => {});
    await page.waitForTimeout(500);
    interactSummary = `点击 ${Math.min(total, 15)} 个按钮, 失败 ${interact.length}`;
  }

  report.views.push({
    view,
    errors,
    layout,
    interactSummary,
    interactFailures: interact.slice(0, 5),
  });
  const totalErr = errors.console.length + errors.pageerror.length + errors.late.length + errors.http5xx.length + errors.requestfailed.length;
  const overlapN = layout.overlaps.length;
  console.log(`${view.padEnd(12)} console=${errors.console.length} pageerror=${errors.pageerror.length} rejection=${errors.late.length} 5xx=${errors.http5xx.length} reqfail=${errors.requestfailed.length} overflow=${layout.overflowX} overlaps=${overlapN}${totalErr || overlapN ? "" : " ✓"}`);
  if (errors.console.length) console.log(`    console: ${errors.console.slice(0, 3).join(" ;; ")}`);
  if (errors.pageerror.length) console.log(`    pageerror: ${errors.pageerror.slice(0, 3).join(" ;; ")}`);
  if (errors.http5xx.length) console.log(`    5xx: ${errors.http5xx.slice(0, 3).join(" ;; ")}`);
  if (errors.late.length) console.log(`    rejection: ${errors.late.slice(0, 3).join(" ;; ")}`);
  if (layout.overlaps.length) console.log(`    overlaps: ${JSON.stringify(layout.overlaps.slice(0, 4))}`);
  await page.close();
}

await browser.close();
writeFileSync("test-results/strict-browser-audit.json", JSON.stringify(report, null, 2));
const bad = report.views.filter((v) => v.errors.console.length + v.errors.pageerror.length + v.errors.late.length + v.errors.http5xx.length + v.errors.requestfailed.length + v.layout.overlaps.length > 0);
console.log(`\n=== 存在问题的视图: ${bad.length}/${report.views.length} ===`);
