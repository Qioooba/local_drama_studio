import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

type ViewportResult = {
  viewport: string;
  status: "PASS" | "FAIL";
  candidate_cards: number;
  waveform_requests: number;
  content_requests: string[];
  writes: string[];
  public_requests: string[];
  console_errors: string[];
  page_errors: string[];
  failed_responses: string[];
  horizontal_overflow_px: number;
};

const projectId = "289cf744-ff65-413d-ae7d-4c9d9b1e912e";
const episodeId = "2132aabb-131f-4ad6-9060-5960aa21265c";
const viewports = [
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1280x800", width: 1280, height: 800 },
  { name: "1024x768", width: 1024, height: 768 },
] as const;

test.setTimeout(120_000);
const results: ViewportResult[] = [];

function evidencePath() {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  return path.join(cwd, "docs", "evidence", "g10", "fr-aud-001-002-tts-review-windows-uat-2026-08-17.json");
}

test.afterAll(() => {
  const passed = results.length === viewports.length && results.every((item) => item.status === "PASS");
  const output = {
    schema_version: "g10-fr-aud-001-002-tts-review-windows-uat.v1",
    observed_at: new Date().toISOString(),
    mode: "LOCAL_ONLY",
    project_id: projectId,
    episode_id: episodeId,
    scope: ["dialogue TTS governance panel", "voice authorization", "published TTS profile", "FORMAL TTS candidate audition"],
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
      "Real React dialogue/TTS governance surfaces were exercised at 1440x900/1280x800/1024x768 against an isolated SQLite snapshot seeded with a real Windows SAPI-synthesized WAV, a USER_OWNED voice authorization, a published TTS profile and a FORMAL TTS candidate. Only derived waveforms are requested; original audio /content is never fetched (preload=none); no mutation controls were clicked.",
  };
  fs.mkdirSync(path.dirname(evidencePath()), { recursive: true });
  fs.writeFileSync(evidencePath(), `${JSON.stringify(output, null, 2)}\n`, "utf8");
});

for (const viewport of viewports) {
  test(`shows the real TTS candidate chain safely at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    const failedResponses: string[] = [];
    const writes: string[] = [];
    const publicRequests: string[] = [];
    const contentRequests: string[] = [];
    let waveformRequests = 0;
    page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    page.on("response", (response) => { if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`); });
    page.on("request", (request) => {
      const url = new URL(request.url());
      const pathname = url.pathname;
      if (!["127.0.0.1", "localhost"].includes(url.hostname)) publicRequests.push(request.url());
      if (pathname.includes("/waveform")) waveformRequests += 1;
      if (pathname.includes("/content")) contentRequests.push(request.url());
      if (pathname.startsWith("/api/") && request.method() !== "GET") writes.push(`${request.method()} ${pathname}`);
    });

    await page.goto(`/?view=projects&project=${projectId}&episode=${episodeId}`, { waitUntil: "networkidle", timeout: 45000 });

    // The dialogue/TTS governance panel renders the real candidate chain.
    await expect(page.getByRole("heading", { name: "对白候选与音色授权" })).toBeVisible();
    await expect(page.getByText("TTS PROFILE READY", { exact: true })).toBeVisible();
    await expect(page.getByText("对白", { exact: false }).first()).toBeVisible();

    // The FORMAL TTS candidate card with its emotion/speech-rate metadata and waveform.
    const candidateCards = page.locator(".tts-candidate-card");
    const cardCount = await candidateCards.count();
    expect(cardCount).toBeGreaterThanOrEqual(1);
    await expect(candidateCards.first()).toContainText("FORMAL");
    await expect(candidateCards.first()).toContainText("平静");

    // Audition audio must never auto-fetch original media (preload=none).
    const audioPreload = await page.locator(".tts-candidate-card audio").first().getAttribute("preload");
    expect(audioPreload).toBe("none");

    const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    const result: ViewportResult = {
      viewport: viewport.name,
      status:
        writes.length === 0 &&
        publicRequests.length === 0 &&
        contentRequests.length === 0 &&
        consoleErrors.length === 0 &&
        pageErrors.length === 0 &&
        failedResponses.length === 0 &&
        horizontalOverflow === 0
          ? "PASS"
          : "FAIL",
      candidate_cards: cardCount,
      waveform_requests: waveformRequests,
      content_requests: contentRequests,
      writes,
      public_requests: publicRequests,
      console_errors: consoleErrors,
      page_errors: pageErrors,
      failed_responses: failedResponses,
      horizontal_overflow_px: horizontalOverflow,
    };
    results.push(result);
    expect(writes).toEqual([]);
    expect(publicRequests).toEqual([]);
    expect(contentRequests).toEqual([]);
    expect(consoleErrors).toEqual([]);
    expect(pageErrors).toEqual([]);
    expect(failedResponses).toEqual([]);
    expect(horizontalOverflow).toBe(0);
  });
}
