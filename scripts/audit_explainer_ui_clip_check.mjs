/**
 * Find text that is actually cut off in the rendered explainer pages.
 *
 * A screenshot at 2K shows the composition, but it hides the interesting failure: an
 * element whose content is clipped by its own box (a truncated label, a number pushed
 * out of a fixed-height card).  This walks the real DOM at both supported resolutions
 * and reports every element that hides content while its own overflow is not
 * scrollable — i.e. the content is unreachable, not merely off-screen.
 *
 *   node ux_clip_check.mjs <projectId> [outFile]
 */
import { writeFile } from "node:fs/promises";
import { chromium } from "@playwright/test";

const projectId = process.argv[2];
const outFile = process.argv[3] ?? "artifacts/ui-audit/clip-report.json";
const BASE = "http://127.0.0.1:5173";
const PAGES = ["script", "assets", "audio", "storyboard", "clips", "review"];
const VIEWPORTS = [
  { name: "1080p", width: 1920, height: 1080 },
  { name: "2k", width: 2560, height: 1440 },
];

const browser = await chromium.launch();
const report = [];

for (const viewport of VIEWPORTS) {
  const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } });
  const page = await context.newPage();
  for (const target of [
    { key: "factory", url: `${BASE}/explainers` },
    { key: "new", url: `${BASE}/explainers/new` },
    ...PAGES.map((step) => ({ key: step, url: `${BASE}/explainers/${projectId}/${step}` })),
  ]) {
    await page.goto(target.url, { waitUntil: "networkidle", timeout: 60000 });
    await page.waitForTimeout(2000);
    const clipped = await page.evaluate(() => {
      const problems = [];
      const seen = new Set();
      for (const node of document.querySelectorAll("body *")) {
        const style = getComputedStyle(node);
        const hides = ["hidden", "clip"].includes(style.overflowX) || ["hidden", "clip"].includes(style.overflowY);
        if (!hides) continue;
        const overflowX = node.scrollWidth - node.clientWidth;
        const overflowY = node.scrollHeight - node.clientHeight;
        const scrollable = ["auto", "scroll"].includes(style.overflowX) || ["auto", "scroll"].includes(style.overflowY);
        if (scrollable) continue;
        if (overflowX <= 2 && overflowY <= 2) continue;
        const rect = node.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        // A deliberate clamp is acceptable only when the full value is reachable from
        // the same element: either a CSS ellipsis or a line clamp, together with a
        // title attribute (or the text living in full elsewhere in the page).
        const clamped = style.textOverflow === "ellipsis" || style.webkitLineClamp !== "none";
        const ellipsis = clamped && Boolean(node.getAttribute("title"));
        const text = (node.textContent || "").replace(/\s+/g, " ").trim();
        if (!text) continue;
        const key = `${node.tagName}.${node.className}|${text.slice(0, 40)}`;
        if (seen.has(key)) continue;
        seen.add(key);
        problems.push({
          tag: node.tagName.toLowerCase(),
          cls: String(node.className || "").slice(0, 90),
          overflowX: Math.round(overflowX),
          overflowY: Math.round(overflowY),
          box: `${Math.round(rect.width)}x${Math.round(rect.height)}`,
          scrollBox: `${node.scrollWidth}x${node.scrollHeight}`,
          ellipsisWithTitle: ellipsis,
          text: text.slice(0, 80),
        });
      }
      return problems;
    });
    report.push({ viewport: viewport.name, page: target.key, url: page.url(), clipped });
  }
  await context.close();
}

await browser.close();
await writeFile(outFile, JSON.stringify(report, null, 2), "utf8");
for (const entry of report) {
  const hard = entry.clipped.filter((item) => !item.ellipsisWithTitle);
  const soft = entry.clipped.filter((item) => item.ellipsisWithTitle);
  console.log(`${entry.viewport} ${entry.page}: ${hard.length} clipped, ${soft.length} ellipsis+title`);
  for (const item of hard.slice(0, 8)) {
    console.log(
      `   ${item.tag}.${item.cls} box=${item.box} content=${item.scrollBox} +${item.overflowX}/+${item.overflowY} :: ${item.text}`,
    );
  }
}
