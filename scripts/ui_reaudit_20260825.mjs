import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

const BASE_URL = process.env.UI_BASE_URL ?? "http://127.0.0.1:5173";
const PROJECT_ID = "c60586d6-d554-49a9-9e4d-6ba43c65a602";
const EPISODE_ID = "3d46e2c1-4a65-44e6-8c2e-a5fc5efbd2f3";
const OUTPUT_DIR = join(process.cwd(), "output", "playwright", "ox-ui-reaudit-20260825");

const routes = [
  ["01-projects-root", "/"],
  ["02-projects", "/projects"],
  ["03-project-home", `/projects/${PROJECT_ID}`],
  ["04-story", `/projects/${PROJECT_ID}/story`],
  ["05-assets", `/projects/${PROJECT_ID}/assets`],
  ["06-qc-policies", `/projects/${PROJECT_ID}/qc-policies`],
  ["07-director-recipes", `/projects/${PROJECT_ID}/director-recipes`],
  ["08-production-settings", `/projects/${PROJECT_ID}/production-settings`],
  ["09-canvas", `/projects/${PROJECT_ID}/canvas`],
  ["10-operations", `/projects/${PROJECT_ID}/operations`],
  ["11-episode-plan", `/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/plan`],
  ["12-generation", `/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/generation`],
  ["13-review", `/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/review`],
  ["14-audio", `/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/audio`],
  ["15-timeline", `/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/timeline`],
  ["16-delivery", `/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/delivery`],
  ["17-models", `/models?project=${PROJECT_ID}`],
  ["18-jobs", `/jobs?project=${PROJECT_ID}`],
  ["19-diagnostics", `/diagnostics?project=${PROJECT_ID}`],
  ["20-lab", `/lab?project=${PROJECT_ID}`],
];

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function captureRoute(context, key, path) {
  const page = await context.newPage();
  const consoleErrors = [];
  const responseErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(`PAGEERROR: ${String(error)}`));
  page.on("response", (response) => {
    if (response.status() >= 400) responseErrors.push(`${response.status()} ${response.url()}`);
  });

  const startedAt = Date.now();
  await page.goto(`${BASE_URL}${path}`, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.waitForLoadState("networkidle", { timeout: 12_000 }).catch(() => undefined);
  await wait(1_200);

  const initial = await page.evaluate(() => {
    const bodyText = document.body.innerText;
    const sidebar = document.querySelector(".sidebar");
    const sidebarRect = sidebar?.getBoundingClientRect() ?? null;
    const visible = (element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
    };
    return {
      title: document.querySelector("h2")?.textContent?.trim() ?? document.title,
      scrollHeight: document.documentElement.scrollHeight,
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      sidebar: sidebarRect ? { top: sidebarRect.top, bottom: sidebarRect.bottom, height: sidebarRect.height } : null,
      visibleAlerts: [...document.querySelectorAll("[role=alert]")].filter(visible).map((item) => item.textContent?.trim()).filter(Boolean),
      visibleBusy: [...document.querySelectorAll("[aria-busy=true]")].filter(visible).map((item) => item.textContent?.trim()).filter(Boolean),
      suspiciousLoadingText: bodyText.match(/(?:正在(?:读取|加载|搜索|处理|复制|生成|导出)|搜索中|处理中|复制中|校验并导出中|生成草稿中)[^\n]*/g) ?? [],
      exposedUuidCount: (bodyText.match(/\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/gi) ?? []).length,
      duplicateApplied: bodyText.includes("APPLIED · APPLIED"),
      rawReviewStates: [...new Set(bodyText.match(/\b(?:NEEDS_CHANGES|APPROVED|SOURCE_AUTHORIZATION_REVOKED)\b/g) ?? [])],
    };
  });

  await page.screenshot({ path: join(OUTPUT_DIR, `${key}-top.png`), fullPage: false });
  await page.screenshot({ path: join(OUTPUT_DIR, `${key}-full.png`), fullPage: true });

  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await wait(250);
  const scrolled = await page.evaluate(() => {
    const rect = document.querySelector(".sidebar")?.getBoundingClientRect() ?? null;
    return {
      scrollY: window.scrollY,
      sidebar: rect ? { top: rect.top, bottom: rect.bottom, height: rect.height } : null,
    };
  });

  const result = {
    key,
    path,
    elapsedMs: Date.now() - startedAt,
    ...initial,
    scrolled,
    consoleErrors: [...new Set(consoleErrors)],
    responseErrors: [...new Set(responseErrors)],
    checks: {
      noHorizontalOverflow: initial.scrollWidth <= initial.clientWidth + 1,
      noConsoleErrors: consoleErrors.length === 0,
      noHttpErrors: responseErrors.length === 0,
      sidebarPinned: !initial.sidebar || !scrolled.sidebar || Math.abs(initial.sidebar.top - scrolled.sidebar.top) <= 1,
      noDuplicateApplied: !initial.duplicateApplied,
    },
  };
  await page.close();
  return result;
}

