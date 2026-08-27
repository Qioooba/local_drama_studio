import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PROJECT_ID = "04710fb9-3a9e-44c0-aa2f-1e8485b26f72";
const EPISODE_ID = "1031eec1-784a-408a-969e-010536931d59";
const BASE_URL = "http://127.0.0.1:5173";
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const EVIDENCE_DIR = path.join(ROOT, "docs", "evidence", "ui-uat-2026-08-22", "screens");

fs.mkdirSync(EVIDENCE_DIR, { recursive: true });

async function run() {
  console.log("🚀 Starting UAT-162 Timeline & Subtitles real browser verification...");
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();

  try {
    // ====================================================
    // Step 1: Open Subtitles Tab on Timeline Page
    // ====================================================
    console.log("1. Navigating to Subtitles tab on Timeline page...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/timeline?view=subtitles`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat162_01_subtitles_initial.jpg"), quality: 30, type: "jpeg" });

    // Check source document select
    const docSelect = page.locator('label:has-text("源剧本文档版本") select');
    const docVal = await docSelect.inputValue();
    console.log(`Source document version: ${docVal}`);

    // Test invalid cues error handling
    console.log("2. Testing invalid cues validation...");
    const cuesTextarea = page.locator('.subtitle-cues-field textarea');
    await cuesTextarea.fill(`[{"start_us": 5000000, "end_us": 2000000, "text": "倒退时间码"}]`);
    await page.locator('button:has-text("创建字幕 revision")').click();
    await page.waitForTimeout(500);

    const errorMsg = page.locator('.subtitle-revision-panel .inline-error');
    console.log(`Invalid cues error: ${await errorMsg.innerText()}`);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat162_02_subtitles_invalid_error.jpg"), quality: 30, type: "jpeg" });

    // Select ASS format
    console.log("3. Configuring ASS subtitle style template...");
    await page.locator('label:has-text("格式") select').selectOption("ASS");
    await page.waitForTimeout(300);

    // Expand style template editor
    const styleBtn = page.locator('button:has-text("字幕样式模板")');
    if (await styleBtn.count() > 0) {
      await styleBtn.click();
      await page.waitForTimeout(500);
    }

    // Configure style: Font, Size, Color, Position, Outline
    await page.locator('.subtitle-style-editor label:has-text("字体") input').fill("Microsoft YaHei");
    await page.locator('.subtitle-style-editor label:has-text("字号") input').fill("52");
    await page.locator('.subtitle-style-editor label:has-text("颜色") input').fill("#FFD700");
    await page.locator('.subtitle-style-editor label:has-text("位置") select').selectOption("BOTTOM");
    await page.locator('.subtitle-style-editor label:has-text("描边") input').fill("3");

    // Save as project template
    await page.locator('.subtitle-style-editor label:has-text("模板名称") input').fill("UAT金色描边字幕");
    await page.locator('button:has-text("保存为项目模板")').click();
    await page.waitForTimeout(1000);

    // Enter valid cues JSON conforming to script authority
    console.log("4. Entering valid cues and submitting subtitle revision...");
    const validCues = JSON.stringify([
      { start_us: 0, end_us: 2500000, text: "零点之前，不要让最后一卷胶片离开剧院。" },
      { start_us: 3000000, end_us: 6000000, text: "门口停着一辆没有牌照的黑车，有人在问第七码胶片。" }
    ], null, 2);
    await cuesTextarea.fill(validCues);
    await page.waitForTimeout(300);

    await page.locator('button:has-text("创建字幕 revision")').click();
    await page.waitForTimeout(1500);

    const successPill = page.locator('.subtitle-revision-panel .review-success');
    console.log(`Subtitle creation result: ${await successPill.innerText()}`);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat162_03_subtitles_created.jpg"), quality: 30, type: "jpeg" });

    // ====================================================
    // Step 2: Timeline Edit & Freeze tab
    // ====================================================
    console.log("5. Navigating to Timeline Composer tab (编排与冻结)...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/timeline?view=edit`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat162_04_timeline_composer_initial.jpg"), quality: 30, type: "jpeg" });

    // Inspect clips
    const clips = page.locator(".timeline-clip");
    const clipCount = await clips.count();
    console.log(`Timeline clips count: ${clipCount}`);

    // If some clips are missing video, assign available project video via MediaPicker
    for (let i = 0; i < clipCount; i++) {
      const clip = clips.nth(i);
      const isMissing = await clip.evaluate(el => el.classList.contains("missing"));
      if (isMissing) {
        console.log(`Clip ${i} is missing video, selecting project video...`);
        await clip.locator('button.timeline-choose-video').click();
        await page.waitForTimeout(500);

        // Click first available video card in MediaPicker
        const pickerCards = page.locator(".media-picker-card");
        if (await pickerCards.count() > 0) {
          await pickerCards.first().click();
          await page.waitForTimeout(300);
        }
      }
    }

    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat162_05_clips_populated.jpg"), quality: 30, type: "jpeg" });

    // Adjust clip 0 duration and transition on clip 1
    console.log("6. Adjusting clip durations and transitions...");
    if (clipCount > 0) {
      await clips.first().locator('label:has-text("时长") input').fill("4.5");
    }
    if (clipCount > 1) {
      await clips.nth(1).locator('label:has-text("入场转场") select').selectOption("DISSOLVE");
    }

    // Test saving DRAFT revision first
    console.log("7. Saving DRAFT revision...");
    await page.locator('button:has-text("保存新草稿 revision")').click();
    await page.waitForTimeout(1500);
    const draftFeedback = page.locator('.timeline-savebar + p, .timeline-v2-shell p[role="status"]').last();
    console.log(`Draft feedback: ${await draftFeedback.innerText()}`);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat162_06_draft_saved.jpg"), quality: 30, type: "jpeg" });

    // Test freeze confirmation and FROZEN revision
    console.log("8. Freezing new revision...");
    const freezeCheckbox = page.locator('.timeline-freeze-confirm input');
    await freezeCheckbox.check();
    await page.waitForTimeout(300);

    await page.locator('button:has-text("冻结新 revision")').click();
    await page.waitForTimeout(2000);

    const frozenFeedback = page.locator('.timeline-savebar + p, .timeline-v2-shell p[role="status"]').last();
    console.log(`Frozen feedback: ${await frozenFeedback.innerText()}`);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat162_07_frozen_saved.jpg"), quality: 30, type: "jpeg" });

    // ====================================================
    // Step 3: Timeline Export & Handshake tab
    // ====================================================
    console.log("9. Navigating to Export tab (导出与合成)...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/timeline?view=export`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat162_08_export_tab.jpg"), quality: 30, type: "jpeg" });

    // Test Export OTIO/EDL
    console.log("10. Exporting OTIO / EDL standard package...");
    const exportOtioBtn = page.locator('button:has-text("导出标准 / OTIO / EDL")');
    if (await exportOtioBtn.count() > 0 && !await exportOtioBtn.isDisabled()) {
      await exportOtioBtn.click();
      await page.waitForTimeout(2000);
      const otioResult = page.locator('.export-result, .timeline-v2-export .inline-error').first();
      console.log(`OTIO Export Result: ${await otioResult.innerText()}`);
    }

    // Test Export Jianying Draft
    console.log("11. Exporting Jianying draft...");
    const exportJianyingBtn = page.locator('button:has-text("导出剪映草稿")');
    if (await exportJianyingBtn.count() > 0 && !await exportJianyingBtn.isDisabled()) {
      await exportJianyingBtn.click();
      await page.waitForTimeout(2000);
      const jianyingResult = page.locator('.export-result, .timeline-v2-export .inline-error').last();
      console.log(`Jianying Export Result: ${await jianyingResult.innerText()}`);
    }


    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat162_09_export_completed.jpg"), quality: 30, type: "jpeg" });

    // Test Drawers: Facts and Compose
    console.log("12. Testing Drawers (版本证据 & 查看合成检查)...");
    await page.locator('button:has-text("版本证据")').click();
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat162_10_facts_drawer.jpg"), quality: 30, type: "jpeg" });
    await page.locator('[role="dialog"] button:has-text("×"), [role="dialog"] button.drawer-close').first().click();
    await page.waitForTimeout(500);

    await page.locator('button:has-text("查看合成检查")').click();
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat162_11_compose_drawer.jpg"), quality: 30, type: "jpeg" });

    console.log("🎉 UAT-162 Timeline & Subtitles verification completed successfully!");
  } catch (error) {
    console.error("❌ Execution error:", error);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat162_error.jpg"), quality: 30, type: "jpeg" }).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
}

run().catch((e) => {
  console.error(e);
  process.exit(1);
});
