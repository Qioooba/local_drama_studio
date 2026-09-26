/** 页面图片实载检查 + 六步点击遍历（只做非破坏性点击）。

用途：确认候选卡上的缩略图在真实浏览器里**真的解码成功**（`naturalWidth > 0`），
而不只是 URL 正确；同时遍历六个步骤页，记录控制台错误与主操作按钮文案。

    node scripts/audit_explainer_ui_pages.mjs <project_id> [--step storyboard]
*/
import { chromium } from "@playwright/test";

const projectId = process.argv[2];
const onlyStep = process.argv.includes("--step") ? process.argv[process.argv.indexOf("--step") + 1] : null;
if (!projectId) {
  console.error("usage: node scripts/audit_explainer_ui_pages.mjs <project_id>");
  process.exit(2);
}

const STEPS = ["script", "assets", "audio", "storyboard", "clips", "review"];
const steps = onlyStep ? [onlyStep] : STEPS;
const browser = await chromium.launch();
const report = [];

for (const step of steps) {
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await context.newPage();
  const errors = [];
  const failedRequests = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text().slice(0, 200));
  });
  page.on("response", (response) => {
    if (response.status() >= 400 && response.url().includes("/api/")) {
      failedRequests.push(`${response.status()} ${response.url().replace(/^https?:\/\/[^/]+/, "").slice(0, 130)}`);
    }
  });

  await page.goto(`http://127.0.0.1:5173/explainers/${projectId}/${step}`, { waitUntil: "networkidle", timeout: 90000 });
  await page.waitForTimeout(3000);

  // 非破坏性点击：切到第 3 个画面段（若存在），再点第一张候选卡预览。
  if (step === "storyboard" || step === "clips") {
    const beats = page.locator(".explainer-shot-item");
    if ((await beats.count()) >= 3) {
      await beats.nth(2).click();
      await page.waitForTimeout(1500);
    }
    const cards = page.locator(".explainer-candidate-card__preview");
    if ((await cards.count()) >= 1) {
      await cards.first().click();
      await page.waitForTimeout(1500);
    }
  }

  const images = await page.evaluate(() =>
    Array.from(document.querySelectorAll("img"))
      .slice(0, 24)
      .map((img) => ({
        src: (img.getAttribute("src") || "").slice(0, 140),
        complete: img.complete,
        naturalWidth: img.naturalWidth,
        naturalHeight: img.naturalHeight,
      })),
  );
  const state = await page.evaluate(() => ({
    title: (document.querySelector(".explainer-head__title")?.textContent || "").trim().slice(0, 80),
    buttons: Array.from(document.querySelectorAll("button"))
      .map((b) => (b.textContent || "").trim())
      .filter(Boolean)
      .slice(0, 14),
    overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
    actionBar: (document.querySelector(".explainer-action-bar")?.textContent || "").replace(/\s+/g, " ").trim().slice(0, 160),
  }));

  await page.screenshot({ path: `artifacts/ui-audit/pages-${step}-1920.png` });
  report.push({ step, state, images: images.filter((i) => i.src), errors, failedRequests });
  console.log(
    JSON.stringify(
      {
        step,
        title: state.title,
        overflowX: state.overflowX,
        images: images.filter((i) => i.src).length,
        decoded: images.filter((i) => i.naturalWidth > 0).length,
        brokenImages: images.filter((i) => i.src && i.naturalWidth === 0).map((i) => i.src),
        errors,
        failedRequests: failedRequests.slice(0, 6),
      },
      null,
      1,
    ),
  );
  await context.close();
}

await browser.close();
const fs = await import("node:fs");
fs.mkdirSync("artifacts/ui-audit", { recursive: true });
fs.writeFileSync("artifacts/ui-audit/pages-report.json", JSON.stringify(report, null, 2));
console.log("report: artifacts/ui-audit/pages-report.json");
