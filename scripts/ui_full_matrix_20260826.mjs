import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

const BASE_URL = process.env.UI_BASE_URL ?? "http://127.0.0.1:5173";
const PROJECT_ID = process.env.UI_PROJECT_ID ?? "fed7746b-4d34-4e61-a5f2-61451b3717a9";
const EPISODE_ID = process.env.UI_EPISODE_ID ?? "f88be6a6-2a75-4494-9862-df7846b92ba1";
const SHOT_ID = process.env.UI_SHOT_ID ?? "ac5429ad-797b-4450-81b5-99bf6e022ea5";
const OUTPUT_DIR = process.env.UI_OUTPUT_DIR
  ? join(process.cwd(), process.env.UI_OUTPUT_DIR)
  : join(process.cwd(), "output", "playwright", "ui-full-audit-20260826-after");

const routes = [
  ["01-root", "/"],
  ["02-projects", "/projects"],
  ["03-project-home", `/projects/${PROJECT_ID}`],
  ["04-story", `/projects/${PROJECT_ID}/story`],
  ["05-assets", `/projects/${PROJECT_ID}/assets`],
  ["06-settings-production", `/projects/${PROJECT_ID}/settings/production`],
  ["07-settings-capabilities", `/projects/${PROJECT_ID}/settings/capabilities`],
  ["08-settings-directing", `/projects/${PROJECT_ID}/settings/directing`],
  ["09-settings-quality", `/projects/${PROJECT_ID}/settings/quality`],
  ["10-settings-delivery", `/projects/${PROJECT_ID}/settings/delivery`],
  ["11-settings-automation", `/projects/${PROJECT_ID}/settings/automation`],
  ["12-settings-rights", `/projects/${PROJECT_ID}/settings/rights`],
  ["13-settings-data", `/projects/${PROJECT_ID}/settings/data`],
  ["14-labs", `/projects/${PROJECT_ID}/labs`],
  ["15-episode-plan", `/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/plan`],
  ["16-shot-studio", `/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/studio`],
  ["17-shot-studio-shot", `/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/studio/${SHOT_ID}`],
  ["18-episode-production", `/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/production`],
  ["19-post-review", `/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/post/review`],
  ["20-post-audio", `/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/post/audio`],
  ["21-post-edit", `/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/post/edit`],
  ["22-delivery", `/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/delivery`],
  ["23-system-capabilities", `/system/capabilities?project=${PROJECT_ID}`],
  ["24-system-jobs", `/system/jobs?project=${PROJECT_ID}`],
  ["25-system-diagnostics", `/system/diagnostics?project=${PROJECT_ID}`],
  ["26-system-workflows", `/system/workflows?project=${PROJECT_ID}`],
];

const requestedRouteKeys = new Set((process.env.UI_ROUTE_KEYS ?? "").split(",").map((value) => value.trim()).filter(Boolean));
const auditRoutes = requestedRouteKeys.size > 0 ? routes.filter(([key]) => requestedRouteKeys.has(key)) : routes;

const viewports = [
  { key: "desktop", width: 1600, height: 1000 },
  { key: "tablet", width: 1024, height: 768 },
  { key: "mobile", width: 375, height: 812 },
];
const requestedViewportKeys = new Set((process.env.UI_VIEWPORT_KEYS ?? "").split(",").map((value) => value.trim()).filter(Boolean));
const auditViewports = requestedViewportKeys.size > 0 ? viewports.filter(({ key }) => requestedViewportKeys.has(key)) : viewports;

