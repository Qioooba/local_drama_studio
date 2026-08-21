import fs from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";

const BASE_URL = "http://127.0.0.1:5173";
const VIEWS = ["overview", "projects", "canvas", "reviews", "jobs", "profiles", "generation", "diagnostics"];
const PARALLEL_WORKERS = 2;

const OUTPUT_ROOT = path.resolve(process.cwd(), "apps/web/screenshots/codex-ui-audit");
const RUN_TS = new Date().toISOString().replace(/[:.]/g, "-");
const RUN_DIR = path.join(OUTPUT_ROOT, RUN_TS);
const MAX_INTERACTIONS_PER_VIEW = 240;

const normalize = (value = "") =>
  String(value)
    .replace(/\s+/g, "_")
    .replace(/[^\w\u4e00-\u9fff-]/g, "")
    .slice(0, 64) || "item";

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const buildViewUrl = (view) => `${BASE_URL}/?view=${encodeURIComponent(view)}`;

async function ensureDir(dir) {
  await fs.mkdir(dir, { recursive: true });
}

async function stateFingerprint(page) {
  return page.evaluate(() => {
    const body = document.body;
    return body ? `${document.title}|${body.scrollHeight}|${body.innerText.length}|${window.location.search}` : "no-body";
  });
}

async function autoReveal(page) {
  await page.evaluate(async () => {
    const delay = 120;
    let previousHeight = 0;
    while (true) {
      const h = document.body.scrollHeight;
      window.scrollTo(0, h);
      // eslint-disable-next-line no-await-in-loop
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

async function interactWithControls(page, viewName, viewDir, report) {
  const interactionDir = path.join(viewDir, "interactions");
  await ensureDir(interactionDir);

  const selector =
    "button, a[href], input:not([type='hidden']), textarea, select, [role='button'], [role='checkbox'], [role='radio'], [role='combobox']";

  const original = await stateFingerprint(page);
  const beforeViewUrl = buildViewUrl(viewName);

  let interactionIndex = 0;
  let successCount = 0;
  let warnCount = 0;
  for (let i = 0; ; i += 1) {
    const controls = await page.$$(selector);
    if (i >= controls.length) break;
    if (interactionIndex >= MAX_INTERACTIONS_PER_VIEW) break;
    const control = controls[i];
    const visible = await control.isVisible().catch(() => false);
    if (!visible) continue;

    const meta = await control.evaluate((el) => {
      const tag = el.tagName.toLowerCase();
      const type = el.type || "";
      const disabled = !!(el.disabled || el.getAttribute("aria-disabled") === "true");
      const label =
        el.getAttribute("aria-label") ||
        el.getAttribute("title") ||
        el.getAttribute("data-testid") ||
        el.textContent?.trim() ||
        el.value ||
        "unlabeled";
      const rect = el.getBoundingClientRect();
      const role = el.getAttribute("role") || "";
      return {
        tag,
        type,
        disabled,
        value: el.value || "",
        role,
        label: label.slice(0, 80),
        rect: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        },
      };
    });

    if (meta.tag === "a" && meta.label === "跳到工作区内容") {
      continue;
    }

    interactionIndex += 1;
    let result = "ok";
    let action = "inspect";
    let message = "";
    const beforeState = await stateFingerprint(page);
    const beforeUrl = page.url();

    if (meta.disabled) {
      result = "warn";
      message = "控件不可用（disabled）";
      warnCount += 1;
    } else {
      try {
        if (meta.tag === "input") {
          const typ = (meta.type || "").toLowerCase();
          if (["checkbox", "radio"].includes(typ)) {
            action = `click-${typ}`;
            await control.click({ timeout: 800 });
          } else if (typ === "file") {
            result = "skip";
            message = "文件输入控件，自动化跳过";
          } else if (typ === "number") {
            action = `fill-number-${meta.type}`;
            await control.evaluate((node) => {
              const n = Number.isFinite(Number(node.value)) ? Number(node.value) : 1;
              node.value = String(n);
              node.dispatchEvent(new Event("input", { bubbles: true }));
              node.dispatchEvent(new Event("change", { bubbles: true }));
            }).catch(() => {
              result = "skip";
              message = "number input 通过脚本赋值";
              warnCount += 1;
            });
          } else {
            action = `fill-${typ || "text"}`;
            await control.fill("");
            await control.fill(`ui-audit-${RUN_TS}`);
          }
        } else if (meta.tag === "textarea") {
          action = "fill-textarea";
          await control.fill("");
          await control.fill(`ui-audit-${RUN_TS}`);
        } else if (meta.tag === "select") {
          action = "select-option";
          const optionCount = await control.evaluate((node) => node.options?.length || 0);
          if (optionCount > 1) {
            await control.selectOption({ index: 1 }).catch(() => {});
          } else {
            result = "warn";
            message = "下拉框选项不足（无法进行切换）";
            warnCount += 1;
          }
        } else if (meta.tag === "a" || meta.tag === "button" || meta.role === "button" || meta.role === "link") {
          action = `click-${meta.tag}`;
          await control.click({ timeout: 900, force: false });
        } else if (meta.tag === "div" || meta.role) {
          action = `click-${meta.role || meta.tag}`;
          await control.click({ timeout: 900, force: true });
        } else {
          action = `interact-${meta.tag}`;
          await control.click({ timeout: 900 });
        }

        await wait(300);
        const afterUrl = page.url();
        if (afterUrl !== beforeUrl && !afterUrl.includes("/api")) {
          await page.goto(beforeViewUrl, { waitUntil: "domcontentloaded", timeout: 5000 }).catch(() => {});
          await autoReveal(page);
        }

        const afterState = await stateFingerprint(page);
        await page.screenshot({
          path: path.join(interactionDir, normalize(`${interactionIndex}_${meta.label}_${action}`).slice(0, 90) + ".png"),
          timeout: 1500,
        }).catch(() => {});

        if (result === "ok") {
          if (beforeState === afterState && afterUrl === beforeViewUrl) {
            result = "warn";
            message = "点击/输入后未见可见差异，请重点核对";
            warnCount += 1;
          } else {
            successCount += 1;
          }
        }
      } catch (error) {
        result = "error";
        message = error?.message ? String(error.message).slice(0, 180) : "interaction error";
        warnCount += 1;
          await page.goto(beforeViewUrl, { waitUntil: "domcontentloaded", timeout: 5000 }).catch(() => {});
      }
    }

    report.interactions.push({
      view: viewName,
      index: interactionIndex,
      label: meta.label,
      tag: meta.tag,
      type: meta.type,
      role: meta.role,
      action,
      result,
      message,
      bbox: meta.rect,
      urlBefore: beforeUrl,
      urlAfter: page.url(),
    });

    if (result !== "ok") {
      report.issues.push({
        view: viewName,
        widget: `${meta.tag}${meta.role ? `/${meta.role}` : ""}`,
        label: meta.label,
        issue: message || "未满足预期可见响应",
        index: interactionIndex,
      });
    }
  }

  return { interactionTotal: interactionIndex, successCount, warnCount, originalFingerprint: original };
}

async function auditView(browserContext, viewName, report) {
  const page = await browserContext.newPage({ viewport: { width: 1600, height: 900 } });
  const viewDir = path.join(RUN_DIR, "pages", viewName);
  await ensureDir(viewDir);

  const viewLog = {
    view: viewName,
    url: buildViewUrl(viewName),
    fullPageScreenshot: "",
    moduleCount: 0,
    interactionCount: 0,
    successCount: 0,
    warnCount: 0,
    consoleErrors: [],
  };

  const errors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      errors.push(`console.error: ${msg.text()}`);
    }
  });
  page.on("pageerror", (err) => {
    errors.push(`pageerror: ${err.message}`);
  });

  try {
    await page.goto(buildViewUrl(viewName), { waitUntil: "networkidle", timeout: 120000 });
    await autoReveal(page);

    const fullPage = path.join(viewDir, `${viewName}-full.png`);
    await page.screenshot({ path: fullPage, fullPage: true });
    viewLog.fullPageScreenshot = fullPage;

    viewLog.moduleCount = await screenshotModules(page, viewDir);

    const result = await interactWithControls(page, viewName, viewDir, report);
    viewLog.interactionCount = result.interactionTotal;
    viewLog.successCount = result.successCount;
    viewLog.warnCount = result.warnCount;
  } catch (error) {
    const msg = error?.message || "未知错误";
    viewLog.consoleErrors.push(`页面抓取失败：${msg}`);
    report.issues.push({
      view: viewName,
      widget: "page",
      label: "full-page",
      issue: `页面抓取失败：${msg}`,
      index: 0,
    });
  } finally {
    viewLog.consoleErrors.push(...errors);
    report.views.push(viewLog);
    await page.close();
  }
}

