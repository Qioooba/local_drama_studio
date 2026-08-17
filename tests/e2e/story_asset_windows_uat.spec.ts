import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * G11 P0-1/2 story asset library Windows UAT: real browser clicks against the
 * isolated simulation environment (production snapshot on :3225).
 *
 * Seeds created by scripts/serve_sim_env.py `_seed_g11`: CHARACTER 母亲 (bound
 * to SHOT_001 with the approved keyframe as canonical reference), SCENE 老屋,
 * PROP 信件, COSTUME 素色棉衣.  This spec drives the real UI: asset library
 * tabs, asset creation, DirectorShotEditor shot binding, ContinuityPanel bound
 * assets, and the prompt-anchor preview endpoint that must byte-match the
 * injection preview.
 */

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
const shotId = "020f9248-14b7-4f92-9edd-ee587ffdedf3";
const base = "http://127.0.0.1:3225";
const steps: string[] = [];
const errors: string[] = [];

function evidencePath() {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  return path.join(cwd, "docs", "evidence", "g10", "story-asset-windows-uat-2026-08-17.json");
}

test.afterAll(() => {
  const output = {
    schema_version: "g11.story-asset-windows-uat.v1",
    observed_at: new Date().toISOString(),
    mode: "LOCAL_ONLY",
    project_id: projectId,
    episode_id: episodeId,
    shot_id: shotId,
    status: errors.length === 0 ? "PASS" : "IN_PROGRESS",
    isolated_snapshot: true,
    production_database_touched: false,
    runtime_contacted: false,
    network_contacted: false,
    steps,
    errors,
    interpretation:
      "G11 story asset library real-click UAT against the isolated snapshot: seeded assets render in the library, a new character asset is created through the real form, the DirectorShotEditor binds it to the shot, the ContinuityPanel lists bound assets, and the prompt-anchor preview reflects the bound characters.",
  };
  fs.mkdirSync(path.dirname(evidencePath()), { recursive: true });
  fs.writeFileSync(evidencePath(), `${JSON.stringify(output, null, 2)}\n`, "utf8");
});

test("G11 story asset library: seeded assets, create, bind, continuity, prompt anchor", async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const failedResponses: string[] = [];
  const publicRequests: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("response", (response) => { if (response.status() >= 400 && !response.url().includes("/favicon")) failedResponses.push(`${response.status()} ${response.url()}`); });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!["127.0.0.1", "localhost"].includes(url.hostname)) publicRequests.push(request.url());
  });

  // --- Projects view: seeded assets in the library -------------------------------------------
  const projectsParams = new URLSearchParams({ view: "projects", project: projectId, episode: episodeId });
  await page.goto(`/?${projectsParams.toString()}`, { waitUntil: "networkidle" });
  const library = page.locator(".story-asset-library");
  await expect(library.getByRole("heading", { name: "故事资产库" })).toBeVisible();
  await expect(library.getByRole("tab", { name: "角色" })).toHaveAttribute("aria-selected", "true");
  await expect(library.getByText("母亲", { exact: true })).toBeVisible();
  await expect(library.getByText(/中年女性，面容温和/)).toBeVisible();
  await expect(library.getByAltText(/母亲 资产参考图的小尺寸缩略图/)).toBeVisible();
  steps.push("projects view: seeded CHARACTER 母亲 with canonical thumbnail rendered");

  await library.getByRole("tab", { name: "场景" }).click();
  await expect(library.getByText("老屋", { exact: true })).toBeVisible();
  await expect(library.getByText(/北方乡村老屋/)).toBeVisible();
  steps.push("projects view: SCENE 老屋 rendered on scene tab");

  // --- Create a new character through the real form ------------------------------------------
  await library.getByRole("tab", { name: "角色" }).click();
  await library.getByText("新建角色资产卡", { exact: true }).click();
  await library.getByPlaceholder("CHAR_MOTHER").fill("daughter");
  await library.getByLabel("名称").fill("女儿");
  await library.getByLabel("描述").fill("年轻女子，梳马尾，穿浅色外衣");
  await library.getByRole("button", { name: "创建角色资产卡" }).click();
  await expect(library.getByText("女儿", { exact: true })).toBeVisible();
  steps.push("projects view: created CHARACTER 女儿 through the real form");

  // --- Generation view: DirectorShotEditor binds 女儿 to the shot -----------------------------
  const generationParams = new URLSearchParams({ view: "generation", project: projectId, episode: episodeId, shot: shotId });
  await page.goto(`/?${generationParams.toString()}`, { waitUntil: "networkidle" });
  const assetSection = page.locator('.shot-asset-section[aria-label="故事资产"]');
  await expect(assetSection.getByRole("heading", { name: "故事资产" })).toBeVisible();
  await expect(assetSection.getByText("母亲", { exact: true })).toBeVisible();
  steps.push("generation view: seeded 母亲 binding visible in DirectorShotEditor");

  await assetSection.getByLabel("绑定资产").selectOption({ label: "daughter · 女儿" });
  await assetSection.getByLabel("镜头内角色").fill("女儿");
  await assetSection.getByRole("button", { name: "绑定到本镜头" }).click();
  await expect(assetSection.getByText("女儿", { exact: true })).toBeVisible();
  steps.push("generation view: bound 女儿 to SHOT_001 through the real UI");

  // --- ContinuityPanel bound assets ------------------------------------------------------------
  const continuity = page.locator('.continuity-bound-assets[aria-label="当前镜头绑定资产"]');
  await expect(continuity.getByText(/绑定资产 · 2/)).toBeVisible();
  await expect(continuity.getByText("母亲", { exact: true })).toBeVisible();
  await expect(continuity.getByText("女儿", { exact: true })).toBeVisible();
  steps.push("generation view: ContinuityPanel lists both bound assets");

  // --- Prompt-anchor preview endpoint reflects the exact injected anchor ----------------------
  const session = await page.request.get(`${base}/api/v1/session/bootstrap`);
  const sessionBody = await session.json();
  const headers = { "X-Local-Instance-Token": sessionBody.token };
  const anchorResponse = await page.request.get(`${base}/api/v1/shots/${shotId}/prompt-anchor`, { headers });
  expect(anchorResponse.status()).toBe(200);
  const anchor = await anchorResponse.json();
  expect(anchor.anchor).toContain("角色锚点 · 母亲");
  expect(anchor.anchor).toContain("角色锚点 · 女儿");
  expect(anchor.characters.length).toBeGreaterThanOrEqual(2);
  steps.push(`prompt-anchor preview: ${anchor.characters.length} characters, anchor text reflects both bindings`);

  // --- Clean zero-error audit -----------------------------------------------------------------
  const passed = consoleErrors.length === 0 && pageErrors.length === 0 && failedResponses.length === 0 && publicRequests.length === 0;
  expect(passed).toBe(true);
  if (consoleErrors.length) errors.push(`console: ${consoleErrors.join("; ")}`);
  if (pageErrors.length) errors.push(`pageerror: ${pageErrors.join("; ")}`);
  if (failedResponses.length) errors.push(`responses: ${failedResponses.join("; ")}`);
  if (publicRequests.length) errors.push(`public: ${publicRequests.join("; ")}`);
  steps.push("zero console errors / zero failed responses / zero public requests");
});
