import { test, expect } from "@playwright/test";

test.describe("One-Click Story & Bible Architect Full Workflow", () => {
  test("user can configure, upload/paste novel, and start one-click story and asset bible generation", async ({ page }) => {
    // 1. Visit Home & Projects
    await page.goto("/projects");
    await page.waitForLoadState("domcontentloaded");

    // Click on the first project
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

    // 3. Switch to One-Click Story stage in Story Workspace
    const pipelineRailBtn = page.locator("nav[aria-label='故事工作流']").getByRole("button", { name: /一键生成/ });
    await expect(pipelineRailBtn).toBeVisible({ timeout: 10000 });
    await pipelineRailBtn.click();

    // 4. Verify Workbench elements
    await expect(page.locator("#story-pipeline-heading")).toBeVisible({ timeout: 10000 });

    // If an existing run is shown on the dashboard, click "重新规划新设定" to test the start form
    const configureNewBtn = page.getByRole("button", { name: "重新规划新设定" });
    if (await configureNewBtn.isVisible()) {
      await configureNewBtn.click();
    }

    // Verify source mode tabs
    const uploadTab = page.getByRole("button", { name: /上传本地小说文档/ });
    const existingTab = page.getByRole("button", { name: /选择项目中已有原稿/ });
    const pasteTab = page.getByRole("button", { name: /直接粘贴文本/ });

    await expect(uploadTab).toBeVisible();
    await expect(existingTab).toBeVisible();
    await expect(pasteTab).toBeVisible();

    // Switch to Paste text mode
    await pasteTab.click();
    const textarea = page.locator("#raw-script-textarea");
    await expect(textarea).toBeVisible();

    await textarea.fill("第一场：坠仙谷。林枫握紧手中的九阳神丹，凝视着远方。\n林枫：这一世我定要逆天改命！\n顾清雪：林枫，快拔出斩龙神剑防身。\n第二场：青云宗主殿。楚天极注视着林枫。\n楚天极：你可愿将斩龙神剑献给宗门？");

    // Verify Native AI Model Capability section exists
    await expect(page.getByText(/AI 规划大模型方案/)).toBeVisible();

    // Start generation
    const startBtn = page.getByRole("button", { name: /启动一键故事与圣经全自动建档/ });
    await expect(startBtn).toBeEnabled();
    await startBtn.click();

    // Verify status transition
    await expect(page.locator(".pipeline-progress-box")).toBeVisible({ timeout: 10000 });
  });
});
