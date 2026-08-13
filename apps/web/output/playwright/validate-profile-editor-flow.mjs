import { chromium } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";
import { execFileSync } from "node:child_process";

const outputDir = path.resolve("output/playwright");
await fs.mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({
  executablePath: "C:/Users/Qi/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe",
  headless: true,
});
const sizes = [[1440, 900], [1280, 800], [1024, 768]];
const results = [];
const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
for (const [index, [width, height]] of sizes.entries()) {
  const page = await browser.newPage({ viewport: { width, height } });
  const consoleErrors = [];
  const pageErrors = [];
  const failedResponses = [];
  const mediaRequests = [];
  page.on("console", (message) => { if (["error", "warning"].includes(message.type())) consoleErrors.push(`${message.type()}: ${message.text()}`); });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("response", (response) => {
    if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`);
    if (/\/thumbnail/.test(response.url()) && /size=large/.test(response.url())) mediaRequests.push(`large-thumb:${response.url()}`);
    if (/\/content\b/.test(response.url())) mediaRequests.push(`original-content:${response.url()}`);
  });
  await page.goto(`http://127.0.0.1:5173/?view=profiles&project=${projectId}`, { waitUntil: "networkidle" });
  await page.getByRole("heading", { name: "项目配置快照与切换影响" }).waitFor({ timeout: 15000 });
  await page.getByRole("heading", { name: "本地能力契约与不可变版本" }).waitFor();

  const record = { width, height, mutating: index === 0, consoleErrors, pageErrors, failedResponses, originalVideoRequested: false, horizontalOverflow: false, configurationSnapshot: true };
  if (index === 0) {
    // Always mutate a known immutable Published source; never re-validate a
    // DRAFT/PUBLISHED version left by a prior acceptance run.
    const source = page.locator(".profile-version-choice").filter({ hasText: "v12" }).first();
    await source.click();
    await page.locator(".profile-contract-meta").filter({ hasText: "v12" }).waitFor({ timeout: 15000 });
    const primary = page.locator(".profile-editor-actions .primary-action");
    await primary.click();
    await page.getByText(/已创建不可变 DRAFT/).waitFor({ timeout: 15000 });
    await page.locator(".profile-contract-meta").filter({ hasText: "DRAFT" }).waitFor({ timeout: 15000 });
    await page.getByRole("button", { name: "运行本地契约验证" }).waitFor({ timeout: 15000 });
    await page.getByRole("button", { name: "运行本地契约验证" }).click();
    const feedback = await page.locator(".inline-error, .review-success").first().textContent().catch(() => null);
    const validationLine = await page.locator(".profile-validation").first().textContent().catch(() => null);
    await page.waitForTimeout(800);
    const publishDisabled = await page.getByRole("button", { name: "发布已验证版本" }).isDisabled().catch(() => null);
    const draftLabel = await page.locator(".profile-version-choice.selected .profile-contract-meta, .profile-contract-meta strong").first().textContent().catch(() => null);
    record.feedback = feedback?.trim();
    record.validationLine = validationLine?.trim();
    record.publishDisabledAfterPASS = publishDisabled;
    record.selectedDraftLabel = draftLabel?.trim();
  } else {
    await page.waitForTimeout(400);
    const publishDisabled = await page.getByRole("button", { name: "发布已验证版本" }).isDisabled().catch(() => null);
    const validationLine = await page.locator(".profile-validation").first().textContent().catch(() => null);
    const labels = await page.locator(".profile-contract-fields label").allTextContents();
    record.publishDisabled = publishDisabled;
    record.validationLine = validationLine?.trim();
    record.contractLabels = labels.length;
  }
  const bodyMetrics = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
  record.horizontalOverflow = bodyMetrics.scrollWidth > bodyMetrics.clientWidth;
  record.originalVideoRequested = mediaRequests.some((u) => u.startsWith("original-content"));
  results.push(record);
  await page.screenshot({ path: path.join(outputDir, `profile-editor-flow-${width}x${height}.png`), fullPage: false });
  await page.close();
}
await browser.close();
await fs.writeFile(path.join(outputDir, "profile-editor-flow.json"), JSON.stringify(results, null, 2));

const ffmpeg = "ffmpeg";
for (const [width, height] of sizes) {
  const png = path.join(outputDir, `profile-editor-flow-${width}x${height}.png`);
  const webp = path.join(outputDir, `profile-editor-flow-${width}x${height}.webp`);
  execFileSync(ffmpeg, ["-y", "-i", png, "-vf", "scale=720:-2", "-q:v", "55", webp], { stdio: "ignore" });
  const size = (await fs.stat(webp)).size;
  console.log(`thumb ${width}x${height} -> ${webp} (${size} bytes)`);
}
console.log(JSON.stringify(results, null, 2));
