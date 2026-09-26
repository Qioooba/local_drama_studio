/** 页面点击审核：在真实页面里点开画面段、点候选预览，确认页面行为与真实数据一致（只预览，不改变采用）。 */
import { chromium } from "@playwright/test";

const projectId = process.argv[2];
const step = process.argv[3] ?? "storyboard";
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const page = await context.newPage();
const errors = [];
page.on("console", (message) => { if (message.type() === "error") errors.push(message.text().slice(0, 200)); });

await page.goto(`http://127.0.0.1:5173/explainers/${projectId}/${step}`, { waitUntil: "networkidle", timeout: 60000 });
await page.waitForTimeout(2500);

const shot = async (name) => page.screenshot({ path: `artifacts/t2i-style-audit/page-${step}-${name}.png` });
await shot("01-initial");

// 1) 点左侧第 3 个画面段
const beats = page.locator(".explainer-shot-item");
console.log("beats:", await beats.count());
if (await beats.count() >= 3) {
  await beats.nth(2).click();
  await page.waitForTimeout(1500);
}
await shot("02-beat-clicked");

// 2) 点第一个候选卡（只预览）
const cards = page.locator(".explainer-candidate-card__preview");
console.log("candidate cards:", await cards.count());
if (await cards.count() > 0) {
  await cards.first().click();
  await page.waitForTimeout(1200);
}
await shot("03-candidate-preview");
const state = await page.evaluate(() => ({
  header: (document.querySelector(".explainer-head__title")?.textContent || "").trim(),
  stage: (document.querySelector(".explainer-shot-stage")?.textContent || "").replace(/\s+/g, " ").slice(0, 220),
  bar: (document.querySelector(".explainer-action-bar")?.textContent || "").replace(/\s+/g, " ").trim().slice(0, 200),
  previewLabels: Array.from(document.querySelectorAll(".explainer-candidate-card")).slice(0, 3).map((node) => (node.textContent || "").replace(/\s+/g, " ").trim().slice(0, 90)),
  adoptButtons: Array.from(document.querySelectorAll("button")).map((b) => (b.textContent || "").trim()).filter((t) => t.includes("采用")).slice(0, 4),
  imgSrcs: Array.from(document.querySelectorAll("img")).map((i) => i.getAttribute("src")).filter(Boolean).slice(0, 3),
}));
console.log(JSON.stringify(state, null, 1));
console.log("console errors:", errors.length ? errors : "none");
await browser.close();
