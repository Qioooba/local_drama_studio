import type { FullResult, Reporter, TestCase, TestResult } from "@playwright/test/reporter";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

type Observation = {
  viewport: string;
  status: string;
  duration_ms: number;
};

export default class G9BenchmarkReporter implements Reporter {
  private readonly observations: Observation[] = [];

  onTestEnd(test: TestCase, result: TestResult): void {
    const viewport = test.title.match(/at (\d+x\d+)$/)?.[1] ?? "unknown";
    this.observations.push({ viewport, status: result.status, duration_ms: result.duration });
  }

  onEnd(result: FullResult): void {
    const evidencePath = resolve(process.cwd(), "docs/evidence/g9/g9-browser-performance-2026-08-15.json");
    mkdirSync(dirname(evidencePath), { recursive: true });
    writeFileSync(
      evidencePath,
      `${JSON.stringify({
        schema_version: "g9.browser_performance_uat.v1",
        observed_at: new Date().toISOString(),
        status: result.status === "passed" && this.observations.every((item) => item.status === "passed") ? "PASS" : "FAIL",
        scope: "isolated local API and real React Flow browser fixture",
        fixture: {
          project_code: "g9_browser_fixture",
          episode_count: 1,
          shot_count: 62,
          returned_shots: 60,
          visible_nodes: 300,
          api_port: 3221,
          production_database_touched: false,
        },
        browser: {
          executable: "system Microsoft Edge",
          viewports: this.observations,
          assertions: [
            "300 .react-flow__node elements rendered",
            "document horizontal overflow = 0",
            "console errors and warnings = 0",
            "page errors = 0",
            "failed responses = 0",
          ],
        },
        safety: { runtime_contacted: false, network_contacted: false, mutated: false, jobs_created: false, comfyui_contacted: false },
        formal_gate_effect: "NONE",
        production_evidence: false,
        interpretation: "This is a repeatable isolated browser performance baseline. It does not substitute for production 100-300-node UAT or authorize G9 PASS.",
      }, null, 2)}\n`,
      "utf8",
    );
  }
}
