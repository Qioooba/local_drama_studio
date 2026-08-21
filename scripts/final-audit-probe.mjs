// FINAL-AUDIT-REPORT 逐条实测：定位每条问题在当前代码的真实状态
import { chromium } from "playwright";
const EXEC = process.env.PLAYWRIGHT_CHROMIUM_PATH || "C:\\Users\\Qi\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe";
const BASE = "http://127.0.0.1:5173";
const browser = await chromium.launch({ executablePath: EXEC, headless: true });
const out = [];

const view = async (name) => {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(`${BASE}/?view=${name}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4200);
  return page;
};

// 1. 业务画布 DAG 连线（P0-6）
{
  const page = await view("canvas");
  const r = await page.evaluate(() => {
    const edges = document.querySelectorAll(".react-flow__edge").length;
    const nodes = document.querySelectorAll(".react-flow__node").length;
    const listEdges = document.querySelectorAll(".canvas-node-list button").length;
    return { flowEdges: edges, flowNodes: nodes, nodeListCount: listEdges };
  });
  out.push({ issue: "P0-6 业务画布 DAG 连线", status: r.flowEdges === 0 ? "无连线(数据/渲染)" : "有连线", detail: JSON.stringify(r) });
  await page.close();
}

// 2. generation 顶部中间空白框（P0-10）
{
  const page = await view("generation");
  const r = await page.evaluate(() => {
    const out = {};
    const slot = document.querySelector(".media-slot");
    if (slot) { const cs = getComputedStyle(slot); out.mediaSlot = { w: Math.round(slot.getBoundingClientRect().width), h: Math.round(slot.getBoundingClientRect().height), bg: cs.backgroundColor, text: slot.textContent.trim().slice(0, 24) }; }
    // 候选卡 poster
    const posters = Array.from(document.querySelectorAll(".candidate-poster")).slice(0, 4).map((p) => ({ w: Math.round(p.getBoundingClientRect().width), h: Math.round(p.getBoundingClientRect().height), hasImg: Boolean(p.querySelector("img")) }));
    out.posters = posters;
    return out;
  });
  out.push({ issue: "P0-10 生成工作台顶部空白框", status: r.mediaSlot ? "media-slot 存在" : "无", detail: JSON.stringify(r) });
  await page.close();
}

// 3. profiles 仅读标签溢出（P0-6 profiles）
{
  const page = await view("profiles");
  const r = await page.evaluate(() => {
    const pills = Array.from(document.querySelectorAll(".status-pill, .mode-badge")).filter((p) => /仅本地|LOCAL_ONLY|LOCAL/.test(p.textContent)).map((p) => {
      const r2 = p.getBoundingClientRect();
      const overflow = p.scrollWidth > p.clientWidth + 2;
      return { text: p.textContent.trim().slice(0, 16), cls: p.className.toString().slice(0, 24), w: Math.round(r2.width), overflow };
    });
    return pills;
  });
  out.push({ issue: "P0-6 profiles 仅读标签", status: r.some((x) => x.overflow) ? "有溢出" : "无溢出", detail: JSON.stringify(r.slice(0, 6)) });
  await page.close();
}

// 4. 垂直拆分检查（P0-1 projects/canvas）
{
  for (const v of ["projects", "canvas"]) {
    const page = await view(v);
    const r = await page.evaluate(() => {
      const hits = [];
      document.querySelectorAll(".card-grid > *, .configuration-card, .gate-checks li, .status-card, .panel strong, .panel span").forEach((el) => {
        const rect = el.getBoundingClientRect();
        if (rect.width < 70 && rect.width > 0 && (el.textContent || "").trim().length > 6) {
          hits.push({ text: el.textContent.trim().slice(0, 22), w: Math.round(rect.width), lines: el.getClientRects().length });
        }
      });
      return hits.slice(0, 8);
    });
    out.push({ issue: `P0-1 垂直拆分(${v})`, status: r.length ? "存在窄列" : "无", detail: JSON.stringify(r) });
    await page.close();
  }
}

// 5. DEGRADED 颜色（P0-2 诊断）
{
  const page = await view("diagnostics");
  const r = await page.evaluate(() => {
    const st = document.querySelector(".diagnostic-status strong");
    const blocked = Array.from(document.querySelectorAll(".diagnostic-row strong")).find((s) => s.textContent === "BLOCKED");
    return { degraded: st ? { text: st.textContent, color: getComputedStyle(st).color } : null, blocked: blocked ? getComputedStyle(blocked).color : null };
  });
  out.push({ issue: "P0-2 DEGRADED 颜色", status: r.degraded ? `DEGRADED=${r.degraded.color} BLOCKED=${r.blocked}` : "无", detail: JSON.stringify(r) });
  await page.close();
}

// 6. reviews 表格重叠 + 主标题截断（P0-3/P0-8）
{
  const page = await view("reviews");
  const r = await page.evaluate(() => {
    const out = {};
    const cells = Array.from(document.querySelectorAll(".formal-selection-panel .configuration-row span"));
    const overlaps = cells.map((c) => { const img = c.querySelector("img"), code = c.querySelector("code"); if (!img || !code) return false; return img.getBoundingClientRect().right > code.getBoundingClientRect().left; }).filter(Boolean).length;
    out.tableOverlap = overlaps;
    const h2 = document.querySelector(".hero h2");
    out.heroTitle = h2 ? { text: h2.textContent.trim().slice(0, 30), clipped: h2.scrollWidth > h2.clientWidth + 3 } : null;
    return out;
  });
  out.push({ issue: "P0-3/P0-8 reviews", status: r.tableOverlap ? "表格仍重叠" : "表格无重叠", detail: JSON.stringify(r) });
  await page.close();
}

// 7. JSON 等宽（P0-5 各页）
{
  for (const v of ["generation", "projects", "profiles"]) {
    const page = await view(v);
    const r = await page.evaluate(() => {
      const tas = Array.from(document.querySelectorAll("textarea")).filter((t) => t.offsetWidth > 0 && /JSON|json|cues|Timeline items|Tokens/i.test(t.closest("label")?.textContent || t.placeholder || ""));
      return tas.slice(0, 6).map((t) => getComputedStyle(t).fontFamily.split(",")[0]);
    });
    out.push({ issue: `P0-5 JSON 等宽(${v})`, status: r.length ? r.join("|") : "无JSON区", detail: "" });
    await page.close();
  }
}

// 8. generation 步骤条对比度 / BLOCKED 灰 / 红色边框（P1-1/P1-2/P2-9）
{
  const page = await view("generation");
  const r = await page.evaluate(() => {
    const out = {};
    const steps = Array.from(document.querySelectorAll(".workflow-step")).map((s) => ({ active: s.classList.contains("active"), color: getComputedStyle(s).color, bg: getComputedStyle(s).backgroundColor }));
    out.steps = steps.slice(0, 5);
    const blocked = Array.from(document.querySelectorAll(".capability-truth.blocked, .gate-checks li.blocked")).slice(0, 3).map((b) => ({ cls: b.className.toString().slice(0, 30), border: getComputedStyle(b).borderColor }));
    out.blocked = blocked;
    const modeSel = document.querySelector(".mode-card.selected");
    out.modeCard = modeSel ? { border: getComputedStyle(modeSel).borderColor, bg: getComputedStyle(modeSel).backgroundColor } : null;
    return out;
  });
  out.push({ issue: "P1-1/2/P2-9 generation 样式", status: "", detail: JSON.stringify(r).slice(0, 400) });
  await page.close();
}

// 9. 主标题字号与句号（P3-23/P2-18）
{
  const page = await view("overview");
  const r = await page.evaluate(() => {
    const h2 = document.querySelector(".hero h2");
    return h2 ? { text: h2.textContent.trim(), fs: getComputedStyle(h2).fontSize, endsWithPunct: /[。！？]$/.test(h2.textContent.trim()) } : null;
  });
  out.push({ issue: "P3-23/P2-18 主标题", status: "", detail: JSON.stringify(r) });
  await page.close();
}

// 10. 空占位黑矩形（P2-16）
{
  const page = await view("reviews");
  const r = await page.evaluate(() => {
    const placeholders = Array.from(document.querySelectorAll(".media-kind-placeholder, .review-preview, .candidate-poster, img")).filter((el) => {
      const rect = el.getBoundingClientRect();
      return rect.width > 60 && rect.height > 40 && (el.tagName === "IMG" ? (el.complete && el.naturalWidth === 0) : true);
    }).map((el) => ({ tag: el.tagName, cls: el.className.toString().slice(0, 30), w: Math.round(el.getBoundingClientRect().width), h: Math.round(el.getBoundingClientRect().height), bg: getComputedStyle(el).backgroundColor }));
    return placeholders.slice(0, 6);
  });
  out.push({ issue: "P2-16 空占位黑矩形", status: r.length ? `${r.length} 个` : "无", detail: JSON.stringify(r) });
  await page.close();
}

// 11. 音频播放器样式（P2-8 projects）
{
  const page = await view("projects");
  const r = await page.evaluate(() => {
    const audios = Array.from(document.querySelectorAll("audio")).map((a) => { const r2 = a.getBoundingClientRect(); return { w: Math.round(r2.width), h: Math.round(r2.height) }; });
    return audios;
  });
  out.push({ issue: "P2-8 音频播放器", status: r.length ? `${r.length} 个 audio 元素` : "无", detail: JSON.stringify(r) });
  await page.close();
}

// 12. 表格表头与数据对齐（P1-5 projects / P1-4 diagnostics）
{
  for (const v of ["projects", "diagnostics"]) {
    const page = await view(v);
    const r = await page.evaluate(() => {
      const issues = [];
      document.querySelectorAll(".configuration-table").forEach((table) => {
        const header = table.querySelector(".configuration-header");
        const rows = Array.from(table.querySelectorAll(".configuration-row:not(.configuration-header)")).slice(0, 3);
        if (!header || !rows.length) return;
        const hCells = Array.from(header.children).map((c) => Math.round(c.getBoundingClientRect().left));
        rows.forEach((row) => {
          const cells = Array.from(row.children).map((c) => Math.round(c.getBoundingClientRect().left));
          cells.forEach((left, i) => { if (hCells[i] !== undefined && Math.abs(left - hCells[i]) > 6) issues.push({ h: hCells[i], c: left, i }); });
        });
      });
      return issues.slice(0, 6);
    });
    out.push({ issue: `P1-5/P1-4 表格对齐(${v})`, status: r.length ? `${r.length} 处错位` : "对齐", detail: JSON.stringify(r) });
    await page.close();
  }
}

// 13. profiles 长标题截断 / 卡片高度（P1-3/P1-4）
{
  const page = await view("profiles");
  const r = await page.evaluate(() => {
    const out = {};
    const strong = document.querySelector(".profile-version-choice strong");
    out.titleCls = strong ? getComputedStyle(strong).whiteSpace : null;
    const choices = Array.from(document.querySelectorAll(".profile-version-choice")).slice(0, 6).map((c) => Math.round(c.getBoundingClientRect().height));
    out.cardHeights = [...new Set(choices)];
    return out;
  });
  out.push({ issue: "P1-3/P1-4 profiles", status: "", detail: JSON.stringify(r) });
  await page.close();
}

console.log("=== FINAL-AUDIT 逐条实测结果 ===");
for (const o of out) {
  console.log(`\n[${o.issue}] 状态: ${o.status}`);
  if (o.detail) console.log(`   ${o.detail.slice(0, 300)}`);
}
await browser.close();
