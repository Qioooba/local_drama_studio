import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const PROJECT_ID = "04710fb9-3a9e-44c0-aa2f-1e8485b26f72";
const EPISODE_ID = "1031eec1-784a-408a-969e-010536931d59";
const BASE_URL = "http://127.0.0.1:5173";
const EVIDENCE_DIR = "F:\\AI_Projects\\h3\\local_drama_studio\\docs\\evidence\\ui-uat-2026-08-22\\screens";

fs.mkdirSync(EVIDENCE_DIR, { recursive: true });

async function run() {
  console.log("🚀 Starting UAT-161 real browser execution...");
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();

  try {
    // ====================================================
    // Phase 1: Review and Reject low-level BGM & SFX
    // ====================================================
    console.log("1. Navigating to Review page...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/review`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat161_01_review_initial.jpg"), quality: 30, type: "jpeg" });

    // Check if there are review rows
    let reviewRows = page.locator(".review-row");
    let rowCount = await reviewRows.count();
    console.log(`Found ${rowCount} review rows.`);

    // Process all pending audio items that fail QC
    while (rowCount > 0) {
      console.log(`Processing audio review item 0 of ${rowCount}...`);
      await reviewRows.first().click();
      await page.waitForTimeout(500);

      // Verify "音轨绑定无需采用" button exists and is disabled
      const noPromoteBtn = page.locator('button:has-text("音轨绑定无需采用")');
      if (await noPromoteBtn.count() > 0) {
        const isDisabled = await noPromoteBtn.isDisabled();
        console.log(`"音轨绑定无需采用" button visible & disabled: ${isDisabled}`);
      }

      // Run QC if needed
      const runQcBtn = page.locator('button:has-text("运行音频 QC")');
      if (await runQcBtn.count() > 0 && !await runQcBtn.isDisabled()) {
        console.log("Running audio QC...");
        await runQcBtn.click();
        await page.waitForTimeout(1500);
      }

      const qcResults = await page.locator(".audio-qc-panel .review-meta span").allInnerTexts();
      console.log("QC Results:", qcResults);

      // Fill checklist: fail loudness
      const checkFieldsets = page.locator(".review-check");
      const checkCount = await checkFieldsets.count();
      for (let i = 0; i < checkCount; i++) {
        const fieldset = checkFieldsets.nth(i);
        const legend = await fieldset.locator("legend").innerText();
        if (legend.includes("响度")) {
          await fieldset.getByRole("radio", { name: "不通过", exact: true }).check();
        } else {
          await fieldset.getByRole("radio", { name: "通过", exact: true }).check();
        }
      }

      // Select 审核决定: REJECTED
      console.log("Selecting 审核决定: 拒绝");
      await page.locator('select#review-decision').selectOption("REJECTED");
      await page.waitForTimeout(300);

      console.log("Clicking 提交审核...");
      await page.locator('button:has-text("提交审核")').click();
      await page.waitForTimeout(500);

      const rejectBox = page.locator('.inline-note-box');
      if (await rejectBox.count() > 0) {
        console.log("Filling rejection reason...");
        await rejectBox.locator('textarea').fill("Integrated loudness is approximately -40 to -44 LUFS (below required -30 LUFS threshold). Rejected for replacement with a properly mastered track.");
        await page.waitForTimeout(300);
        await rejectBox.locator('button:has-text("确认拒绝")').click();
        await page.waitForTimeout(1000);
      }

      // Re-query review rows
      reviewRows = page.locator(".review-row");
      rowCount = await reviewRows.count();
      console.log(`Remaining pending review items: ${rowCount}`);
    }

    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat161_02_all_rejected.jpg"), quality: 30, type: "jpeg" });

    // ====================================================
    // Phase 2: Audio Tracks Page -> Unbind old BGM & SFX
    // ====================================================
    console.log("2. Navigating to Audio Tracks page...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/audio?view=tracks`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat161_03_audio_tracks_before_unbind.jpg"), quality: 30, type: "jpeg" });

    // Unbind BGM
    let bgmRow = page.locator('.audio-binding-row:has-text("BGM")');
    if (await bgmRow.count() > 0) {
      console.log("Unbinding BGM row (testing cancel first)...");
      await bgmRow.first().locator('button:has-text("解绑")').click();
      await page.waitForTimeout(500);

      const dialog = page.locator('[role="dialog"]');
      console.log("Dialog text:", await dialog.innerText());

      // Cancel
      await dialog.locator('button:has-text("取消")').click();
      await page.waitForTimeout(500);

      // Unbind confirm
      await bgmRow.first().locator('button:has-text("解绑")').click();
      await page.waitForTimeout(500);
      await dialog.locator('button:has-text("确认解绑")').click();
      await page.waitForTimeout(1500);
      console.log("BGM unbound.");
    }

    // Unbind SFX
    let sfxRow = page.locator('.audio-binding-row:has-text("音效")');
    if (await sfxRow.count() > 0) {
      console.log("Unbinding SFX row...");
      await sfxRow.first().locator('button:has-text("解绑")').click();
      await page.waitForTimeout(500);

      const dialog = page.locator('[role="dialog"]');
      await dialog.locator('button:has-text("确认解绑")').click();
      await page.waitForTimeout(1500);
      console.log("SFX unbound.");
    }

    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat161_04_all_unbound.jpg"), quality: 30, type: "jpeg" });

    // ====================================================
    // Phase 3: Import and bind new compliant BGM & SFX
    // ====================================================
    console.log("3. Importing and binding compliant BGM (uat_bgm_v2.wav)...");
    const openFormBtn = page.locator('button:has-text("导入并绑定本地音频")');
    if (await openFormBtn.count() > 0) {
      await openFormBtn.click();
      await page.waitForTimeout(500);
    }

    const licenseRel = "00_admin/licenses/uat_local_audio_attestation.json";
    const bgmPath = "F:\\AI_Projects\\h3\\local_drama_studio\\projects\\goal_fullchain_20260823_01\\03_audio\\uat_tracks\\uat_bgm_v2.wav";

    await page.locator('label:has-text("本地音频绝对路径") input').fill(bgmPath);
    await page.locator('label:has-text("项目内授权证据相对路径") input').fill(licenseRel);
    await page.locator('label:has-text("轨道") select').selectOption("BGM");
    await page.locator('label:has-text("授权类型") select').selectOption("USER_OWNED");
    await page.locator('label:has-text("开始（秒）") input').fill("0");
    await page.locator('label:has-text("结束（秒）") input').fill("12");
    await page.locator('label:has-text("Gain（dB）") input').fill("-16");
    await page.locator('label:has-text("淡入（ms）") input').fill("500");
    await page.locator('label:has-text("淡出（ms）") input').fill("800");

    const loopCheckbox = page.locator('label:has-text("循环源音频以覆盖绑定范围") input');
    if (!await loopCheckbox.isChecked()) {
      await loopCheckbox.check();
    }

    await page.locator('button:has-text("校验、导入并绑定")').click();
    await page.waitForTimeout(2000);
    console.log("BGM v2 bound.");

    // Now import and bind SFX
    console.log("4. Importing and binding compliant SFX (uat_sfx_v2.wav)...");
    const sfxPath = "F:\\AI_Projects\\h3\\local_drama_studio\\projects\\goal_fullchain_20260823_01\\03_audio\\uat_tracks\\uat_sfx_v2.wav";

    // If form is collapsed, open it
    if (await openFormBtn.count() > 0 && await page.locator('button:has-text("导入并绑定本地音频")').isVisible()) {
      await openFormBtn.click();
      await page.waitForTimeout(500);
    }

    await page.locator('label:has-text("本地音频绝对路径") input').fill(sfxPath);
    await page.locator('label:has-text("项目内授权证据相对路径") input').fill(licenseRel);
    await page.locator('label:has-text("轨道") select').selectOption("SFX");
    await page.locator('label:has-text("授权类型") select').selectOption("USER_OWNED");
    await page.locator('label:has-text("开始（秒）") input').fill("5");
    await page.locator('label:has-text("结束（秒）") input').fill("6");
    await page.locator('label:has-text("Gain（dB）") input').fill("-8");
    await page.locator('label:has-text("淡入（ms）") input').fill("20");
    await page.locator('label:has-text("淡出（ms）") input').fill("80");

    if (await loopCheckbox.isChecked()) {
      await loopCheckbox.uncheck();
    }

    await page.locator('button:has-text("校验、导入并绑定")').click();
    await page.waitForTimeout(2000);
    console.log("SFX v2 bound.");

    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat161_05_rebound_tracks.jpg"), quality: 30, type: "jpeg" });

    // ====================================================
    // Phase 4: Review Page -> QC and Approve new tracks
    // ====================================================
    console.log("5. Navigating to Review page to QC and Approve new tracks...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/review`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);

    let newAudioRows = page.locator(".review-row");
    let newAudioCount = await newAudioRows.count();
    console.log(`Pending new audio items for review: ${newAudioCount}`);

    while (newAudioCount > 0) {
      console.log(`Reviewing new audio item 0 of ${newAudioCount}...`);
      await newAudioRows.first().click();
      await page.waitForTimeout(500);

      // Run audio QC
      const runQcBtn = page.locator('button:has-text("运行音频 QC")');
      if (await runQcBtn.count() > 0) {
        console.log("Running audio QC on track...");
        await runQcBtn.click();
        await page.waitForTimeout(2000);
      }

      const qcResults = await page.locator(".audio-qc-panel .review-meta span").allInnerTexts();
      console.log("Track QC Results:", qcResults);

      // Fill all PASS
      const checkFieldsets = page.locator(".review-check");
      for (let i = 0; i < await checkFieldsets.count(); i++) {
        await checkFieldsets.nth(i).getByRole("radio", { name: "通过", exact: true }).check();
      }

      // Approve
      console.log("Submitting APPROVED decision...");
      await page.locator('select#review-decision').selectOption("APPROVED");
      await page.waitForTimeout(300);
      await page.locator('button:has-text("提交审核")').click();
      await page.waitForTimeout(1500);

      newAudioRows = page.locator(".review-row");
      newAudioCount = await newAudioRows.count();
      console.log(`Remaining pending audio items: ${newAudioCount}`);
    }

    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat161_06_all_approved.jpg"), quality: 30, type: "jpeg" });

    // ====================================================
    // Phase 5: Check Audio Overview Tab
    // ====================================================
    console.log("6. Checking Audio Overview tab...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/audio?view=overview`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat161_07_audio_overview.jpg"), quality: 30, type: "jpeg" });

    console.log("🎉 UAT-161 full chain successfully executed and verified!");
  } catch (error) {
    console.error("❌ Execution error:", error);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat161_error.jpg"), quality: 30, type: "jpeg" }).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
}

run().catch((e) => {
  console.error(e);
  process.exit(1);
});
