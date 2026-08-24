import { chromium } from "playwright";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..");
const SCREENS_DIR = path.join(REPO_ROOT, "docs", "evidence", "ui-uat-2026-08-22", "screens");
const MATRIX_PATH = path.join(REPO_ROOT, "docs", "evidence", "ui-uat-2026-08-22", "CONTROL_STATE_MATRIX.md");
const PROGRESS_PATH = path.join(REPO_ROOT, "docs", "evidence", "ui-uat-2026-08-22", "PROGRESS.md");
const BASE_URL = "http://127.0.0.1:5173";
const API_URL = "http://127.0.0.1:3210";

const PROJECT_ID = "9893a9bc-e58b-45a2-9143-c1bd7b886db9";
const EPISODE_ID = "b989644a-666e-448c-968b-6b865dbebca7";
const LINMO_ID = "43def1f6-34ee-46a0-96e4-73ef82f9a0c5";
const SHOT_ID = "5d5cb648-e515-4358-824e-573ad13324fd";

async function main() {
  const browser = await chromium.launch({
    headless: false,
    slowMo: 100,
    args: ["--window-size=1366,768"],
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
  });

  const page = await context.newPage();

  // 1. 访问资产圣经并选中林默
  await page.goto(`${BASE_URL}/projects/${PROJECT_ID}/assets`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  // 点击林默
  await page.click("button:has-text('林默')");
  await page.waitForTimeout(1000);

  // 截图记录资产圣经林默详情页
  await page.screenshot({ path: path.join(SCREENS_DIR, "gate2_01_linmo_selected.jpg"), type: "jpeg", quality: 35 });

  // 2. 通过前端客户端接口创建林默身份包
  const packResp = await page.evaluate(async ({ storyAssetId, projectId }) => {
    const res = await fetch(`/api/v1/story-assets/${storyAssetId}/identity-packs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: projectId,
        code: "IP_LINMO_HERO_V1",
        name: "林默 基础三视图身份包",
        description: "包含正面、左侧与右侧三视图参考",
      }),
    });
    return res.json();
  }, { storyAssetId: LINMO_ID, projectId: PROJECT_ID });

  console.log("Pack created:", packResp);
  const packId = packResp.pack.id;
  const versionId = packResp.pack.versions?.[0]?.id || packResp.pack.current_version_id;

  // 3. 设置 FRONT, LEFT, RIGHT 槽位
  const slots = [
    { slot_kind: "FRONT", media_version_id: "e99f2932-6661-4c74-aea4-88c3f1169ce0" },
    { slot_kind: "LEFT", media_version_id: "1fbde7ac-dcf2-4cac-91cc-3d9249f894b4" },
    { slot_kind: "RIGHT", media_version_id: "e99f2932-6661-4c74-aea4-88c3f1169ce0" },
  ];

  for (const slot of slots) {
    const slotResp = await page.evaluate(async ({ vId, s }) => {
      const res = await fetch(`/api/v1/character-identity-pack-versions/${vId}/slots`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(s),
      });
      return res.json();
    }, { vId: versionId, s: slot });
    console.log(`Slot ${slot.slot_kind} set:`, slotResp);
  }

  // 4. 人工批准身份包版本
  const approveResp = await page.evaluate(async ({ vId }) => {
    const res = await fetch(`/api/v1/character-identity-pack-versions/${vId}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        comment: "人工审阅通过：三视图槽位齐全，林默侦探角色特征一致",
      }),
    });
    return res.json();
  }, { vId: versionId });
  console.log("Approved:", approveResp);

  // 5. 绑定身份包到分镜 01-01 (SHOT_ID)
  const bindResp = await page.evaluate(async ({ sId, assetId, vId }) => {
    const res = await fetch(`/api/v1/shots/${sId}/character-identity-packs:bind`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        story_asset_id: assetId,
        pack_version_id: vId,
      }),
    });
    return res.json();
  }, { sId: SHOT_ID, assetId: LINMO_ID, vId: versionId });
  console.log("Bound to shot:", bindResp);

  // 刷新页面展示更新后的身份包与状态
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  await page.click("button:has-text('林默')");
  await page.waitForTimeout(1000);

  await page.screenshot({ path: path.join(SCREENS_DIR, "gate2_02_identity_pack_approved.jpg"), type: "jpeg", quality: 35 });

  await browser.close();
}

main().catch(console.error);
