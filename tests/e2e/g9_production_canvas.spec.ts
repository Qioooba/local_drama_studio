import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
const viewports = [
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1280x800", width: 1280, height: 800 },
  { name: "1024x768", width: 1024, height: 768 },
] as const;

const results: Array<Record<string, unknown>> = [];

test.afterAll(() => {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  const output = path.join(cwd, "docs", "evidence", "g9", "g9-production-canvas-uat-2026-08-15.json");
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(
    output,
    `${JSON.stringify({
      schema_version: "g9-production-canvas-uat.v1",
      observed_at: new Date().toISOString(),
      mode: "LOCAL_ONLY",
      project_id: projectId,
      episode_id: episodeId,
      production_evidence: true,
      fixture_mode: false,
      status: results.length === viewports.length && results.every((result) => result.status === "PASS") ? "PASS" : "IN_PROGRESS",
      viewports: results,
      runtime_contacted: false,
      network_contacted: false,
      jobs_created: false,
      media_created: false,
      interpretation: "Real production API and React canvas with persisted SCALE_UAT shot entities; no mocked nodes or media outputs.",
    }, null, 2)}\n`,
    "utf8",
  );
});

for (const viewport of viewports) {
  test(`renders production canvas at ${viewport.name} without browser errors`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const consoleErrors: string[] = [];
    const consoleWarnings: string[] = [];
    const pageErrors: string[] = [];
    const failedResponses: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
      if (message.type() === "warning") consoleWarnings.push(message.text());
    });
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    page.on("response", (response) => {
      if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`);
    });
    const started = Date.now();
    await page.goto(`/?view=canvas&project=${projectId}&episode=${episodeId}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByText("显示：110/110 节点")).toBeVisible({ timeout: 60_000 });
    const canvasReadyMs = Date.now() - started;
    const nodeCount = await page.locator(".react-flow__node").count();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    const result = {
      viewport: viewport.name,
      status: "PASS",
      node_count: nodeCount,
      expected_node_count: 110,
      canvas_ready_ms: canvasReadyMs,
      horizontal_overflow_px: overflow,
      console_errors: consoleErrors,
      console_warnings: consoleWarnings,
      page_errors: pageErrors,
      failed_responses: failedResponses,
    };
    results.push(result);
    expect(nodeCount).toBe(110);
    expect(overflow).toBe(0);
    expect(consoleErrors).toEqual([]);
    expect(consoleWarnings).toEqual([]);
    expect(pageErrors).toEqual([]);
    expect(failedResponses).toEqual([]);
    expect(canvasReadyMs).toBeLessThan(60_000);
  });
}
