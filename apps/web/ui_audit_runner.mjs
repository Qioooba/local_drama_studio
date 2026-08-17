/**
 * Full UI audit: screenshot every view/module/scroll slice, click every button once,
 * collect console/page/layout issues into issues.json + issues.md
 */
import { chromium } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = __dirname;
const SHOTS = path.join(ROOT, "screenshots");
const MODULES = path.join(ROOT, "modules");
const CLICKS = path.join(ROOT, "clicks");
const BASE = process.env.UI_AUDIT_BASE || "http://127.0.0.1:5173";
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";

const VIEWS = [
  { id: "overview", label: "概览" },
  { id: "projects", label: "分集生产" },
  { id: "generation", label: "AI生成工作台" },
  { id: "reviews", label: "审核收件箱" },
  { id: "canvas", label: "业务画布" },
  { id: "profiles", label: "模型与能力" },
  { id: "jobs", label: "任务与机器" },
  { id: "diagnostics", label: "诊断中心" },
];

const issues = [];
const clickLog = [];
const screenshotIndex = [];

function addIssue(issue) {
  issues.push({ id: `ISSUE-${String(issues.length + 1).padStart(3, "0")}`, ...issue, at: new Date().toISOString() });
}

function slug(s) {
  return String(s || "unknown")
    .replace(/[^\w\u4e00-\u9fff\-]+/g, "_")
    .replace(/_+/g, "_")
    .slice(0, 80);
}

async function ensureDirs() {
  for (const d of [ROOT, SHOTS, MODULES, CLICKS]) await fs.mkdir(d, { recursive: true });
}

async function launchBrowser() {
  const opts = {
    headless: true,
    args: ["--disable-dev-shm-usage"],
  };
  try {
    await fs.access(EDGE);
    opts.executablePath = EDGE;
  } catch {
    /* use bundled chromium */
  }
  return chromium.launch(opts);
}

async function preparePage(context, viewId) {
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  const consoleErrors = [];
  const pageErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  page.on("dialog", async (dialog) => {
    addIssue({
      severity: "medium",
      category: "dialog",
      view: viewId,
      title: `Unexpected dialog: ${dialog.type()}`,
      detail: dialog.message(),
    });
    await dialog.dismiss().catch(() => {});
  });
  page._audit = { consoleErrors, pageErrors };
  return page;
}

