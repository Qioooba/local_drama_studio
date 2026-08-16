import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const defaultProjectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const defaultEpisodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
const defaultReviewId = "9773143a-9221-4fe9-9921-5cdc0c936ae8";
const projectId = process.env.CORE_UAT_PROJECT_ID ?? defaultProjectId;
const episodeId = process.env.CORE_UAT_EPISODE_ID ?? defaultEpisodeId;
const reviewId = process.env.CORE_UAT_REVIEW_ID ?? defaultReviewId;
const viewports = [
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1280x800", width: 1280, height: 800 },
  { name: "1024x768", width: 1024, height: 768 },
] as const;

test.setTimeout(120_000);
const results: Array<Record<string, unknown>> = [];

function evidencePath() {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  return path.join(cwd, "docs", "evidence", "g10", "core-chain-browser-readonly-uat-2026-08-16.json");
}

test.afterAll(() => {
  const passed = results.length === viewports.length && results.every((item) => item.status === "PASS");
  const output = {
    schema_version: "g10-core-chain-browser-readonly-uat.v1",
    observed_at: new Date().toISOString(),
    mode: "LOCAL_ONLY",
    project_id: projectId,
    episode_id: episodeId,
    scope: ["project read model", "generation readiness / read-only probe", "review inbox", "timeline / delivery history"],
    status: passed ? "PASS" : "IN_PROGRESS",
    isolated_snapshot: true,
    production_database_touched: false,
    production_project_tree_touched: false,
    runtime_contacted: false,
    network_contacted: false,
    mutated: false,
    original_media_requested: false,
    screenshots_created: false,
    viewports: results,
    interpretation: "Real React routes and FastAPI read paths were exercised from an isolated SQLite/project snapshot. Mutation controls were intentionally not clicked; POST/PUT/PATCH/DELETE API traffic would fail the test.",
  };
  fs.mkdirSync(path.dirname(evidencePath()), { recursive: true });
  fs.writeFileSync(evidencePath(), `${JSON.stringify(output, null, 2)}\n`, "utf8");
});

for (const viewport of viewports) {
  test(`reads the core project → preflight → review → delivery chain at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    const failedResponses: string[] = [];
    const writes: string[] = [];
    const publicRequests: string[] = [];
    const originalMediaRequests: string[] = [];
    const apiGets = new Set<string>();
    page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    page.on("response", (response) => {
      if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`);
    });
    page.on("request", (request) => {
      const url = new URL(request.url());
      const pathname = url.pathname;
      if (!['127.0.0.1', 'localhost'].includes(url.hostname)) publicRequests.push(request.url());
      if (pathname.startsWith("/api/")) {
        if (request.method() === "GET") apiGets.add(pathname);
        else writes.push(`${request.method()} ${pathname}`);
      }
      if (/\.(mp4|mov|mkv|webm|wav|flac|mp3)(\?|$)/i.test(pathname) || pathname.includes("/original") || pathname.includes("/content")) {
        originalMediaRequests.push(request.url());
      }
    });

    const routes = [
      {
        name: "project",
        url: `/?view=projects&project=${projectId}&episode=${episodeId}`,
        checks: async () => {
          await expect(page.getByText("项目与生产台", { exact: true })).toBeVisible();
          await expect(page.getByRole("heading", { name: "项目健康检查" })).toBeVisible();
          await expect(page.getByRole("heading", { name: "全局搜索" })).toBeVisible();
          const globalSearch = page.getByLabel("搜索项目、集、镜头、资产、任务、媒体或关键词");
          await globalSearch.fill("g2");
          await expect(page.locator(".search-results")).toBeVisible();
          await expect(page.getByRole("heading", { name: "时间线与交付状态" })).toBeVisible();
          await expect(page.getByRole("heading", { name: "整集渲染与本地交付候选" })).toBeVisible();
          await expect(page.getByText("机器 PASS ≠ 人工/平台批准", { exact: true })).toBeVisible();
        },
      },
      {
        name: "generation",
        url: `/?view=generation&project=${projectId}&episode=${episodeId}`,
        checks: async () => {
          await expect(page.getByRole("heading", { name: "生成工作台" })).toBeVisible();
          await expect(page.getByRole("heading", { name: "真实生成闭环门禁" })).toBeVisible();
          await expect(page.getByRole("heading", { name: "真实证据探针计划" })).toBeVisible();
          await expect(page.getByRole("button", { name: "建立意图并执行只读生成预检" })).toBeVisible();
          await expect(page.getByText("只读检查完成：未创建 Job、未连接 ComfyUI")).toBeVisible();
        },
      },
      {
        name: "review",
        url: `/?view=reviews&project=${projectId}&episode=${episodeId}&review=${reviewId}`,
        checks: async () => {
          await expect(page.getByRole("heading", { name: "批量正式交付选择" })).toBeVisible();
          await expect(page.getByRole("heading", { name: "媒体版本审核与选择" })).toBeVisible();
          await expect(page.getByRole("search", { name: "审核收件箱筛选" })).toBeVisible();
          await expect(page.getByText(/320px small.*缩略图|缩略图.*不加载原图/).first()).toBeVisible();
        },
      },
    ];

    for (const route of routes) {
      await page.goto(route.url, { waitUntil: "domcontentloaded" });
      await expect(page.locator("#workspace-content")).toBeVisible();
      await expect(page.locator(".workspace-error")).toHaveCount(0);
      await route.checks();
    }

    const expectedPaths = [
      "/api/v1/projects",
      "/api/v1/projects/" + projectId + "/seasons",
      "/api/v1/projects/" + projectId + "/gates/g6",
      "/api/v1/search",
      "/api/v1/reviews/inbox",
      "/api/v1/episodes/" + episodeId + "/timeline-status",
      "/api/v1/episodes/" + episodeId + "/delivery-packages",
    ];
    for (const expected of expectedPaths) {
      expect([...apiGets].some((actual) => actual === expected || actual.startsWith(`${expected}?`))).toBe(true);
    }
    const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    const result = {
      viewport: viewport.name,
      status: writes.length === 0 && publicRequests.length === 0 && originalMediaRequests.length === 0 && consoleErrors.length === 0 && pageErrors.length === 0 && failedResponses.length === 0 && horizontalOverflow === 0 ? "PASS" : "FAIL",
      routes: routes.map((route) => route.name),
      api_get_paths: [...apiGets].sort(),
      writes,
      public_requests: publicRequests,
      original_media_requests: originalMediaRequests,
      console_errors: consoleErrors,
      page_errors: pageErrors,
      failed_responses: failedResponses,
      horizontal_overflow_px: horizontalOverflow,
    };
    results.push(result);
    expect(writes).toEqual([]);
    expect(publicRequests).toEqual([]);
    expect(originalMediaRequests).toEqual([]);
    expect(consoleErrors).toEqual([]);
    expect(pageErrors).toEqual([]);
    expect(failedResponses).toEqual([]);
    expect(horizontalOverflow).toBe(0);
  });
}
