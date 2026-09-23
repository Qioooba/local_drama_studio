/**
 * PR-07: a PARTIAL long manuscript needs a visible "continue" entry.
 *
 * The backend persisted a durable ``analysis_cursor`` and exposed
 * ``continuePipelineAnalysis``, but no component called it: the workbench only showed
 * a PARTIAL warning, so a 60-window batch limit was a dead end in the product. The
 * page must offer the action, submit the SERVER's cursor with a fresh operation id,
 * show the real Job state, and never present PARTIAL as if it were complete.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./pipelineClient", () => ({
  applyPipelineRun: vi.fn(),
  cancelPipelineRun: vi.fn(),
  continuePipelineAnalysis: vi.fn(),
  getLatestPipeline: vi.fn(),
  getPipelineRun: vi.fn(),
  getWholeDramaStatus: vi.fn(),
  listPipelineRuns: vi.fn(),
  preflightStoryPipeline: vi.fn(),
  previewPipelineApply: vi.fn(),
  retryPipelineRun: vi.fn(),
  runWholeDrama: vi.fn(),
  startOneClickPipeline: vi.fn(),
}));
vi.mock("../../generated/api", () => ({ getProjectOverviewV2: vi.fn(), uploadScriptDocument: vi.fn() }));
vi.mock("../story-adaptation/adaptationPlanClient", () => ({
  listAdaptationSources: vi.fn().mockResolvedValue({ items: [] }),
}));
vi.mock("../model-config/CapabilityPicker", () => ({
  CapabilityPicker: () => <div>模型设置</div>,
  effectiveCapabilityProfile: () => null,
  useCapabilityOptions: () => ({ data: { options: [] }, isPending: false }),
}));

import * as apiGenerated from "../../generated/api";
import { OneClickPipelineWorkbench } from "./OneClickPipelineWorkbench";
import * as pipelineClient from "./pipelineClient";

function partialRun(): pipelineClient.PipelineRun {
  const now = new Date().toISOString();
  return {
    run_id: "pipe-partial", project_id: "proj-1", state: "SUCCEEDED", stage: "REVIEW_READY",
    stage_label: "第一批规划完成", progress_pct: 100, revision: 9,
    visual_style: "国风", target_episode_duration_seconds: 120, voice_preset: "DEFAULT_VOX_CPM2",
    auto_run_rendering: false, source_document_version_id: "ver-1",
    application_authorization: { endpoint: "DRAFT_ONLY", sections: [] },
    apply_continuation: { state: "NOT_AUTHORIZED", job_id: null, last_error_code: null },
    episodes_count: 60, characters_count: 0, scenes_count: 0, props_count: 0, shots_count: 0,
    episodes: [{ code: "EP60", title: "第六十集", summary: "末尾。" }],
    assets: { characters: [], scenes: [], props: [] },
    draft: {
      schema_version: "v3",
      story_plan: { episodes: [] },
      story_bible: undefined,
      breakdowns: [],
      source_coverage: {
        schema_version: "pipeline.source-coverage.v2",
        source_sha256: "d".repeat(64),
        status: "PARTIAL",
        coverage_complete: false,
        completed_window_count: 60,
        total_window_count: 61,
        authorized_range: { start_paragraph: 1, end_paragraph: 122, paragraph_count: 122 },
        completed_ranges: [],
        completed_paragraph_intervals: [],
        covered_paragraph_count: 120,
        authorized_paragraph_count: 122,
        coverage_gaps: [],
        unprocessed_ranges: [],
        resume: { start_paragraph: 121, end_paragraph: 122, reason: "BATCH_EPISODE_LIMIT" },
      },
    },
    analysis_cursor: {
      schema_version: "pipeline.analysis-cursor.v1",
      completed_window_count: 60,
      next_window_index: 60,
      total_window_count: 61,
      has_more_windows: true,
    },
    quality_report: { status: "READY", blockers: [], warnings: [], checks: [] },
    apply_state: "NOT_APPLIED", applied_sections: [], extraction_method: undefined,
    created_at: now, updated_at: now, error_message: null,
  } as unknown as pipelineClient.PipelineRun;
}

function renderWorkbench() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><OneClickPipelineWorkbench projectId="proj-1" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("one-click pipeline continue entry (PR-07)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(pipelineClient.listPipelineRuns).mockResolvedValue({ runs: [] });
    vi.mocked(pipelineClient.getLatestPipeline).mockResolvedValue({ run: partialRun() });
    vi.mocked(apiGenerated.getProjectOverviewV2).mockResolvedValue({ seasons: [] } as never);
  });

  afterEach(() => {
    cleanup();
  });

  it("offers the remaining windows with the server's cursor", async () => {
    vi.mocked(pipelineClient.continuePipelineAnalysis).mockResolvedValue({
      run: partialRun(), job_state: "QUEUED", recovery_action: "SUBMITTED",
    } as never);
    renderWorkbench();
    const button = await screen.findByRole("button", { name: /继续解析剩余内容（还有 1 个窗口）/ });
    // The window position is visible before the click, so PARTIAL is never silent.
    expect(screen.getByText(/输入窗口 60 \/ 61/)).toBeTruthy();
    fireEvent.click(button);
    await waitFor(() => expect(pipelineClient.continuePipelineAnalysis).toHaveBeenCalledTimes(1));
    // The submitted command carried the server cursor, not a client guess.
    const call = vi.mocked(pipelineClient.continuePipelineAnalysis).mock.calls[0];
    expect(call[0]).toBe("proj-1");
    expect(call[1]).toBe("pipe-partial");
    expect(call[2]).toBe(9);
    expect(call[3]).toBe("d".repeat(64));
    expect(call[4]).toBe(60);
    // The real Job state is shown instead of an optimistic "已提交".
    expect(await screen.findByText(/任务状态 QUEUED/)).toBeTruthy();
  });

  it("does not offer the action when the cursor is finished", async () => {
    const finished = partialRun();
    finished.analysis_cursor = {
      schema_version: "pipeline.analysis-cursor.v1",
      completed_window_count: 61, next_window_index: 61, total_window_count: 61, has_more_windows: false,
    };
    vi.mocked(pipelineClient.getLatestPipeline).mockResolvedValue({ run: finished });
    renderWorkbench();
    await screen.findByText(/全部窗口已处理/);
    expect(screen.queryByRole("button", { name: /继续解析剩余内容/ })).toBeNull();
  });
});
