import { chromium } from "playwright";

async function main() {
  const browser = await chromium.launch({ headless: false, slowMo: 150 });
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  
  await page.goto("http://127.0.0.1:5173/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/story#story-review");
  await page.waitForTimeout(2000);

  const checkbox = page.locator("input[type='checkbox']").first();
  console.log("Checkbox visible:", await checkbox.isVisible());
  await checkbox.check();
  await page.waitForTimeout(500);

  const applyBtn = page.locator("button:has-text('应用到成片')").first();
  console.log("Apply button visible:", await applyBtn.isVisible());
  console.log("Apply button enabled:", await applyBtn.isEnabled());

  if (await applyBtn.isEnabled()) {
    await applyBtn.click();
    console.log("Clicked apply button!");
    await page.waitForTimeout(4000);
  }

  const feedback = await page.locator(".frame-feedback, .inline-error").allInnerTexts();
  console.log("Feedback after apply:", feedback);

  await browser.close();
}

main().catch(console.error);
