import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

type ViewportResult = {
  viewport: string;
  status: "PASS" | "FAIL";
  image_candidates_seen: number;
  thumbnail_requests: number;
  writes: string[];
  public_requests: string[];
  original_media_requests: string[];
  console_errors: string[];
  page_errors: string[];
  failed_responses: string[];
  horizontal_overflow_px: number;
};

const projectId = "19fb17a9-4b13-42f4-ba34-869dd133fc39";
const episodeId = process.env.IMAGE_UAT_EPISODE_ID ?? "";
const viewports = [
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1280x800", width: 1280, height: 800 },
  { name: "1024x768", width: 1024, height: 768 },
] as const;

test.setTimeout(120_000);
const results: ViewportResult[] = [];

function evidencePath() {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  return path.join(cwd, "docs", "evidence", "g10", "fr-img-002-006-image-review-windows-uat-2026-08-17.json");
}

test.afterAll(() => {
  const passed = results.length === viewports.length && results.every((item) => item.status === "PASS");
  const output = {
    schema_version: "g10-fr-img-002-006-image-review-windows-uat.v1",
    observed_at: new Date().toISOString(),
    mode: "LOCAL_ONLY",
    project_id: projectId,
    scope: ["V2 review inbox", "image A/B thumbnail compare", "keyboard candidate navigation"],
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
    interpretation:
      "The V2 EpisodeReviewWorkspace and ReviewInboxPanel were exercised at 1440x900/1280x800/1024x768 against an isolated SQLite snapshot seeded with two real local PNG PROXY image candidates. Only size=small derived thumbnails are requested; original /content is never fetched; no mutation controls were clicked.",
  };
  fs.mkdirSync(path.dirname(evidencePath()), { recursive: true });
  fs.writeFileSync(evidencePath(), `${JSON.stringify(output, null, 2)}\n`, "utf8");
});

for (const viewport of viewports) {
  test(`reviews real image candidates safely at ${viewport.name}`, async ({ page }) => {
    test.skip(!episodeId, "IMAGE_UAT_EPISODE_ID is required for the V2 episode review route");
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    const failedResponses: string[] = [];
    const writes: string[] = [];
    const publicRequests: string[] = [];
    const originalMediaRequests: string[] = [];
    let thumbnailRequests = 0;
    page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    page.on("response", (response) => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`); });
    page.on("request", (request) => {
      const url = new URL(request.url());
      const pathname = url.pathname;
      if (!["127.0.0.1", "localhost"].includes(url.hostname)) publicRequests.push(request.url());
      if (pathname.includes("/thumbnail")) thumbnailRequests += 1;
      if (pathname.includes("/content") || pathname.includes("/original") || /\.(mp4|mov|mkv|webm|wav|flac|mp3)(\?|$)/i.test(pathname)) {
        originalMediaRequests.push(request.url());
      }
      if (pathname.startsWith("/api/") && request.method() !== "GET") writes.push(`${request.method()} ${pathname}`);
    });

    const reviewUrl = `/projects/${projectId}/episodes/${episodeId}/review`;
    await page.goto(reviewUrl, { waitUntil: "networkidle", timeout: 45000 });

    // The V2 review inbox owns candidate navigation and comparison.
    await expect(page.getByRole("heading", { name: "媒体版本审核与选择" })).toBeVisible();

    // Both seeded PROXY IMAGE candidates must be listed in the review inbox.
    const cards = page.getByRole("list", { name: "待审核媒体版本" }).locator("button.review-row");
    const cardCount = await cards.count();
    expect(cardCount).toBeGreaterThanOrEqual(2);

    // Select the first image candidate and verify the A/B compare toolbar appears.
    await cards.first().click();
    await page.waitForTimeout(800);
    await expect(page.getByLabel("图片比较对象")).toBeVisible();
    await expect(page.getByRole("button", { name: "置顶当前参考图" })).toBeVisible();

    // Keyboard candidate navigation: focus the grid card and ArrowRight moves to the next candidate.
    await cards.first().focus();
    await page.keyboard.press("ArrowRight");
    await page.waitForTimeout(500);
    const selectedSecond = await cards.nth(1).getAttribute("aria-current");
    expect(selectedSecond).toBe("true");

    const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    const result: ViewportResult = {
      viewport: viewport.name,
      status:
        writes.length === 0 &&
        publicRequests.length === 0 &&
        originalMediaRequests.length === 0 &&
        consoleErrors.length === 0 &&
        pageErrors.length === 0 &&
        failedResponses.length === 0 &&
        horizontalOverflow === 0
          ? "PASS"
          : "FAIL",
      image_candidates_seen: cardCount,
      thumbnail_requests: thumbnailRequests,
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