const landscapeRoutes = new Set(["15-episode-plan", "16-shot-studio", "17-shot-studio-shot", "21-post-edit"]);
const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const fileSafe = (value) => value.replace(/[^a-z0-9_-]+/gi, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "tab";

async function inspectPage(page, viewport) {
  return page.evaluate(({ width, height }) => {
    const visible = (element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) > 0.02 && rect.width > 0 && rect.height > 0;
    };
    const rgba = (value) => {
      const match = value.match(/rgba?\(([^)]+)\)/i);
      if (!match) return null;
      const parts = match[1].split(/[ ,/]+/).filter(Boolean).map(Number);
      if (parts.length < 3 || parts.slice(0, 3).some(Number.isNaN)) return null;
      return { r: parts[0], g: parts[1], b: parts[2], a: parts[3] ?? 1 };
    };
    const backgroundFor = (element) => {
      let current = element;
      while (current && current !== document.documentElement) {
        const parsed = rgba(getComputedStyle(current).backgroundColor);
        if (parsed && parsed.a >= .92) return parsed;
        current = current.parentElement;
      }
      return rgba(getComputedStyle(document.body).backgroundColor) ?? { r: 8, g: 10, b: 16, a: 1 };
    };
    const luminance = ({ r, g, b }) => {
      const linear = [r, g, b].map((channel) => {
        const value = channel / 255;
        return value <= .03928 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
      });
      return linear[0] * .2126 + linear[1] * .7152 + linear[2] * .0722;
    };
    const ratio = (foreground, background) => {
      const a = luminance(foreground);
      const b = luminance(background);
      return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
    };
    const selector = "button,a[href],input,select,textarea,summary,[role=button],[role=tab]";
    const controls = [...document.querySelectorAll(selector)].filter(visible);
    const touchTargetFailures = controls.map((element) => {
      const isChoice = element instanceof HTMLInputElement && ["checkbox", "radio"].includes(element.type);
      const hitArea = isChoice ? element.closest("label") ?? element : element;
      const rect = hitArea.getBoundingClientRect();
      return { tag: element.tagName.toLowerCase(), label: (element.getAttribute("aria-label") || element.textContent || element.getAttribute("placeholder") || "").trim().slice(0, 80), width: Math.round(rect.width), height: Math.round(rect.height) };
    }).filter((item) => item.width < 44 || item.height < 44).slice(0, 40);
    const unlabeledControls = [...document.querySelectorAll("input:not([type=hidden]),select,textarea")].filter(visible).filter((element) => {
      const id = element.id;
      return !element.getAttribute("aria-label") && !element.getAttribute("aria-labelledby") && !element.closest("label") && !(id && document.querySelector(`label[for=${CSS.escape(id)}]`));
    }).map((element) => ({ tag: element.tagName.toLowerCase(), type: element.getAttribute("type"), placeholder: element.getAttribute("placeholder") })).slice(0, 30);
    const iconButtonsWithoutName = [...document.querySelectorAll("button")].filter(visible).filter((button) => {
      const text = (button.textContent || "").trim();
      const hasImage = Boolean(button.querySelector("svg,img"));
      return hasImage && !text && !button.getAttribute("aria-label") && !button.getAttribute("title");
    }).length;
    const overflowElements = [...document.querySelectorAll("body *")].filter(visible).map((element) => {
      const rect = element.getBoundingClientRect();
      return { tag: element.tagName.toLowerCase(), cls: String(element.className || "").slice(0, 90), left: Math.round(rect.left), right: Math.round(rect.right), width: Math.round(rect.width) };
    }).filter((item) => item.left < -2 || item.right > width + 2).sort((a, b) => b.width - a.width).slice(0, 30);
    const lowContrast = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6,p,small,label,span,strong,a,button,td,th,legend")].filter(visible).filter((element) => element.childElementCount === 0 && (element.textContent || "").trim()).map((element) => {
      const style = getComputedStyle(element);
      const foreground = rgba(style.color);
      if (!foreground) return null;
      const background = backgroundFor(element);
      const value = ratio(foreground, background);
      const fontSize = Number.parseFloat(style.fontSize);
      const threshold = fontSize >= 18 || (fontSize >= 14 && Number(style.fontWeight) >= 700) ? 3 : 4.5;
      return { text: (element.textContent || "").trim().slice(0, 90), ratio: Number(value.toFixed(2)), threshold, color: style.color, background: `rgb(${background.r}, ${background.g}, ${background.b})`, fontSize };
    }).filter(Boolean).filter((item) => item.ratio + .05 < item.threshold).sort((a, b) => a.ratio - b.ratio).slice(0, 40);
    const lightSurfaces = [...document.querySelectorAll("section,article,aside,dialog,form,.panel,.card")].filter(visible).map((element) => {
      const color = rgba(getComputedStyle(element).backgroundColor);
      if (!color || color.a < .8) return null;
      return { cls: String(element.className || "").slice(0, 100), luminance: Number(luminance(color).toFixed(3)), color: getComputedStyle(element).backgroundColor };
    }).filter(Boolean).filter((item) => item.luminance > .55).slice(0, 30);
    const doc = document.documentElement;
    return {
      pathname: location.pathname + location.search + location.hash,
      title: document.querySelector("h2")?.textContent?.trim() ?? document.querySelector("h1")?.textContent?.trim() ?? document.title,
      document: { clientWidth: doc.clientWidth, scrollWidth: doc.scrollWidth, clientHeight: doc.clientHeight, scrollHeight: doc.scrollHeight },
      horizontalOverflow: Math.max(0, doc.scrollWidth - doc.clientWidth),
      viewportMismatch: doc.clientWidth !== width || window.innerHeight !== height,
      bodyFontSize: getComputedStyle(document.body).fontSize,
      visibleButtons: [...document.querySelectorAll("button")].filter(visible).length,
      visibleInputs: [...document.querySelectorAll("input,select,textarea")].filter(visible).length,
      visibleTabs: [...document.querySelectorAll("[role=tab]")].filter(visible).length,
      touchTargetFailures,
      unlabeledControls,
      iconButtonsWithoutName,
      overflowElements,
      lowContrast,
      lightSurfaces,
      alerts: [...document.querySelectorAll("[role=alert]")].filter(visible).map((element) => (element.textContent || "").trim().slice(0, 240)),
    };
  }, viewport);
}

