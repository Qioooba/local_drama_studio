// 三个报告的修复状态抽样断言（代表性条目）：
// 每个断言对应一个或多个报告条目，全部通过才算无遗漏。
import { chromium } from "playwright";
const EXEC = process.env.PLAYWRIGHT_CHROMIUM_PATH || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const BASE = "http://127.0.0.1:5173";

const browser = await chromium.launch({ executablePath: EXEC, headless: true });
const results = [];
const check = (name, ok, detail) => { results.push({ name, ok: Boolean(ok), detail }); console.log(`${ok ? "PASS" : "FAIL"}  ${name}  ${detail}`); };

// 1. 8 视图可加载（ISSUE-031/038 critical）
for (const view of ["overview", "projects", "canvas", "reviews", "jobs", "profiles", "generation", "diagnostics"]) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));
  try {
    await page.goto(`${BASE}/?view=${view}`, { waitUntil: "domcontentloaded", timeout: 20000 });
    await page.waitForTimeout(2500);
    const hero = await page.locator(".hero h2").textContent().catch(() => "");
    check(`加载 ${view}`, hero.length > 0, hero.slice(0, 30) + (errs.length ? ` | pageerrors=${errs.length}` : ""));
  } catch (e) {
    check(`加载 ${view}`, false, String(e).slice(0, 100));
  }
  await page.close();
}

// 2. VIS-001 分视图 Hero：8 个视图标题互不相同
{
  const titles = {};
  for (const view of ["overview", "projects", "generation", "reviews", "canvas", "jobs", "profiles", "diagnostics"]) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.goto(`${BASE}/?view=${view}`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2000);
    titles[view] = (await page.locator(".hero h2").textContent().catch(() => "")).trim();
    await page.close();
  }
  const unique = new Set(Object.values(titles));
  check("VIS-001 分视图 Hero 文案", unique.size === 8, `${unique.size}/8 个视图标题唯一`);
}

// 3. FORM-N01 数字输入前导零
{
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(`${BASE}/?view=projects`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4500);
  const input = page.locator('input[aria-label$="时长"]').first();
  if (await input.count()) {
    await input.fill("");
    await input.type("12");
    const v = await input.inputValue();
    check("FORM-N01 时长输入无前导零", v === "12", `fill('')+type('12')=${JSON.stringify(v)}`);
  } else check("FORM-N01 时长输入", false, "未找到时长输入");
  await page.close();
}

// 4. FORM-S01 顶栏清空
{
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(`${BASE}/?view=overview`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3500);
  const sel = page.locator('select[aria-label="当前项目"]');
  const before = await sel.inputValue();
  await sel.selectOption("");
  await page.waitForTimeout(500);
  const after = await sel.inputValue();
  check("FORM-S01 顶栏项目可清空", before !== "" && after === "", `${before.slice(0, 8)}→${JSON.stringify(after)}`);
  await page.close();
}

// 5. ISSUE-013 无浏览器弹窗（已全部内联化）
{
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  let dialogs = 0;
  page.on("dialog", () => { dialogs += 1; });
  await page.goto(`${BASE}/?view=projects`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3000);
  // 触发交付审核说明按钮（若有交付）
  const btn = page.locator("button", { hasText: "记录人工批准" }).first();
  if (await btn.count()) {
    await btn.click().catch(() => {});
    await page.waitForTimeout(600);
  }
  check("ISSUE-013 无 window.prompt 弹窗", dialogs === 0, `弹窗数=${dialogs}`);
  await page.close();
}

// 6. b009-002/VISB-006-007 生成台 JSON 文本域不重叠
{
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(`${BASE}/?view=generation`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4500);
  const overlap = await page.evaluate(() => {
    const labels = Array.from(document.querySelectorAll(".generation-control-panel .field-grid label")).filter((el) => el.offsetWidth > 0);
    for (let i = 0; i < labels.length; i += 1) for (let j = i + 1; j < labels.length; j += 1) {
      const a = labels[i].getBoundingClientRect(), b = labels[j].getBoundingClientRect();
      if (Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)) > 40) return true;
    }
    return false;
  });
  check("b009-002 导演控制 JSON 无重叠", !overlap, "field-grid 标签互不重叠");
  await page.close();
}

// 7. VISM-04-001 顶栏不遮挡内容（scroll-padding 生效）
{
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(`${BASE}/?view=profiles`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3500);
  const sp = await page.evaluate(() => getComputedStyle(document.documentElement).scrollPaddingTop);
  check("VISM-04-001 scroll-padding-top", parseFloat(sp) >= 60, `scrollPaddingTop=${sp}`);
  await page.close();
}

// 8. 状态徽章统一（jobs 所有状态为 pill）
{
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(`${BASE}/?view=jobs`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3500);
  const stats = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll(".job-row"));
    const plain = rows.filter((r) => { const s = r.children[2]; return s && !s.classList.contains("status-pill") && !s.classList.contains("blocker-text"); }).length;
    return { rows: rows.length, nonPill: plain };
  });
  check("B8 jobs 状态徽章统一", stats.rows > 0 && stats.nonPill === 0, JSON.stringify(stats));
  await page.close();
}

// 9. 数字输入清空行为（DirectorShotEditor 强度 clamp）
{
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(`${BASE}/?view=generation`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4500);
  const intensity = page.locator('label:has-text("强度（0—1）") input').first();
  if (await intensity.count()) {
    await intensity.fill("12");
    const v = await intensity.inputValue();
    check("FORM-N01 强度 0—1 夹取", Number(v) <= 1, `输入 12 → ${v}`);
  } else check("FORM-N01 强度", false, "未找到强度输入");
  await page.close();
}

// 10. ISSUE-002 归档按钮无重叠（projects 资产卡）
{
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(`${BASE}/?view=projects`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4500);
  const over = await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll("button.secondary")).filter((b) => b.textContent?.trim() === "归档" && b.offsetWidth > 0);
    if (!btns.length) return null;
    const r = btns[0].getBoundingClientRect();
    // 与同卡片其它元素的水平重叠
    const card = btns[0].closest(".story-asset-card, article");
    if (!card) return false;
    const cr = card.getBoundingClientRect();
    return r.right > cr.right + 4 || r.left < cr.left - 4;
  });
  check("ISSUE-002 归档按钮在卡片内", over === false, over === null ? "无归档按钮" : "按钮未越界");
  await page.close();
}

// 11. 页面横向无溢出（所有视图）
for (const view of ["overview", "projects", "canvas", "reviews", "jobs", "profiles", "generation", "diagnostics"]) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(`${BASE}/?view=${view}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  const o = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
  check(`无横向溢出 ${view}`, o.sw <= o.cw + 2, `${o.sw}/${o.cw}`);
  await page.close();
}

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n=== 抽样断言: ${results.length - failed.length}/${results.length} 通过 ===`);
