import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const PROJECT_ID = "04710fb9-3a9e-44c0-aa2f-1e8485b26f72";
const EPISODE_ID = "1031eec1-784a-408a-969e-010536931d59";
const BASE_URL = "http://127.0.0.1:5173";
const EVIDENCE_DIR = "F:\\AI_Projects\\h3\\local_drama_studio\\docs\\evidence\\ui-uat-2026-08-22\\screens";

fs.mkdirSync(EVIDENCE_DIR, { recursive: true });

async function run() {
  console.log("🚀 Starting Phase 1 Full-Scope Control Matrix verification in real browser...");
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();

  try {
    // ====================================================
    // Test 1: Invalid IDs Graceful Fallback
    // ====================================================
    console.log("1. Testing invalid project / episode / shot IDs fallback...");
    await page.goto(`${BASE_URL}/projects/invalid-project-id-9999/story`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_01_invalid_project_fallback.jpg"), quality: 30, type: "jpeg" });

    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/invalid-episode-id-9999/plan`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_02_invalid_episode_fallback.jpg"), quality: 30, type: "jpeg" });

    // ====================================================
    // Test 2: System Diagnostics & Tabs
    // ====================================================
    console.log("2. Testing Diagnostics controls & tabs...");
    await page.goto(`${BASE_URL}/diagnostics?view=env`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    const runBtn = page.locator('button:has-text("运行诊断")');
    if (await runBtn.count() > 0) {
      await runBtn.click();
      await page.waitForTimeout(1500);
    }
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_03_diagnostics_env.jpg"), quality: 30, type: "jpeg" });

    await page.goto(`${BASE_URL}/diagnostics?view=audit`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_04_diagnostics_audit.jpg"), quality: 30, type: "jpeg" });

    await page.goto(`${BASE_URL}/diagnostics?view=search`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_05_diagnostics_search.jpg"), quality: 30, type: "jpeg" });

    // ====================================================
    // Test 3: Models & Capabilities Tabs
    // ====================================================
    console.log("3. Testing Models & Capabilities tabs...");
    await page.goto(`${BASE_URL}/models?view=profile-contracts`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_06_models_contracts.jpg"), quality: 30, type: "jpeg" });

    await page.goto(`${BASE_URL}/models?view=workflows`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_07_models_workflows.jpg"), quality: 30, type: "jpeg" });

    await page.goto(`${BASE_URL}/models?view=preferences`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_08_models_preferences.jpg"), quality: 30, type: "jpeg" });

    await page.goto(`${BASE_URL}/models?view=compatibility&project=${PROJECT_ID}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_09_models_compatibility.jpg"), quality: 30, type: "jpeg" });

    await page.goto(`${BASE_URL}/models?view=local-llm`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_10_models_localllm.jpg"), quality: 30, type: "jpeg" });

    // ====================================================
    // Test 4: QC Policies & Director Recipes
    // ====================================================
    console.log("4. Testing QC Policies & Director Recipes...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/qc-policies`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_11_qc_policies.jpg"), quality: 30, type: "jpeg" });

    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/director-recipes`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_12_director_recipes.jpg"), quality: 30, type: "jpeg" });

    // ====================================================
    // Test 5: Asset Bible
    // ====================================================
    console.log("5. Testing Asset Bible category switching...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/assets`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_13_assets_initial.jpg"), quality: 30, type: "jpeg" });

    // ====================================================
    // Test 6: Director Desk Navigation, Drawers & Shortcuts
    // ====================================================
    console.log("6. Testing Director Desk navigation & shortcuts...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/direct`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_14_director_initial.jpg"), quality: 30, type: "jpeg" });

    // Press 'N' to toggle Shot Navigator drawer
    await page.keyboard.press("KeyN");
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_15_director_nav_drawer.jpg"), quality: 30, type: "jpeg" });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);

    // Press 'I' to toggle Inspector drawer
    await page.keyboard.press("KeyI");
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_16_director_inspector_drawer.jpg"), quality: 30, type: "jpeg" });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);

    // ====================================================
    // Test 7: Episode Production Run (G7 Workspace)
    // ====================================================
    console.log("7. Testing Episode Production Run workspace...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/run`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_17_episode_run_initial.jpg"), quality: 30, type: "jpeg" });

    // ====================================================
    // Test 8: Jobs Panel & Operations Page
    // ====================================================
    console.log("8. Testing Jobs Panel & Project Operations...");
    await page.goto(`${BASE_URL}/jobs?project=${PROJECT_ID}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_18_jobs_panel.jpg"), quality: 30, type: "jpeg" });

    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/operations`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_19_operations_page.jpg"), quality: 30, type: "jpeg" });

    // ====================================================
    // Test 9: Canvas & Media Lab
    // ====================================================
    console.log("9. Testing Canvas & Media Lab...");
    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/canvas?episode=${EPISODE_ID}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_20_canvas_page.jpg"), quality: 30, type: "jpeg" });

    await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/lab`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_21_media_lab.jpg"), quality: 30, type: "jpeg" });

    console.log("🎉 Phase 1 Full-Scope Control Matrix verification finished successfully!");
  } catch (error) {
    console.error("❌ Execution error:", error);
    await page.screenshot({ path: path.join(EVIDENCE_DIR, "phase1_error.jpg"), quality: 30, type: "jpeg" }).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
}

run().catch((e) => {
  console.error(e);
  process.exit(1);
});