async function capture(context, viewport, key, path) {
  const page = await context.newPage();
  const consoleErrors = [];
  const responseErrors = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", (error) => consoleErrors.push(`PAGEERROR: ${String(error)}`));
  page.on("response", (response) => { if (response.status() >= 400) responseErrors.push(`${response.status()} ${response.url()}`); });
  await page.goto(`${BASE_URL}${path}`, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.waitForLoadState("networkidle", { timeout: 12_000 }).catch(() => undefined);
  await delay(600);
  const prefix = `${viewport.key}-${key}`;
  await page.screenshot({ path: join(OUTPUT_DIR, `${prefix}-viewport.png`), fullPage: false });
  await page.screenshot({ path: join(OUTPUT_DIR, `${prefix}-full.png`), fullPage: true });
  const metrics = await inspectPage(page, viewport);
  const tabScreenshots = [];
  if (viewport.key === "desktop") {
    const initialTabs = page.locator('[role="tab"]:visible');
    const count = Math.min(await initialTabs.count(), 12);
    for (let index = 0; index < count; index += 1) {
      const tabs = page.locator('[role="tab"]:visible');
      if (index >= await tabs.count()) break;
      const tab = tabs.nth(index);
      const label = (await tab.getAttribute("aria-label")) || (await tab.textContent()) || `tab-${index + 1}`;
      if (await tab.isDisabled().catch(() => true)) continue;
      await tab.click({ timeout: 3_000 }).catch(() => undefined);
      await delay(250);
      const filename = `${prefix}-tab-${String(index + 1).padStart(2, "0")}-${fileSafe(label)}.png`;
      await page.screenshot({ path: join(OUTPUT_DIR, filename), fullPage: false });
      tabScreenshots.push({ index, label: label.trim(), filename });
    }
  }
  await page.close();
  return { key, path, viewport, metrics, tabScreenshots, consoleErrors: [...new Set(consoleErrors)], responseErrors: [...new Set(responseErrors)] };
}

async function main() {
  await mkdir(OUTPUT_DIR, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const results = [];
  for (const viewport of auditViewports) {
    const context = await browser.newContext({ viewport, locale: "zh-CN", colorScheme: "dark", reducedMotion: "no-preference" });
    await context.addInitScript(() => localStorage.setItem("local-drama.feature-flags.v2", JSON.stringify({ DIRECTOR_DESK_V2: true, ASSET_BIBLE_V2: true, EPISODE_AGENT_RUN_V2: true })));
    for (const [key, path] of auditRoutes) {
      process.stdout.write(`capture ${viewport.key} ${key}\n`);
      results.push(await capture(context, viewport, key, path));
    }
    await context.close();
  }
  if (requestedViewportKeys.size === 0 || requestedViewportKeys.has("landscape")) {
    const landscape = { key: "landscape", width: 812, height: 375 };
    const landscapeContext = await browser.newContext({ viewport: landscape, locale: "zh-CN", colorScheme: "dark" });
    await landscapeContext.addInitScript(() => localStorage.setItem("local-drama.feature-flags.v2", JSON.stringify({ DIRECTOR_DESK_V2: true, ASSET_BIBLE_V2: true, EPISODE_AGENT_RUN_V2: true })));
    for (const [key, path] of auditRoutes.filter(([key]) => landscapeRoutes.has(key))) {
      process.stdout.write(`capture landscape ${key}\n`);
      results.push(await capture(landscapeContext, landscape, key, path));
    }
    await landscapeContext.close();
  }
  await browser.close();

  const summary = results.map((item) => ({
    route: item.key,
    viewport: item.viewport.key,
    horizontalOverflow: item.metrics.horizontalOverflow,
    overflowElementCount: item.metrics.overflowElements.length,
    touchTargetFailureCount: item.metrics.touchTargetFailures.length,
    unlabeledControlCount: item.metrics.unlabeledControls.length,
    lowContrastCount: item.metrics.lowContrast.length,
    lightSurfaceCount: item.metrics.lightSurfaces.length,
    alertCount: item.metrics.alerts.length,
    consoleErrorCount: item.consoleErrors.length,
    responseErrorCount: item.responseErrors.length,
  }));
  await writeFile(join(OUTPUT_DIR, "report.json"), JSON.stringify({ generatedAt: new Date().toISOString(), baseUrl: BASE_URL, projectId: PROJECT_ID, episodeId: EPISODE_ID, shotId: SHOT_ID, routes: auditRoutes, viewports: auditViewports, results, summary }, null, 2), "utf8");
  await writeFile(join(OUTPUT_DIR, "summary.json"), JSON.stringify(summary, null, 2), "utf8");
  process.stdout.write(`done captures=${results.length} routes=${auditRoutes.length}\n`);
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
