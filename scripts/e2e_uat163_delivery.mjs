import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const PROJECT_ID = "04710fb9-3a9e-44c0-aa2f-1e8485b26f72";
const EPISODE_ID = "1031eec1-784a-408a-969e-010536931d59";
const BASE_URL = "http://127.0.0.1:5173";
const EVIDENCE_DIR = "F:\\AI_Projects\\h3\\local_drama_studio\\docs\\evidence\\ui-uat-2026-08-22\\screens";

fs.mkdirSync(EVIDENCE_DIR, { recursive: true });

async function run() {
  console.log("🚀 Starting UAT-163 Delivery Workflow real browser verification...");
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();

  try {
    // ====================================================
    // Phase 1: Set Selected Delivery Target & Compliance Policy in Settings
    // ====================================================
    console.log("1. Navigating to Project Settings delivery tab...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/production-settings?view=delivery`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat163_01_settings_initial.jpg"), quality: 30, type: "jpeg" });

    // Select delivery target DOUYIN_VERTICAL
    const targetSelect = page.locator('select[aria-label="交付目标版本"]');
    if (await targetSelect.count() > 0) {
      console.log("Selecting delivery target DOUYIN_VERTICAL...");
      await targetSelect.selectOption({ index: 1 });
      await page.waitForTimeout(300);

      await page.locator('button:has-text("选择为当前交付目标")').click();
      await page.waitForTimeout(1500);

      const targetMsg = page.locator('.delivery-target-editor .review-success');
      if (await targetMsg.count() > 0) {
        console.log(`Target selection result: ${await targetMsg.innerText()}`);
      }
    }

    // Publish updated CompliancePolicy with 180s max duration
    console.log("Publishing compliant CompliancePolicy (180000ms max duration)...");
    const durationInput = page.locator('.brand-kit-panel label:has-text("最长时长") input');
    if (await durationInput.count() > 0) {
      await durationInput.fill("180000");
      await page.waitForTimeout(300);

      await page.locator('button:has-text("发布 CompliancePolicy 新版本")').click();
      await page.waitForTimeout(1500);

      const complianceMsg = page.locator('.brand-kit-panel .review-success');
      if (await complianceMsg.count() > 0) {
        console.log(`Compliance policy result: ${await complianceMsg.innerText()}`);
      }
    }
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat163_02_target_and_compliance.jpg"), quality: 30, type: "jpeg" });

    // ====================================================
    // Phase 2: Delivery Step 1 - Preflight & Compose Render
    // ====================================================
    console.log("2. Navigating to Delivery page (Step 1 Preflight)...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/delivery?view=preflight`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat163_03_delivery_preflight.jpg"), quality: 30, type: "jpeg" });

    // Click "继续到合成候选"
    console.log("Clicking '继续到合成候选'...");
    await page.locator('button:has-text("继续到合成候选")').click();
    await page.waitForTimeout(1000);

    // Step 2 (Compose): Register render
    console.log("3. Delivery Step 2 (Compose) - Registering episode render...");
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat163_04_delivery_compose.jpg"), quality: 30, type: "jpeg" });

    await page.locator('button:has-text("登记整集渲染")').click();
    await page.waitForTimeout(4000);
    let renderSuccess = page.locator('.delivery-workflow-panel .review-success');
    if (await renderSuccess.count() > 0) {
      console.log(`Render registration: ${await renderSuccess.innerText()}`);
    }

    // ====================================================
    // Phase 3: Human Review of Episode Render Version
    // ====================================================
    console.log("4. Navigating to Episode Review -> Render 审核 tab...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/review?view=render`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat163_05_render_review_initial.jpg"), quality: 30, type: "jpeg" });

    // Fill all required review checks with "PASS" (通过)
    const checkFieldsets = page.locator('.episode-review-panel .review-check');
    const checkCount = await checkFieldsets.count();
    console.log(`Render review checklist items count: ${checkCount}`);
    for (let i = 0; i < checkCount; i++) {
      const passRadio = checkFieldsets.nth(i).locator('input[type="radio"]').first();
      await passRadio.check();
      await page.waitForTimeout(100);
    }

    // Ensure decision is APPROVED
    await page.locator('.episode-review-panel select').selectOption("APPROVED");
    await page.locator('.episode-review-panel textarea').fill("整集 38 镜画面对齐、字幕无重叠、音画同步、电平合格、节奏流畅。");
    await page.waitForTimeout(300);

    // Click "提交整集审核"
    console.log("Submitting Episode Render Review (APPROVED)...");
    await page.locator('button:has-text("提交整集审核")').click();
    await page.waitForTimeout(1500);

    const renderReviewMsg = page.locator('.episode-review-panel .review-success');
    if (await renderReviewMsg.count() > 0) {
      console.log(`Render review result: ${await renderReviewMsg.innerText()}`);
    }
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat163_06_render_approved.jpg"), quality: 30, type: "jpeg" });

    // ====================================================
    // Phase 4: Delivery Step 2 - Build Delivery Package
    // ====================================================
    console.log("5. Navigating back to Delivery Compose step to build candidate...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/delivery?view=compose`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);

    console.log("Clicking '创建交付候选'...");
    const buildBtn = page.locator('button:has-text("创建交付候选")');
    await buildBtn.click();
    await page.waitForTimeout(4000);
    let buildSuccess = page.locator('.delivery-workflow-panel .review-success');
    if (await buildSuccess.count() > 0) {
      console.log(`Build candidate result: ${await buildSuccess.innerText()}`);
    }
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat163_07_candidate_built.jpg"), quality: 30, type: "jpeg" });

    // Click "下一步：审核证据 →"
    console.log("Navigating to Step 3 (Review)...");
    await page.locator('button:has-text("下一步：审核证据 →")').click();
    await page.waitForTimeout(1000);

    // ====================================================
    // Phase 5: Delivery Step 3 - Verify & Record Approvals
    // ====================================================
    console.log("6. Delivery Step 3 (Review)...");
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat163_08_delivery_review_step.jpg"), quality: 30, type: "jpeg" });

    // Click "验证 manifest / SHA"
    console.log("Clicking '验证 manifest / SHA'...");
    await page.locator('button:has-text("验证 manifest / SHA")').click();
    await page.waitForTimeout(2000);
    let verifySuccess = page.locator('.delivery-workflow-panel .review-success');
    if (await verifySuccess.count() > 0) {
      console.log(`Verify result: ${await verifySuccess.innerText()}`);
    }

    // Click "记录人工批准"
    console.log("Recording Human Approval on Delivery Package...");
    await page.locator('button:has-text("记录人工批准")').click();
    await page.waitForTimeout(500);

    let noteBox = page.locator('.inline-note-box');
    await noteBox.locator('textarea').fill("人工复核：画音字完整对齐，电平与时长符合广播交付标准。");
    await noteBox.locator('button:has-text("记录审核")').click();
    await page.waitForTimeout(1500);
    console.log("Human approval recorded.");

    // Click "记录平台批准"
    console.log("Recording Platform Approval on Delivery Package...");
    await page.locator('button:has-text("记录平台批准")').click();
    await page.waitForTimeout(500);

    noteBox = page.locator('.inline-note-box');
    await noteBox.locator('textarea').fill("平台预检：抖音竖屏视频编码与规格验证通过。");
    await noteBox.locator('button:has-text("记录审核")').click();
    await page.waitForTimeout(1500);
    console.log("Platform approval recorded.");

    // Test package withdrawal
    console.log("Testing package withdrawal and immutable audit trace...");
    await page.locator('button:has-text("撤回交付包")').click();
    await page.waitForTimeout(500);

    noteBox = page.locator('.inline-note-box');
    await noteBox.locator('textarea').fill("UAT验证：测试交付包不可变撤回操作与事件历史记录。");
    await noteBox.locator('button:has-text("确认撤回")').click();
    await page.waitForTimeout(1500);
    console.log("Withdrawal executed.");

    // Re-create final approved package
    console.log("Re-creating final active delivery package...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/delivery?view=compose`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);

    await page.locator('button:has-text("创建交付候选")').click();
    await page.waitForTimeout(3000);

    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/delivery?view=review`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);

    await page.locator('button:has-text("验证 manifest / SHA")').click();
    await page.waitForTimeout(2000);

    await page.locator('button:has-text("记录人工批准")').click();
    await page.waitForTimeout(500);
    noteBox = page.locator('.inline-note-box');
    await noteBox.locator('textarea').fill("最终交付版本人工全量核验通过。");
    await noteBox.locator('button:has-text("记录审核")').click();
    await page.waitForTimeout(1500);

    await page.locator('button:has-text("记录平台批准")').click();
    await page.waitForTimeout(500);
    noteBox = page.locator('.inline-note-box');
    await noteBox.locator('textarea').fill("最终交付版本平台规范核验通过。");
    await noteBox.locator('button:has-text("记录审核")').click();
    await page.waitForTimeout(1500);

    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat163_09_final_approved_package.jpg"), quality: 30, type: "jpeg" });

    // Click "下一步：打包交付 →"
    console.log("Navigating to Step 4 (Package)...");
    await page.locator('button:has-text("下一步：打包交付 →")').click();
    await page.waitForTimeout(1000);

    // ====================================================
    // Phase 6: Delivery Step 4 - Package & Local Tools
    // ====================================================
    console.log("7. Delivery Step 4 (Package & Local Tools)...");
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat163_10_package_step.jpg"), quality: 30, type: "jpeg" });

    // Test Contact Sheet export
    console.log("Testing Episode Contact Sheet export...");
    const contactSheetBtn = page.locator('button:has-text("导出联系表")');
    if (await contactSheetBtn.count() > 0) {
      await contactSheetBtn.click();
      await page.waitForTimeout(2000);
      const csResult = page.locator('.contact-sheet-action .review-success, .panel p[role="status"]');
      if (await csResult.count() > 0) {
        console.log(`Contact sheet export result: ${await csResult.innerText()}`);
      }
    }

    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat163_11_contact_sheet_exported.jpg"), quality: 30, type: "jpeg" });

    // Verify Delivery History Table
    const tableRows = page.locator('.delivery-workspace-v2 tbody tr');
    const rowCount = await tableRows.count();
    console.log(`Delivery packages in history table: ${rowCount}`);
    for (let i = 0; i < rowCount; i++) {
      const rowText = await tableRows.nth(i).innerText();
      console.log(`History row ${i}: ${rowText.replace(/\n/g, " | ")}`);
    }

    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat163_12_delivery_history_table.jpg"), quality: 30, type: "jpeg" });

    console.log("🎉 UAT-163 Delivery Workflow verification completed successfully!");
  } catch (error) {
    console.error("❌ Execution error:", error);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "uat163_error.jpg"), quality: 30, type: "jpeg" }).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
}

run().catch((e) => {
  console.error(e);
  process.exit(1);
});
