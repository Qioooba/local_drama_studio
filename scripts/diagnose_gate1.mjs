import { chromium } from "playwright";

async function main() {
  const browser = await chromium.launch({ headless: false, slowMo: 100 });
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  
  await page.goto("http://127.0.0.1:5173/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/story#story-import");
  await page.waitForTimeout(2000);

  const input = page.locator("label:has-text('电脑中的文档绝对路径') input").first();
  await input.fill("F:\\AI_Projects\\h3\\local_drama_studio\\docs\\evidence\\ui-uat-2026-08-22\\sample_novel.txt");
  
  await page.locator("button:has-text('建立源版本并解析预览')").click();
  await page.waitForTimeout(2000);

  const commitBtn = page.locator("button:has-text('确认 commit（不覆盖母本）')");
  if (await commitBtn.count() > 0 && await commitBtn.isEnabled()) {
    await commitBtn.click();
    await page.waitForTimeout(1000);
  }

  const btn = page.locator("button:has-text('提交 AI 拆解任务')");
  console.log("Button count:", await btn.count());
  console.log("Button visible:", await btn.isVisible());
  console.log("Button enabled:", await btn.isEnabled());

  // Check select values
  const seasons = page.locator("select[aria-label='AI 拆解目标季度']");
  console.log("Season count:", await seasons.count(), "val:", await seasons.inputValue().catch(() => "none"));

  const eps = page.locator("select[aria-label='AI 拆解目标集']");
  console.log("Episode count:", await eps.count(), "val:", await eps.inputValue().catch(() => "none"));

  // Check inputs
  const pStart = page.locator("label:has-text('本集原文起始段') input");
  console.log("pStart val:", await pStart.inputValue().catch(() => "none"));

  const pEnd = page.locator("label:has-text('本集原文结束段') input");
  console.log("pEnd val:", await pEnd.inputValue().catch(() => "none"));

  // Check warnings
  const warnings = await page.locator(".breakdown-runtime-warning, .inline-error").allInnerTexts();
  console.log("Warnings:", warnings);

  await page.waitForTimeout(3000);
  console.log("Final Button enabled:", await btn.isEnabled());

  if (await btn.isEnabled()) {
    await btn.click();
    console.log("Clicked submit button successfully!");
    await page.waitForTimeout(3000);
  }

  await browser.close();
}

main().catch(console.error);
