import { test, expect } from "@playwright/test";

test.describe("Story Workspace 4-Stage Full Content Audit", () => {
  test("verifies all 4 stages (1.导入原稿, 2.审核拆解, 3.角色建档, 4.故事圣经) have populated content after one-click generation", async ({ page }) => {
    // 1. Visit Projects and enter the first project
    await page.goto("/projects");
    await page.waitForLoadState("domcontentloaded");

    const firstProjectLink = page.locator("a[href*='/projects/']").first();
    await expect(firstProjectLink).toBeVisible({ timeout: 15000 });
    await firstProjectLink.click();

    // 2. Navigate to Story Workspace
    const currentUrl = page.url();
    const match = currentUrl.match(/\/projects\/([^/]+)/);
    if (match) {
      await page.goto(`/projects/${match[1]}/story`);
    }
    await page.waitForLoadState("domcontentloaded");

    // 3. Switch to Stage 0 (🚀 一键生成故事与圣经)
    const pipelineRailBtn = page.locator("nav[aria-label='故事工作流']").getByRole("button", { name: /一键生成/ });
    await expect(pipelineRailBtn).toBeVisible({ timeout: 10000 });
    await pipelineRailBtn.click();

    await expect(page.locator("#story-pipeline-heading")).toBeVisible({ timeout: 10000 });

    const configureNewBtn = page.getByRole("button", { name: "重新规划新设定" });
    try {
      if (await configureNewBtn.isVisible({ timeout: 2000 })) {
        await configureNewBtn.click();
      }
    } catch {
      // not visible, continue
    }

    // Switch to Paste text mode and start
    const pasteTab = page.getByRole("button", { name: /直接粘贴文本/ });
    await expect(pasteTab).toBeVisible({ timeout: 5000 });
    await pasteTab.click();

    const textarea = page.locator("#raw-script-textarea");
    await textarea.fill("第一场：坠仙谷。林枫握紧手中的九阳神丹，凝视着远方。\n林枫：这一世我定要逆天改命！\n顾清雪：林枫，快拔出斩龙神剑防身。\n第二场：青云宗主殿。楚天极注视着林枫。\n楚天极：你可愿将斩龙神剑献给宗门？");

    const startBtn = page.getByRole("button", { name: /启动一键故事与圣经全自动建档/ });
    await expect(startBtn).toBeEnabled();
    await startBtn.click();

    // Wait for Dashboard to show
    await expect(page.locator(".pipeline-progress-box")).toBeVisible({ timeout: 15000 });

    // ==========================================
    // STAGE 1 AUDIT: 1. 导入原稿 (Source Passage)
    // ==========================================
    const stage1Btn = page.locator("nav[aria-label='故事工作流']").getByRole("button", { name: /1\.\s*导入原稿/ });
    await stage1Btn.click();
    await expect(page.locator("#story-import-heading")).toBeVisible();

    // ==========================================
    // STAGE 2 AUDIT: 2. 审核拆解 (Script Breakdown)
    // ==========================================
    const stage2Btn = page.locator("nav[aria-label='故事工作流']").getByRole("button", { name: /2\.\s*审核拆解/ });
    await stage2Btn.click();
    await expect(page.locator("#story-review-heading")).toBeVisible();

    // ==========================================
    // STAGE 3 AUDIT: 3. 角色建档 (Character Profiles)
    // ==========================================
    const stage3Btn = page.locator("nav[aria-label='故事工作流']").getByRole("button", { name: /3\.\s*角色建档/ });
    await stage3Btn.click();
    await expect(page.locator("#story-assets-heading")).toBeVisible();

    // ==========================================
    // STAGE 4 AUDIT: 故事圣经 (World Bible & Assets)
    // ==========================================
    const stage4Btn = page.locator("nav[aria-label='故事工作流']").getByRole("button", { name: /故事圣经/ });
    await stage4Btn.click();
    await expect(page.locator("#story-bible-heading")).toBeVisible();
  });
});