async function run() {
  await ensureDir(RUN_DIR);

  const report = {
    startTime: new Date().toISOString(),
    baseUrl: BASE_URL,
    runDir: RUN_DIR,
    views: [],
    interactions: [],
    issues: [],
  };

  const browser = await chromium.launch({
    headless: true,
    executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  });
  const context = await browser.newContext();

  const chunks = Array.from({ length: PARALLEL_WORKERS }, () => []);
  VIEWS.forEach((view, idx) => chunks[idx % PARALLEL_WORKERS].push(view));

  try {
    await Promise.all(
      chunks
        .filter((chunk) => chunk.length > 0)
        .map(async (chunk) => {
          for (const viewName of chunk) {
            await auditView(context, viewName, report);
          }
        }),
    );
  } finally {
    await context.close();
    await browser.close();
  }

  report.endTime = new Date().toISOString();
  report.summary = {
    totalViews: report.views.length,
    totalInteractions: report.interactions.length,
    totalIssues: report.issues.length,
    totalSuccessInteractions: report.interactions.filter((i) => i.result === "ok").length,
  };

  const reportMd = [
    "# Codex UI 检查报告",
    "",
    `- 开始时间: ${report.startTime}`,
    `- 结束时间: ${report.endTime}`,
    `- 输出目录: ${RUN_DIR}`,
    `- 服务地址: ${report.baseUrl}`,
    "",
    "## 1. 页面级结果",
    ...report.views.map((view) =>
      `- ${view.view}: 页面截图 ${path.relative(process.cwd(), view.fullPageScreenshot)} | 模块数 ${view.moduleCount} | 交互项 ${view.interactionCount} | 成功 ${view.successCount} | 可疑 ${view.warnCount}`,
    ),
    "",
    "## 2. 发现问题清单（待修复）",
    ...((report.issues.length
      ? report.issues.map((item, idx) => `${idx + 1}. [${item.view}] ${item.widget || ""} ${item.label || ""}：${item.issue}`)
      : ["- 未检测到明显异常。" ])),
    "",
    "## 3. 建议复核文件",
    `- 页面截图: ${path.relative(process.cwd(), path.join(RUN_DIR, "pages"))}`,
    `- 模块截图: ${path.relative(process.cwd(), path.join(RUN_DIR, "pages"))}`,
    `- 交互后截图: ${path.relative(process.cwd(), path.join(RUN_DIR, "pages"))}`,
  ].join("\n");

  await fs.writeFile(path.join(RUN_DIR, "audit_report.md"), reportMd, "utf8");
  await fs.writeFile(path.join(RUN_DIR, "audit_issues.json"), JSON.stringify(report.issues, null, 2), "utf8");
  await fs.writeFile(path.join(RUN_DIR, "audit_interactions.json"), JSON.stringify(report.interactions, null, 2), "utf8");
  await fs.writeFile(path.join(RUN_DIR, "audit_meta.json"), JSON.stringify(report, null, 2), "utf8");

  console.log(`DONE_DIR=${RUN_DIR}`);
}

await run();
