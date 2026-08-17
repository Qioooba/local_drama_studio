import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * Bounded browser performance observation (NFR-PERF-001/002).
 *
 * Measures real page-load time (domcontentloaded and networkidle) for the main
 * management and production views at 1440x900 / 1280x800 / 1024x768 against
 * the isolated production snapshot.  Output is an OBSERVED_NOT_BENCHMARKED
 * baseline: it records truthful p95 observations on the local Windows host and
 * explicitly does not claim the formal 10k-scale release baseline.
 */

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
const shotId = "020f9248-14b7-4f92-9edd-ee587ffdedf3";
const viewports = [
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1280x800", width: 1280, height: 800 },
  { name: "1024x768", width: 1024, height: 768 },
] as const;
const views = [
  { name: "projects", url: `/?view=projects&project=${projectId}&episode=${episodeId}` },
  { name: "generation", url: `/?view=generation&project=${projectId}&episode=${episodeId}&shot=${shotId}` },
  { name: "reviews", url: `/?view=reviews&project=${projectId}&episode=${episodeId}` },
  { name: "jobs", url: `/?view=jobs&project=${projectId}&episode=${episodeId}` },
  { name: "diagnostics", url: `/?view=diagnostics&project=${projectId}&episode=${episodeId}` },
  { name: "profiles", url: `/?view=profiles&project=${projectId}&episode=${episodeId}` },
] as const;

test.setTimeout(300_000);
const observations: Array<{ viewport: string; view: string; domcontentloaded_ms: number; networkidle_ms: number }> = [];
const errors: string[] = [];

function evidencePath() {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  return path.join(cwd, "docs", "evidence", "g10", "nfr-perf-browser-observation-2026-08-17.json");
}

function p95(values: number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.95) - 1);
  return sorted[index];
}

test.afterAll(() => {
  const domMs = observations.map((item) => item.domcontentloaded_ms);
  const idleMs = observations.map((item) => item.networkidle_ms);
  const output = {
    schema_version: "g10.nfr-perf-browser-observation.v1",
    observed_at: new Date().toISOString(),
    mode: "LOCAL_ONLY",
    project_id: projectId,
    status: errors.length === 0 ? "PASS" : "IN_PROGRESS",
    isolated_snapshot: true,
    production_database_touched: false,
    observations,
    summary: {
      domcontentloaded_p95_ms: p95(domMs),
      networkidle_p95_ms: p95(idleMs),
      domcontentloaded_max_ms: Math.max(...domMs),
      networkidle_max_ms: Math.max(...idleMs),
      sample_count: observations.length,
      viewports: viewports.length,
      views: views.length,
    },
    benchmark_scope: "OBSERVED_NOT_BENCHMARKED",
    errors,
    interpretation:
      "Local Windows host browser page-load observation on the production snapshot (real React + FastAPI). Not the formal 10k-scale release baseline: no cold-cache runs, no virtual scrolling, no release hardware p95 commitment.",
  };
  fs.mkdirSync(path.dirname(evidencePath()), { recursive: true });
  fs.writeFileSync(evidencePath(), `${JSON.stringify(output, null, 2)}\n`, "utf8");
});

for (const viewport of viewports) {
  for (const view of views) {
    test(`observes page load for ${view.name} at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      page.on("console", (message) => { if (message.type() === "error") errors.push(`console ${message.text()}`); });
      page.on("pageerror", (error) => errors.push(`pageerror ${String(error)}`));
      page.on("response", (response) => { if (response.status() >= 400) errors.push(`response ${response.status()} ${response.url()}`); });
      const start = Date.now();
      await page.goto(view.url, { waitUntil: "domcontentloaded", timeout: 60_000 });
      const domMs = Date.now() - start;
      const idleStart = Date.now();
      await page.waitForLoadState("networkidle", { timeout: 60_000 }).catch(() => undefined);
      const idleMs = Date.now() - idleStart + domMs;
      await expect(page.locator("#workspace-content")).toBeVisible();
      observations.push({ viewport: viewport.name, view: view.name, domcontentloaded_ms: domMs, networkidle_ms: idleMs });
    });
  }
}