async function targetedChecks(context) {
  const page = await context.newPage();
  const results = {};

  await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/canvas`, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle", { timeout: 12_000 }).catch(() => undefined);
  await page.locator(".canvas-node-browser > summary").click().catch(() => undefined);
  const canvasNodeButton = page.locator(".canvas-node-browser button").first();
  const canvasHasNode = (await canvasNodeButton.count()) > 0 && await canvasNodeButton.isVisible().catch(() => false);
  if (canvasHasNode) await canvasNodeButton.click({ timeout: 2_500 }).catch(() => undefined);
  const upstream = page.getByRole("button", { name: "聚焦上游" });
  results.canvas = {
    keyboardNodeClickable: canvasHasNode,
    upstreamEnabledAfterSelection: canvasHasNode ? await upstream.isEnabled() : false,
    minimapInitiallyHidden: (await page.locator(".react-flow__minimap").count()) === 0,
    emptyStateAvailable: (await page.locator(".canvas-search-empty").count()) === 0,
  };
  if (canvasHasNode) await upstream.click({ timeout: 2_500 });

  await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/qc-policies`, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle", { timeout: 12_000 }).catch(() => undefined);
  const threshold = page.locator("input[type=number]").first();
  results.qcPolicies = {
    thresholdPresent: (await threshold.count()) > 0,
    thresholdEnabled: (await threshold.count()) > 0 ? await threshold.isEnabled() : false,
  };
  if ((await threshold.count()) > 0 && await threshold.isEnabled()) await threshold.click({ timeout: 2_500 });

  await page.goto(`${BASE_URL}/lab?project=${PROJECT_ID}`, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle", { timeout: 12_000 }).catch(() => undefined);
  const planButton = page.getByRole("button", { name: "生成测试计划" });
  results.lab = { testPlanButtonEnabled: await planButton.isEnabled() };
  if (await planButton.isEnabled()) {
    await planButton.click({ timeout: 2_500 });
    await page.waitForFunction(() => !document.querySelector("button[aria-busy=true]"), { timeout: 48_000 }).catch(() => undefined);
    results.lab.result = await page.locator(".review-success, .inline-error").last().textContent().catch(() => "无结果反馈");
    results.lab.busyButtonsAfterCompletion = await page.locator("button[aria-busy=true]").count();
  }

  await page.close();
  return results;
}

async function responsiveChecks(browser) {
  const checks = [];
  for (const viewport of [{ width: 1024, height: 768 }, { width: 375, height: 812 }, { width: 812, height: 375 }]) {
    const context = await browser.newContext({ viewport, locale: "zh-CN" });
    const page = await context.newPage();
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/story`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 12_000 }).catch(() => undefined);
    const metrics = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      navToggleVisible: getComputedStyle(document.querySelector(".mobile-nav-toggle")).display !== "none",
    }));
    await page.screenshot({ path: join(OUTPUT_DIR, `responsive-${viewport.width}x${viewport.height}.png`), fullPage: false });
    checks.push({ viewport, ...metrics, noHorizontalOverflow: metrics.scrollWidth <= metrics.clientWidth + 1 });
    await context.close();
  }
  return checks;
}

async function main() {
  await mkdir(OUTPUT_DIR, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1600, height: 900 }, locale: "zh-CN", colorScheme: "light" });
  await context.addInitScript(() => localStorage.setItem("local-drama.feature-flags.v2", JSON.stringify({ DIRECTOR_DESK_V2: true, ASSET_BIBLE_V2: true, EPISODE_AGENT_RUN_V2: true })));

  const routeResults = [];
  for (const [key, path] of routes) {
    process.stdout.write(`audit ${key}\n`);
    routeResults.push(await captureRoute(context, key, path));
  }
  const targeted = await targetedChecks(context);
  await context.close();
  const responsive = await responsiveChecks(browser);
  await browser.close();

  const failures = routeResults.flatMap((route) => Object.entries(route.checks).filter(([, passed]) => !passed).map(([check]) => `${route.key}: ${check}`));
  const report = { generatedAt: new Date().toISOString(), baseUrl: BASE_URL, failures, routeResults, targeted, responsive };
  await writeFile(join(OUTPUT_DIR, "report.json"), JSON.stringify(report, null, 2), "utf8");
  process.stdout.write(`done routes=${routeResults.length} failures=${failures.length}\n`);
  if (failures.length > 0) process.exitCode = 1;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