async function gotoView(page, viewId) {
  const url = `${BASE}/?view=${viewId}`;
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);
  // wait for shell
  await page.locator("main.shell, .shell, #workspace-content").first().waitFor({ state: "visible", timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(800);
}

async function captureFullAndScroll(page, viewId) {
  const content = page.locator("#workspace-content, .content").first();
  const fullPath = path.join(SHOTS, `${viewId}__full.png`);
  await page.screenshot({ path: fullPath, fullPage: true });
  screenshotIndex.push({ view: viewId, kind: "full", path: fullPath });

  // viewport top
  const topPath = path.join(SHOTS, `${viewId}__viewport_top.png`);
  await page.screenshot({ path: topPath, fullPage: false });
  screenshotIndex.push({ view: viewId, kind: "viewport_top", path: topPath });

  // scroll slices inside content
  const scrollInfo = await page.evaluate(() => {
    const el = document.querySelector("#workspace-content") || document.querySelector(".content") || document.documentElement;
    return {
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
      canScroll: el.scrollHeight > el.clientHeight + 40,
      tag: el.tagName,
      id: el.id || "",
      className: String(el.className || ""),
    };
  });

  if (!scrollInfo.canScroll) {
    // maybe body scrolls
    const bodyInfo = await page.evaluate(() => ({
      scrollHeight: document.documentElement.scrollHeight,
      clientHeight: window.innerHeight,
    }));
    if (bodyInfo.scrollHeight > bodyInfo.clientHeight + 40) {
      const steps = Math.min(12, Math.ceil(bodyInfo.scrollHeight / Math.max(400, bodyInfo.clientHeight * 0.7)));
      for (let i = 0; i < steps; i++) {
        const y = Math.floor((i / Math.max(1, steps - 1)) * Math.max(0, bodyInfo.scrollHeight - bodyInfo.clientHeight));
        await page.evaluate((yy) => window.scrollTo(0, yy), y);
        await page.waitForTimeout(250);
        const p = path.join(SHOTS, `${viewId}__scroll_${String(i).padStart(2, "0")}.png`);
        await page.screenshot({ path: p, fullPage: false });
        screenshotIndex.push({ view: viewId, kind: "scroll", index: i, path: p });
      }
      await page.evaluate(() => window.scrollTo(0, 0));
    }
  } else {
    const steps = Math.min(16, Math.ceil(scrollInfo.scrollHeight / Math.max(400, scrollInfo.clientHeight * 0.65)));
    for (let i = 0; i < steps; i++) {
      const y = Math.floor((i / Math.max(1, steps - 1)) * Math.max(0, scrollInfo.scrollHeight - scrollInfo.clientHeight));
      await page.evaluate((yy) => {
        const el = document.querySelector("#workspace-content") || document.querySelector(".content");
        if (el) el.scrollTop = yy;
      }, y);
      await page.waitForTimeout(250);
      const p = path.join(SHOTS, `${viewId}__scroll_${String(i).padStart(2, "0")}.png`);
      await page.screenshot({ path: p, fullPage: false });
      screenshotIndex.push({ view: viewId, kind: "scroll", index: i, path: p });
    }
    await page.evaluate(() => {
      const el = document.querySelector("#workspace-content") || document.querySelector(".content");
      if (el) el.scrollTop = 0;
    });
  }

  // module panels
  const modules = await page.evaluate(() => {
    const nodes = [
      ...document.querySelectorAll(
        "section.panel, .panel, .status-card, .card-grid > *, [class*='Panel'], form, .workspace-error, .project-filters, .system-strip, .production-summary, .hero"
      ),
    ];
    return nodes.slice(0, 80).map((el, idx) => {
      const r = el.getBoundingClientRect();
      const title =
        el.querySelector("h2,h3,h4,.eyebrow,.panel-heading h3,.status-label")?.textContent?.trim() ||
        el.getAttribute("aria-label") ||
        el.className?.toString?.().slice(0, 40) ||
        `module_${idx}`;
      return {
        idx,
        title: title.slice(0, 60),
        tag: el.tagName,
        className: String(el.className || "").slice(0, 80),
        x: Math.round(r.x),
        y: Math.round(r.y),
        w: Math.round(r.width),
        h: Math.round(r.height),
        visible: r.width > 0 && r.height > 0,
      };
    });
  });

  for (const mod of modules) {
    if (!mod.visible || mod.w < 20 || mod.h < 20) continue;
    const safe = slug(`${viewId}__mod_${String(mod.idx).padStart(2, "0")}_${mod.title}`);
    const p = path.join(MODULES, `${safe}.png`);
    try {
      const locator = page.locator("section.panel, .panel, .status-card, .card-grid > *, form, .workspace-error, .project-filters, .system-strip, .production-summary, .hero").nth(mod.idx);
      await locator.scrollIntoViewIfNeeded().catch(() => {});
      await locator.screenshot({ path: p, timeout: 5000 }).catch(async () => {
        // clip fallback
        await page.screenshot({
          path: p,
          clip: {
            x: Math.max(0, mod.x),
            y: Math.max(0, mod.y),
            width: Math.min(mod.w, 1800),
            height: Math.min(mod.h, 1400),
          },
        });
      });
      screenshotIndex.push({ view: viewId, kind: "module", title: mod.title, path: p });
    } catch (err) {
      addIssue({
        severity: "low",
        category: "screenshot",
        view: viewId,
        title: `Module screenshot failed: ${mod.title}`,
        detail: String(err),
      });
    }
  }

  return { scrollInfo, moduleCount: modules.length };
}

async function detectLayoutProblems(page, viewId) {
  const findings = await page.evaluate(() => {
    const out = [];
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    const all = [...document.querySelectorAll("button, a, input, select, textarea, label, .panel, .status-card, .nav-item, .shot-row")];
    for (const el of all) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      const style = getComputedStyle(el);
      if (style.visibility === "hidden" || style.display === "none") continue;

      // overflow viewport horizontally
      if (r.right > vw + 2) {
        out.push({
          type: "overflow_x",
          text: (el.innerText || el.getAttribute("aria-label") || el.tagName).slice(0, 80),
          right: Math.round(r.right),
          vw,
          className: String(el.className || "").slice(0, 60),
        });
      }

      // text overflow / clipped
      if ((el.scrollWidth > el.clientWidth + 4 || el.scrollHeight > el.clientHeight + 4) && ["BUTTON", "A", "LABEL", "P", "SPAN", "H1", "H2", "H3"].includes(el.tagName)) {
        out.push({
          type: "text_clip",
          text: (el.innerText || "").slice(0, 80),
          scrollW: el.scrollWidth,
          clientW: el.clientWidth,
          className: String(el.className || "").slice(0, 60),
        });
      }

      // zero opacity interactive
      if (["BUTTON", "A", "INPUT"].includes(el.tagName) && Number(style.opacity) === 0) {
        out.push({ type: "invisible_interactive", text: (el.innerText || el.getAttribute("aria-label") || "").slice(0, 80) });
      }

      // tiny click target
      if (["BUTTON", "A"].includes(el.tagName) && r.width * r.height > 0 && (r.width < 20 || r.height < 20)) {
        out.push({
          type: "tiny_hit_target",
          text: (el.innerText || el.getAttribute("aria-label") || "").slice(0, 80),
          w: Math.round(r.width),
          h: Math.round(r.height),
        });
      }
    }

    // overlapping buttons
    const buttons = [...document.querySelectorAll("button")].filter((b) => {
      const r = b.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
    for (let i = 0; i < buttons.length; i++) {
      const a = buttons[i].getBoundingClientRect();
      for (let j = i + 1; j < Math.min(buttons.length, i + 25); j++) {
        const b = buttons[j].getBoundingClientRect();
        const overlap = !(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top);
        if (!overlap) continue;
        const interW = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const interH = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (interW > 8 && interH > 8) {
          out.push({
            type: "overlap_buttons",
            a: (buttons[i].innerText || "").slice(0, 40),
            b: (buttons[j].innerText || "").slice(0, 40),
            interW: Math.round(interW),
            interH: Math.round(interH),
          });
        }
      }
    }

    // misaligned sibling rows (large x variance among same-class children)
    document.querySelectorAll(".shot-row, .card-grid > *, .status-card, .nav-item").forEach((el) => {
      const parent = el.parentElement;
      if (!parent) return;
      const kids = [...parent.children].filter((c) => c.className === el.className);
      if (kids.length < 2) return;
      const xs = kids.map((c) => Math.round(c.getBoundingClientRect().left));
      const min = Math.min(...xs);
      const max = Math.max(...xs);
      if (max - min > 24 && el.classList.contains("nav-item") === false) {
        // only flag once per parent via first child
        if (el === kids[0]) {
          out.push({ type: "sibling_misalign", className: String(el.className).slice(0, 40), delta: max - min, count: kids.length });
        }
      }
    });

    // empty clickable with no label
    for (const el of document.querySelectorAll("button")) {
      const label = (el.innerText || el.getAttribute("aria-label") || el.title || "").trim();
      if (!label && !el.querySelector("svg,img")) {
        out.push({ type: "empty_button_label", className: String(el.className || "").slice(0, 60) });
      }
    }

    // inputs without labels
    for (const input of document.querySelectorAll("input, select, textarea")) {
      const id = input.id;
      const hasLabel = (id && document.querySelector(`label[for="${CSS.escape(id)}"]`)) || input.closest("label") || input.getAttribute("aria-label");
      if (!hasLabel) {
        out.push({
          type: "input_missing_label",
          name: input.name || input.placeholder || input.type || input.tagName,
          className: String(input.className || "").slice(0, 40),
        });
      }
    }

    void vh;
    return out.slice(0, 200);
  });

  for (const f of findings) {
    addIssue({
      severity: f.type === "overlap_buttons" || f.type === "overflow_x" ? "high" : "medium",
      category: "layout",
      view: viewId,
      title: `Layout: ${f.type}`,
      detail: JSON.stringify(f),
    });
  }
  return findings.length;
}

async function listClickableMeta(page) {
  return page.evaluate(() => {
    const nodes = [...document.querySelectorAll("button, [role='button'], a.button, input[type='button'], input[type='submit']")];
    return nodes.map((el, idx) => {
      const r = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      return {
        idx,
        tag: el.tagName,
        text: (el.innerText || el.value || el.getAttribute("aria-label") || el.title || "").trim().slice(0, 80),
        disabled: Boolean(el.disabled) || el.getAttribute("aria-disabled") === "true",
        visible: r.width > 0 && r.height > 0 && style.visibility !== "hidden" && style.display !== "none",
        className: String(el.className || "").slice(0, 80),
        x: Math.round(r.x),
        y: Math.round(r.y),
        w: Math.round(r.w || r.width),
        h: Math.round(r.h || r.height),
      };
    });
  });
}

async function clickAllButtons(page, viewId) {
  // re-goto view for clean state
  await gotoView(page, viewId);
  const beforeUrl = page.url();
  let metas = await listClickableMeta(page);
  const visited = new Set();

  // Limit to avoid infinite loops if DOM churns; re-scan a few passes
  for (let pass = 0; pass < 3; pass++) {
    metas = await listClickableMeta(page);
    for (const meta of metas) {
      const key = `${meta.text}|${meta.className}|${meta.idx}`;
      if (visited.has(key)) continue;
      if (!meta.visible) continue;
      visited.add(key);

      const entry = {
        view: viewId,
        pass,
        text: meta.text || `(unnamed ${meta.tag})`,
        disabled: meta.disabled,
        className: meta.className,
        result: "pending",
      };

      if (meta.disabled) {
        entry.result = "skipped_disabled";
        clickLog.push(entry);
        // disabled button that looks primary can be a UX issue if always disabled without reason
        if (!meta.text.includes("未选择") && meta.text) {
          // note only once later in aggregation
        }
        continue;
      }

      // skip pure nav sidebar items after first pass of their view — still click once
      const locator = page.locator("button, [role='button'], a.button, input[type='button'], input[type='submit']").nth(meta.idx);
      const consoleBefore = page._audit.consoleErrors.length;
      const pageErrBefore = page._audit.pageErrors.length;
      const urlBefore = page.url();

      try {
        await locator.scrollIntoViewIfNeeded({ timeout: 3000 }).catch(() => {});
        const box = await locator.boundingBox();
        if (!box) {
          entry.result = "not_in_viewport_or_detached";
          addIssue({
            severity: "high",
            category: "click",
            view: viewId,
            title: `Button not clickable (no box): ${entry.text}`,
            detail: meta.className,
          });
          clickLog.push(entry);
          continue;
        }

        // screenshot around button before click
        const shotName = slug(`${viewId}__click_${String(clickLog.length).padStart(3, "0")}_${entry.text}`);
        const beforeShot = path.join(CLICKS, `${shotName}__before.png`);
        await page
          .screenshot({
            path: beforeShot,
            clip: {
              x: Math.max(0, box.x - 20),
              y: Math.max(0, box.y - 20),
              width: Math.min(box.width + 40, 900),
              height: Math.min(box.height + 40, 400),
            },
          })
          .catch(() => {});

        const clickPromise = locator.click({ timeout: 4000, trial: false });
        await Promise.race([
          clickPromise,
          new Promise((_, reject) => setTimeout(() => reject(new Error("click_timeout")), 5000)),
        ]);

        await page.waitForTimeout(450);

        const consoleAfter = page._audit.consoleErrors.slice(consoleBefore);
        const pageErrAfter = page._audit.pageErrors.slice(pageErrBefore);
        const urlAfter = page.url();

        // dismiss overlays if any close buttons appear
        const closeBtn = page.locator("button:has-text('关闭'), button:has-text('取消'), button:has-text('Cancel'), [aria-label='Close'], .modal button.secondary").first();
        if (await closeBtn.isVisible().catch(() => false)) {
          const modalShot = path.join(CLICKS, `${shotName}__modal.png`);
          await page.screenshot({ path: modalShot, fullPage: false }).catch(() => {});
          await closeBtn.click({ timeout: 2000 }).catch(() => {});
          entry.modal = true;
        }

        if (consoleAfter.length || pageErrAfter.length) {
          entry.result = "error_on_click";
          addIssue({
            severity: "high",
            category: "click",
            view: viewId,
            title: `Click caused JS error: ${entry.text}`,
            detail: JSON.stringify({ consoleAfter, pageErrAfter }),
            screenshot: beforeShot,
          });
        } else if (urlAfter !== urlBefore && !urlAfter.includes(`view=${viewId}`) && !meta.className.includes("nav-item")) {
          entry.result = "navigated_away";
          // restore
          await gotoView(page, viewId);
        } else {
          entry.result = "clicked_ok";
        }

        // detect pointer-events none / covered
        const covered = await page.evaluate(
          ({ x, y }) => {
            const top = document.elementFromPoint(x, y);
            return top ? { tag: top.tagName, text: (top.innerText || "").slice(0, 40), className: String(top.className || "").slice(0, 60) } : null;
          },
          { x: box.x + box.width / 2, y: box.y + box.height / 2 }
        );
        entry.topElement = covered;
      } catch (err) {
        entry.result = "click_failed";
        entry.error = String(err);
        addIssue({
          severity: "high",
          category: "click",
          view: viewId,
          title: `Button click failed: ${entry.text}`,
          detail: String(err),
        });
        // restore view if broken
        if (page.url() !== beforeUrl && !page.url().includes(`view=${viewId}`)) {
          await gotoView(page, viewId);
        }
      }

      clickLog.push(entry);
    }
    // restore view between passes
    await gotoView(page, viewId);
  }

  return { clicked: clickLog.filter((c) => c.view === viewId).length };
}

async function alsoScreenshotInputs(page, viewId) {
  const inputs = page.locator("input:not([type='hidden']), textarea, select");
  const count = await inputs.count();
  for (let i = 0; i < Math.min(count, 60); i++) {
    const el = inputs.nth(i);
    if (!(await el.isVisible().catch(() => false))) continue;
    await el.scrollIntoViewIfNeeded().catch(() => {});
    const label = (await el.getAttribute("aria-label").catch(() => null)) || (await el.getAttribute("placeholder").catch(() => null)) || `input_${i}`;
    const p = path.join(MODULES, slug(`${viewId}__input_${String(i).padStart(2, "0")}_${label}`) + ".png");
    await el.screenshot({ path: p, timeout: 3000 }).catch(() => {});
    screenshotIndex.push({ view: viewId, kind: "input", label, path: p });
  }
}

async function auditView(browser, view) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
  const page = await preparePage(context, view.id);
  const summary = { view: view.id, label: view.label, ok: true };

  try {
    await gotoView(page, view.id);

    // console errors on load
    await page.waitForTimeout(500);
    if (page._audit.consoleErrors.length) {
      addIssue({
        severity: "high",
        category: "console",
        view: view.id,
        title: "Console errors on view load",
        detail: page._audit.consoleErrors.slice(0, 20).join("\n"),
      });
    }
    if (page._audit.pageErrors.length) {
      addIssue({
        severity: "critical",
        category: "pageerror",
        view: view.id,
        title: "Page errors on view load",
        detail: page._audit.pageErrors.slice(0, 20).join("\n"),
      });
    }

    // empty / error workspace
    const errPanel = page.locator(".workspace-error");
    if (await errPanel.isVisible().catch(() => false)) {
      const text = await errPanel.innerText();
      addIssue({
        severity: "high",
        category: "data",
        view: view.id,
        title: "Workspace error panel visible",
        detail: text.slice(0, 500),
      });
      await errPanel.screenshot({ path: path.join(SHOTS, `${view.id}__workspace_error.png`) }).catch(() => {});
    }

    const shotMeta = await captureFullAndScroll(page, view.id);
    summary.modules = shotMeta.moduleCount;
    await alsoScreenshotInputs(page, view.id);
    summary.layoutFindings = await detectLayoutProblems(page, view.id);
    const clickMeta = await clickAllButtons(page, view.id);
    summary.clicks = clickMeta.clicked;

    // final after-interaction full shot
    await gotoView(page, view.id);
    await page.screenshot({ path: path.join(SHOTS, `${view.id}__after_clicks_full.png`), fullPage: true });
  } catch (err) {
    summary.ok = false;
    summary.error = String(err);
    addIssue({
      severity: "critical",
      category: "audit",
      view: view.id,
      title: `View audit crashed: ${view.label}`,
      detail: String(err),
    });
    await page.screenshot({ path: path.join(SHOTS, `${view.id}__CRASH.png`), fullPage: true }).catch(() => {});
  } finally {
    await context.close();
  }
  return summary;
}

function dedupeIssues(list) {
  const seen = new Set();
  const out = [];
  for (const issue of list) {
    const key = `${issue.category}|${issue.view}|${issue.title}|${issue.detail}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(issue);
  }
  // renumber
  return out.map((issue, i) => ({ ...issue, id: `ISSUE-${String(i + 1).padStart(3, "0")}` }));
}

async function writeReports(summaries) {
  const unique = dedupeIssues(issues);
  const payload = {
    generatedAt: new Date().toISOString(),
    base: BASE,
    summaries,
    issueCount: unique.length,
    issues: unique,
    clickLog,
    screenshotIndex,
  };
  await fs.writeFile(path.join(ROOT, "issues.json"), JSON.stringify(payload, null, 2), "utf8");

  const bySeverity = {};
  for (const i of unique) bySeverity[i.severity] = (bySeverity[i.severity] || 0) + 1;
  const byView = {};
  for (const i of unique) byView[i.view || "global"] = (byView[i.view || "global"] || 0) + 1;

  const failedClicks = clickLog.filter((c) => ["click_failed", "error_on_click", "not_in_viewport_or_detached"].includes(c.result));
  const disabledClicks = clickLog.filter((c) => c.result === "skipped_disabled");

  let md = `# LocalDramaStudio UI 审计问题清单\n\n`;
  md += `- 生成时间: ${payload.generatedAt}\n`;
  md += `- 目标: ${BASE}\n`;
  md += `- 截图目录: \`test-ui-audit/screenshots\`, \`modules\`, \`clicks\`\n`;
  md += `- 问题数: **${unique.length}**\n`;
  md += `- 点击尝试: ${clickLog.length}（失败 ${failedClicks.length}，禁用跳过 ${disabledClicks.length}）\n`;
  md += `- 截图数量: ${screenshotIndex.length}\n\n`;
  md += `## 严重级别统计\n\n`;
  for (const [k, v] of Object.entries(bySeverity).sort()) md += `- ${k}: ${v}\n`;
  md += `\n## 按页面统计\n\n`;
  for (const [k, v] of Object.entries(byView).sort()) md += `- ${k}: ${v}\n`;
  md += `\n## 各视图审计摘要\n\n`;
  for (const s of summaries) {
    md += `- **${s.label}** (\`${s.view}\`): ${s.ok ? "完成" : "失败"} · modules≈${s.modules ?? "?"} · layoutFindings=${s.layoutFindings ?? "?"} · clicks=${s.clicks ?? "?"}${s.error ? ` · error=${s.error}` : ""}\n`;
  }

  md += `\n## 点击失败明细\n\n`;
  if (!failedClicks.length) md += `_无_\n`;
  for (const c of failedClicks) {
    md += `- [${c.view}] **${c.text}** → \`${c.result}\`${c.error ? ` · ${c.error}` : ""}\n`;
  }

  md += `\n## 问题详情（按严重级别）\n\n`;
  const order = { critical: 0, high: 1, medium: 2, low: 3 };
  const sorted = [...unique].sort((a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9));
  for (const issue of sorted) {
    md += `### ${issue.id} · ${issue.severity} · ${issue.category} · ${issue.view || "-"}\n\n`;
    md += `**${issue.title}**\n\n`;
    md += `\`\`\`\n${issue.detail || ""}\n\`\`\`\n\n`;
    if (issue.screenshot) md += `截图: \`${path.relative(ROOT, issue.screenshot).replace(/\\/g, "/")}\`\n\n`;
  }

  md += `\n## 截图索引（节选）\n\n`;
  for (const s of screenshotIndex.filter((x) => x.kind === "full" || x.kind === "viewport_top")) {
    md += `- [${s.view}/${s.kind}] \`${path.relative(ROOT, s.path).replace(/\\/g, "/")}\`\n`;
  }

  md += `\n## 后续修复建议顺序\n\n`;
  md += `1. 先修 critical/high：页面崩溃、点击报错、按钮重叠、横向溢出\n`;
  md += `2. 再修 medium：错位、文本裁切、缺 label\n`;
  md += `3. 最后处理 low：截图失败等审计噪声\n`;

  await fs.writeFile(path.join(ROOT, "ISSUES.md"), md, "utf8");
  await fs.writeFile(path.join(ROOT, "click-log.json"), JSON.stringify(clickLog, null, 2), "utf8");
  return unique.length;
}

async function main() {
  await ensureDirs();
  const browser = await launchBrowser();
  console.log(`[ui-audit] base=${BASE}`);
  // parallel: 2 at a time to avoid hammering local API
  const summaries = [];
  const queue = [...VIEWS];
  const concurrency = 2;
  async function worker() {
    while (queue.length) {
      const view = queue.shift();
      console.log(`[ui-audit] start ${view.id}`);
      const summary = await auditView(browser, view);
      summaries.push(summary);
      console.log(`[ui-audit] done ${view.id}`, summary);
    }
  }
  await Promise.all(Array.from({ length: concurrency }, () => worker()));
  await browser.close();
  summaries.sort((a, b) => VIEWS.findIndex((v) => v.id === a.view) - VIEWS.findIndex((v) => v.id === b.view));
  const count = await writeReports(summaries);
  console.log(`[ui-audit] wrote ISSUES.md with ${count} issues`);
}

main().catch(async (err) => {
  console.error(err);
  await fs.writeFile(path.join(ROOT, "FATAL.txt"), String(err?.stack || err), "utf8").catch(() => {});
  process.exit(1);
});
