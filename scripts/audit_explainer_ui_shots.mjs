/**
 * Real-UI audit capture for the explainer factory.
 *
 * Uses the project's own Playwright install and the real dev servers (API on 3210 via
 * Vite's /api proxy, Vite on 5173).  Nothing here is mocked: the pages read their real
 * read models, so a screenshot shows what the product actually renders.
 *
 *   node ux_audit_shots.mjs <projectId> <outDir>
 */
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { chromium } from "@playwright/test";

const projectId = process.argv[2];
const outDir = process.argv[3] ?? "artifacts/ui-audit";
if (!projectId) {
  console.error("usage: node ux_audit_shots.mjs <projectId> [outDir]");
  process.exit(2);
}

const BASE = "http://127.0.0.1:5173";
const PAGES = ["script", "assets", "audio", "storyboard", "clips", "review"];
const VIEWPORTS = [
  { name: "1080p", width: 1920, height: 1080 },
  { name: "2k", width: 2560, height: 1440 },
];

await mkdir(outDir, { recursive: true });

const browser = await chromium.launch();
const report = [];

for (const viewport of VIEWPORTS) {
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text().slice(0, 300));
  });
  page.on("pageerror", (error) => consoleErrors.push(`PAGEERROR ${String(error).slice(0, 300)}`));

  // The factory list first: it is the entry point a user actually lands on.
  for (const target of [
    { key: "factory", url: `${BASE}/explainers` },
    { key: "new", url: `${BASE}/explainers/new` },
  ]) {
    await page.goto(target.url, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(1500);
    const file = path.join(outDir, `${target.key}-${viewport.name}.png`);
    await page.screenshot({ path: file, fullPage: false });
    report.push({ viewport: viewport.name, page: target.key, file, url: page.url() });
  }

  for (const step of PAGES) {
    const url = `${BASE}/explainers/${projectId}/${step}`;
    await page.goto(url, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(2500);
    const file = path.join(outDir, `${step}-${viewport.name}.png`);
    await page.screenshot({ path: file, fullPage: false });
    const metrics = await page.evaluate(() => {
      const bar = document.querySelector(".explainer-action-bar");
      const steps = document.querySelector(".explainer-steps, .explainer-step-bar");
      const overflowX = document.documentElement.scrollWidth > window.innerWidth + 1;
      // Anything wider than the viewport is a real horizontal overflow, so report the
      // offending elements instead of only a boolean.
      const wide = [];
      for (const node of document.querySelectorAll("body *")) {
        const rect = node.getBoundingClientRect();
        if (rect.width > 0 && rect.right > window.innerWidth + 2) {
          wide.push({
            tag: node.tagName.toLowerCase(),
            cls: String(node.className || "").slice(0, 80),
            right: Math.round(rect.right),
            text: (node.textContent || "").trim().slice(0, 40),
          });
        }
      }
      return {
        title: document.title,
        viewportWidth: window.innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        overflowX,
        horizontalOverflowNodes: wide.slice(0, 12),
        actionBarVisible: Boolean(bar),
        actionBarText: bar ? (bar.textContent || "").replace(/\s+/g, " ").trim().slice(0, 200) : null,
        stepBarVisible: Boolean(steps),
        // The bottom bar must not cover the last form row: compare its top with the
        // page's last visible element bottom.
        bodyScrollHeight: document.body.scrollHeight,
        headings: Array.from(document.querySelectorAll("h1,h2,h3")).map((node) =>
          (node.textContent || "").replace(/\s+/g, " ").trim().slice(0, 60),
        ),
      };
    });
    report.push({ viewport: viewport.name, page: step, file, url: page.url(), metrics });
  }

  report.push({ viewport: viewport.name, consoleErrors: [...new Set(consoleErrors)].slice(0, 20) });
  await context.close();
}

await browser.close();
console.log(JSON.stringify(report, null, 2));
