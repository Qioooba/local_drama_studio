// 控件级全面审查：枚举所有视图的 input/select/textarea/button，
// 检测溢出/截断/重叠/label/占位/disabled/可切换性/字号等，并输出 JSON 报告。
// 用法: node scripts/controls-audit.mjs [--interact]
import { chromium } from "playwright";
import { writeFileSync } from "node:fs";

const BASE = "http://127.0.0.1:5173";
const VIEWS = ["overview", "projects", "canvas", "reviews", "jobs", "profiles", "generation", "diagnostics"];
const INTERACT = process.argv.includes("--interact");
const EXEC = process.env.PLAYWRIGHT_CHROMIUM_PATH || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

function rgbToLum(rgb) {
  const m = rgb.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (!m) return 1;
  const f = (c) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4); };
  return 0.2126 * f(+m[1]) + 0.7152 * f(+m[2]) + 0.0722 * f(+m[3]);
}
function contrast(a, b) {
  const la = rgbToLum(a), lb = rgbToLum(b);
  const [hi, lo] = la > lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

const browser = await chromium.launch({ executablePath: EXEC, headless: true });
const report = { generatedAt: new Date().toISOString(), views: [] };

for (const view of VIEWS) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e).slice(0, 200)));
  await page.goto(`${BASE}/?view=${view}`, { waitUntil: "domcontentloaded", timeout: 40000 });
  await page.waitForTimeout(4500);

  const findings = await page.evaluate(() => {
    const rgbToLum = (rgb) => {
      const m = rgb.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
      if (!m) return 1;
      const f = (c) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4); };
      return 0.2126 * f(+m[1]) + 0.7152 * f(+m[2]) + 0.0722 * f(+m[3]);
    };
    const contrast = (a, b) => {
      const la = rgbToLum(a), lb = rgbToLum(b);
      const [hi, lo] = la > lb ? [la, lb] : [lb, la];
      return (hi + 0.05) / (lo + 0.05);
    };
    const out = [];
    const push = (el, kind, problem, detail) => {
      const r = el.getBoundingClientRect();
      out.push({ kind, problem, detail, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) });
    };
    const visible = (el) => {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const inClosedDetails = el.closest("details") && !(el.closest("details")?.open);
      return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none" && !inClosedDetails && cs.contentVisibility !== "hidden";
    };

    document.querySelectorAll("input:not([type='hidden']), select, textarea, button").forEach((el) => {
      if (!visible(el)) return;
      const tag = el.tagName.toLowerCase();
      const type = (el.type || "").toLowerCase();
      const cls = (el.className && typeof el.className === "string" ? el.className : "").toString().slice(0, 50);
      const label = el.getAttribute("aria-label") || el.getAttribute("title") || (el.labels && el.labels[0]?.textContent?.trim().slice(0, 30)) || "";
      const isCheck = type === "checkbox" || type === "radio";
      if (isCheck) return; // 勾选框单独检查

      // 1) 文本溢出（scrollWidth > clientWidth）：输入/下拉/按钮文本被裁剪
      if (el.scrollWidth > el.clientWidth + 2) {
        const text = (el.textContent || el.value || "").trim().slice(0, 40);
        push(el, tag, "overflow_text_clipped", `${label || text} (${el.scrollWidth}/${el.clientWidth})`);
      }

      // 2) 超出父容器右缘
      const r = el.getBoundingClientRect();
      const parent = el.parentElement;
      if (parent && parent !== document.body) {
        const pr = parent.getBoundingClientRect();
        if (pr.width > 0 && r.right > pr.right + 4 && r.width > 40) {
          push(el, tag, "overflow_parent", `${label || el.textContent?.trim().slice(0, 20)} right=${Math.round(r.right)} parentRight=${Math.round(pr.right)}`);
        }
      }

      // 3) 输入/下拉无 label 关联
      if ((tag === "input" || tag === "select" || tag === "textarea") && !isCheck) {
        const hasLabel = Boolean(el.getAttribute("aria-label")) || Boolean(el.getAttribute("aria-labelledby")) ||
          Boolean(el.labels && el.labels.length) || Boolean(el.closest("label"));
        if (!hasLabel) push(el, tag, "missing_label", `${type || tag} ${cls}`);
      }

      // 4) 空输入无 placeholder 无可见 label
      if (tag === "input" && !isCheck && !el.value && !el.getAttribute("placeholder") && !el.closest("label") && !el.getAttribute("aria-label")) {
        push(el, tag, "empty_no_placeholder", cls);
      }

      // 5) disabled 控件（记录，仅 select/input 数据位）
      if (el.disabled && (tag === "select" || tag === "input")) {
        push(el, tag, "disabled", `${label || cls} (${type || "select"})`);
      }

      // 6) 字号过小（< 11px）
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if (fs > 0 && fs < 11) push(el, tag, "tiny_font", `${fs}px ${label || cls}`);

      // 7) select 选项不足
      if (tag === "select") {
        const opts = Array.from(el.options).filter((o) => o.value !== "");
        if (opts.length === 0 && !el.disabled) push(el, tag, "select_no_options", label || cls);
        if (el.options.length > 1) {
          const sel = el.options[el.selectedIndex];
          if (sel && sel.textContent && sel.scrollWidth > sel.clientWidth + 2) {
            push(el, tag, "select_option_clipped", `${sel.textContent.trim().slice(0, 30)}`);
          }
        }
      }

      // 8) textarea 高度过矮（< 60px）且有多行内容
      if (tag === "textarea" && el.clientHeight < 60 && (el.value || el.placeholder)) {
        push(el, tag, "textarea_short", `${el.clientHeight}px ${label || cls}`);
      }

      // 9) 按钮空文本
      if (tag === "button" && !(el.textContent || "").trim() && !el.getAttribute("aria-label") && !el.querySelector("svg")) {
        push(el, tag, "button_empty", cls);
      }

      // 10) placeholder 对比度（读取 ::placeholder 伪元素颜色）
      if (tag === "input" || tag === "textarea") {
        const ph = el.getAttribute("placeholder");
        if (ph) {
          const cs = getComputedStyle(el);
          const phColor = getComputedStyle(el, "::placeholder").color || cs.color;
          const bg = el.offsetParent ? getComputedStyle(el.closest(".panel, section, div") || el).backgroundColor : "rgb(255,255,255)";
          const c = contrast(phColor, bg);
          if (c < 3) push(el, tag, "placeholder_low_contrast", `${ph.slice(0, 20)} (${c.toFixed(1)}:1)`);
        }
      }
    });

    // 11) 相邻控件重叠检测（同父容器内的 input/select/textarea 两两）
    const fields = Array.from(document.querySelectorAll("input:not([type='hidden']):not([type='checkbox']):not([type='radio']), select, textarea")).filter(visible);
    for (let i = 0; i < fields.length; i += 1) {
      for (let j = i + 1; j < fields.length; j += 1) {
        const a = fields[i].getBoundingClientRect(), b = fields[j].getBoundingClientRect();
        if (a.width === 0 || b.width === 0) continue;
        const ix = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
        const iy = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
        if (ix * iy > 120 && a.left !== b.left) {
          const la = fields[i].getAttribute("aria-label") || fields[i].value || fields[i].placeholder || fields[i].className;
          const lb = fields[j].getAttribute("aria-label") || fields[j].value || fields[j].placeholder || fields[j].className;
          out.push({ kind: "field", problem: "fields_overlap", detail: `${String(la).slice(0, 20)} × ${String(lb).slice(0, 20)} area=${Math.round(ix * iy)}`, x: Math.round(a.x), y: Math.round(a.y), w: 0, h: 0 });
        }
      }
    }
    return out;
  });

  const interactFindings = [];
  if (INTERACT) {
    // select 切换 + textarea 输入探测
    const selects = await page.locator("select:not([disabled])").count();
    const textareas = await page.locator("textarea").count();
    let selectTried = 0, textareaTried = 0;
    const select = page.locator("select:not([disabled])");
    for (let i = 0; i < await select.count() && i < 30; i += 1) {
      const el = select.nth(i);
      try {
        const opts = await el.locator("option").count();
        if (opts > 1) {
          await el.selectOption({ index: 1 });
          selectTried += 1;
        }
      } catch (e) { interactFindings.push({ kind: "select", problem: "select_switch_failed", detail: String(e).slice(0, 120) }); }
    }
    const ta = page.locator("textarea");
    for (let i = 0; i < await ta.count() && i < 20; i += 1) {
      const el = ta.nth(i);
      try {
        if (!(await el.isDisabled())) {
          await el.fill("ui-复查测试文本");
          textareaTried += 1;
        }
      } catch (e) { interactFindings.push({ kind: "textarea", problem: "textarea_fill_failed", detail: String(e).slice(0, 120) }); }
    }
    interactFindings.push({ kind: "summary", problem: "interaction", detail: `select切换成功=${selectTried}/${Math.min(await select.count(), 30)} textarea输入成功=${textareaTried}/${Math.min(await ta.count(), 20)}` });
  }

  report.views.push({ view, pageErrors, findings, interactFindings });
  console.log(`${view.padEnd(12)} findings=${findings.length} pageErrors=${pageErrors.length}`);
  await page.close();
}
await browser.close();

// 分组汇总
const byProblem = {};
for (const v of report.views) {
  for (const f of v.findings) {
    const key = `${f.kind}|${f.problem}`;
    byProblem[key] = (byProblem[key] || 0) + 1;
  }
}
console.log("=== 问题类型汇总 ===");
for (const [k, n] of Object.entries(byProblem).sort((a, b) => b[1] - a[1])) {
  console.log(`  ${n.toString().padStart(3)}  ${k}`);
}
writeFileSync("test-results/controls-audit.json", JSON.stringify(report, null, 2));
console.log("report: test-results/controls-audit.json");
