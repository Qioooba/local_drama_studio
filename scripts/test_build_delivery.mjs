import { chromium } from "playwright";

async function run() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.goto("http://127.0.0.1:5173/projects/04710fb9-3a9e-44c0-aa2f-1e8485b26f72/episodes/1031eec1-784a-408a-969e-010536931d59/delivery?view=compose", { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);

  const buildBtn = page.locator('button:has-text("创建交付候选")');
  console.log("Build button disabled?", await buildBtn.isDisabled());

  await buildBtn.click();
  await page.waitForTimeout(3000);

  const successEl = page.locator('.delivery-workflow-panel .review-success');
  const errorEl = page.locator('.delivery-workflow-panel .inline-error');
  if (await successEl.count() > 0) console.log("Success:", await successEl.innerText());
  if (await errorEl.count() > 0) console.log("Error:", await errorEl.innerText());

  await browser.close();
}

run().catch(console.error);
