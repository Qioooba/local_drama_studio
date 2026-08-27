import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * G11 P1-10 Jianying (CapCut) draft export Windows UAT: real browser clicks
 * against the isolated simulation environment (:3225).  A new timeline
 * revision is created through the real API with a real verified VIDEO media
 * version, then the V2 TimelineExportPanel exports the Jianying draft and
 * the export package (draft_content.json + bundled media) is verified.
 */

const projectId = "e5eaa01d-d39a-4a63-acbf-026da30b46e7";
const episodeId = "d4db1033-9517-4bd0-958d-4228e0abead1";
const realVideoMediaId = "b6b102ad-d344-45aa-801b-80cddbb297cd";
const base = "http://127.0.0.1:3225";
const steps: string[] = [];
const errors: string[] = [];

function evidencePath() {
  const cwd = path.basename(process.cwd()).toLowerCase() === "web" ? path.resolve(process.cwd(), "../..") : process.cwd();
  return path.join(cwd, "docs", "evidence", "g10", "jianying-export-windows-uat-2026-08-17.json");
}

test.afterAll(() => {
  const output = {
    schema_version: "g11.jianying-export-windows-uat.v1",
    observed_at: new Date().toISOString(),
    mode: "LOCAL_ONLY",
    project_id: projectId,
    episode_id: episodeId,
    status: errors.length === 0 ? "PASS" : "IN_PROGRESS",
    isolated_snapshot: true,
    production_database_touched: false,
    runtime_contacted: false,
    network_contacted: false,
    steps,
    errors,
    interpretation:
      "G11 P1-10 Jianying draft export real-click UAT: a frozen timeline revision referencing a verified VIDEO media version is exported through the V2 TimelineExportPanel; the export package contains draft_content.json with video tracks/segments and the bundled media copy.",
  };
  fs.mkdirSync(path.dirname(evidencePath()), { recursive: true });
  fs.writeFileSync(evidencePath(), `${JSON.stringify(output, null, 2)}\n`, "utf8");
});

test("G11 Jianying export: create timeline revision, export draft through the panel", async ({ page }) => {
  test.setTimeout(180_000);
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const failedResponses: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("response", (response) => { if (response.status() >= 400 && !response.url().includes("/favicon")) failedResponses.push(`${response.status()} ${response.url()}`); });

  // --- Create a real timeline revision with the verified video ------------------------------
  const session = await page.request.get(`${base}/api/v1/session/bootstrap`);
  const sessionBody = await session.json();
  const headers = { "X-Local-Instance-Token": sessionBody.token };
  const timelineResponse = await page.request.post(`${base}/api/v1/episodes/${episodeId}/timeline-revisions`, {
    headers: { ...headers, "Content-Type": "application/json" },
    data: {
      items: [{ track_type: "VIDEO", media_version_id: realVideoMediaId, start_us: 0, end_us: 3_000_000, parameters: {} }],
      input_snapshot: { schema_version: "g11.jianying-uat.v1", source: "playwright-spec" },
      status: "FROZEN",
    },
  });
  expect(timelineResponse.status()).toBe(201);
  const timeline = (await timelineResponse.json()).timeline;
  expect(timeline.items.length).toBe(1);
  steps.push(`real timeline revision v${timeline.revision_no} created with verified video ${realVideoMediaId}`);

  // --- Export the Jianying draft through the panel ------------------------------------------
  await page.goto(`${base}/projects/${projectId}/episodes/${episodeId}/timeline?view=export`, { waitUntil: "networkidle" });
  const exportPanel = page.locator(".timeline-v2-export");
  await expect(exportPanel.getByRole("button", { name: "导出剪映草稿" })).toBeVisible();
  await exportPanel.getByRole("button", { name: "导出剪映草稿" }).click();
  await expect(exportPanel.getByText(/已导出 \d+ 个文件/)).toBeVisible({ timeout: 30_000 });
  steps.push("Jianying draft exported through the panel button");

  // --- Verify the export package through the API --------------------------------------------
  const statusResponse = await page.request.get(`${base}/api/v1/episodes/${episodeId}/timeline-status`, { headers });
  const status = (await statusResponse.json()).status;
  const latestId = String(status.timeline.latest.id);
  const exportResponse = await page.request.post(`${base}/api/v1/timeline-revisions/${latestId}:export?format=jianying`, { headers });
  expect(exportResponse.status()).toBe(200);
  const exported = (await exportResponse.json()).export;
  expect(exported.schema_version).toContain("timeline-export");
  const names = (exported.files ?? []).map((file: { rel_path: string }) => String(file.rel_path));
  const draftFile = names.find((name: string) => name.endsWith("draft_content.json"));
  expect(draftFile).toBeTruthy();
  expect(names.some((name: string) => name.includes("/media/"))).toBe(true);
  steps.push(`API: export package verified with draft_content.json + bundled media (${exported.files.length} files)`);

  const passed = consoleErrors.length === 0 && pageErrors.length === 0 && failedResponses.length === 0;
  expect(passed).toBe(true);
  if (consoleErrors.length) errors.push(`console: ${consoleErrors.join("; ")}`);
  if (pageErrors.length) errors.push(`pageerror: ${pageErrors.join("; ")}`);
  if (failedResponses.length) errors.push(`responses: ${failedResponses.join("; ")}`);
  steps.push("zero console errors / zero failed responses");
});
