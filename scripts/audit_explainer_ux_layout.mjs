/**
 * Measure the explainer workspace's real layout boxes at several widths.
 *
 * Reports each significant container's bounding box plus its computed grid/max-width, so
 * an under-filled or overflowing layout is described by numbers instead of an impression.
 *
 *   node ux_layout_measure.mjs <projectId>
 */
import { chromium } from "@playwright/test";

const projectId = process.argv[2];
const BASE = "http://127.0.0.1:5173";
const STEPS = ["script", "assets", "audio", "storyboard", "clips", "review"];
const WIDTHS = [1600, 1920, 2560];

const SELECTORS = [
  ".app-shell",
  ".content",
  ".explainer-surface",
  ".explainer-workspace",
  ".explainer-head",
  ".explainer-steps",
  ".explainer-page",
  ".explainer-three-pane",
  ".explainer-grid",
  ".explainer-action-bar",
  ".explainer-panel",
  ".explainer-main",
];

const browser = await chromium.launch();
const output = [];
for (const width of WIDTHS) {
  const context = await browser.newContext({ viewport: { width, height: width >= 2560 ? 1440 : width >= 1920 ? 1080 : 900 } });
  const page = await context.newPage();
  for (const step of STEPS) {
    await page.goto(`${BASE}/explainers/${projectId}/${step}`, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(1200);
    const measured = await page.evaluate((selectors) => {
      const rows = [];
      for (const selector of selectors) {
        for (const node of document.querySelectorAll(selector)) {
          const rect = node.getBoundingClientRect();
          const style = getComputedStyle(node);
          rows.push({
            selector,
            cls: String(node.className || "").slice(0, 90),
            left: Math.round(rect.left),
            width: Math.round(rect.width),
            right: Math.round(rect.right),
            height: Math.round(rect.height),
            maxWidth: style.maxWidth,
            marginLeft: style.marginLeft,
            marginRight: style.marginRight,
            display: style.display,
            gridTemplateColumns: style.gridTemplateColumns,
          });
        }
      }
      return {
        viewportWidth: window.innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        usedRightEdge: Math.max(0, ...rows.map((row) => row.right)),
        rows,
      };
    }, SELECTORS);
    output.push({ width, step, measured });
  }
  await context.close();
}
await browser.close();
console.log(JSON.stringify(output, null, 2));
