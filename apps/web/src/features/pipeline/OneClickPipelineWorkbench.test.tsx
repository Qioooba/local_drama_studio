import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import * as apiGenerated from "../../generated/api";
import { OneClickPipelineWorkbench } from "./OneClickPipelineWorkbench";
import * as pipelineClient from "./pipelineClient";
import { queryKeys } from "../../query/queryKeys";

vi.mock("./pipelineClient", () => ({
  applyPipelineRun: vi.fn(),
  cancelPipelineRun: vi.fn(),
  getPipelineRun: vi.fn(),
  getLatestPipeline: vi.fn(),
  listPipelineRuns: vi.fn(),
  preflightStoryPipeline: vi.fn(),
  retryPipelineRun: vi.fn(),
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

function renderWorkbench(queryClient: QueryClient) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><OneClickPipelineWorkbench projectId="proj-1" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

function completedRun(status: "READY" | "REVIEW_REQUIRED" = "READY"): pipelineClient.PipelineRun {
  const now = new Date().toISOString();
  return {
    run_id: "pipe-456", project_id: "proj-1", job_id: "job-1", state: "SUCCEEDED",
    stage: "REVIEW_READY", stage_label: "全剧规划完成", progress_pct: 100, revision: 7,
    visual_style: "国风仙侠 电影级写实 (Cinematic Realistic)", target_episode_duration_seconds: 120,
    voice_preset: "DEFAULT_VOX_CPM2", auto_run_rendering: false, source_document_version_id: "ver-1",
    episodes_count: 2, characters_count: 1, scenes_count: 1, props_count: 1, shots_count: 0,
    episodes: [{ number: 1, code: "EP01", title: "归来", summary: "主角回到宗门。" }],
    assets: {
      characters: [{ id: "c1", name: "韩立", kind: "CHARACTER", code: "CHAR_001", description: "谨慎的修仙者" }],
      scenes: [{ id: "s1", name: "神手谷", kind: "SCENE", code: "SCENE_001" }],
      props: [{ id: "p1", name: "掌天瓶", kind: "PROP", code: "PROP_001" }],
    },
    draft: {
      schema_version: "pipeline.story-plan.v3",
      story_plan: { episodes: [
        { number: 1, code: "EP01", title: "归来", summary: "主角回到宗门。" },
        { number: 2, code: "EP02", title: "试炼", summary: "新的试炼开始。" },
      ] },
      story_bible: {
        title: "创作记忆", logline: "修仙者归来。", synopsis: "主角重新面对命运。",
        visual_style: "国风", target_episode_duration_seconds: 120,
      },
      assets: {
        characters: [{ id: "c1", name: "韩立", kind: "CHARACTER", description: "谨慎的修仙者" }],
        scenes: [{ id: "s1", name: "神手谷", kind: "SCENE" }],
        props: [{ id: "p1", name: "掌天瓶", kind: "PROP" }],
      },
      breakdowns: [],
      generation: { generation_mode: "LLM_COMPLETE", provider: "OLLAMA_LOOPBACK", model: "qwen-test", llm_call_count: 3, generated_at: now, media_generation_started: false, coverage: ["EPISODE_PLAN"] },
    },
    quality_report: { status, blockers: [], warnings: status === "REVIEW_REQUIRED" ? ["分集数量达到上限"] : [], checks: [] },
    apply_state: "NOT_APPLIED", applied_sections: [], extraction_mode: "LLM",
    created_at: now, updated_at: now, error_message: null,
  };
}

describe("OneClickPipelineWorkbench", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    vi.mocked(pipelineClient.listPipelineRuns).mockResolvedValue({ runs: [] });
    vi.mocked(apiGenerated.getProjectOverviewV2).mockResolvedValue({ seasons: [] } as never);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("checks and starts production with one primary action", async () => {
    vi.mocked(pipelineClient.getLatestPipeline).mockResolvedValue({ run: null });
    vi.mocked(apiGenerated.uploadScriptDocument).mockResolvedValue({
      import: { source_document_version_id: "ver-1", preview: { character_count: 5000, paragraph_count: 50 } },
    } as never);
    vi.mocked(pipelineClient.preflightStoryPipeline).mockResolvedValue({
      safe_mode: true,
      ai: { required: true, ready: true, provider: "OLLAMA_LOOPBACK", model: "qwen-test" },
      source: { label: "test.txt", character_count: 5000, paragraph_count: 50, chapter_count: 4, sha256: "hash" },
      existing: { episodes: 0, bibles: 0, assets: 0, shots: 0 },
      estimated_episode_count: 4, warnings: [], effects: { generation: "轻量规划", apply: "自动写入" },
    });
    const running = { ...completedRun(), state: "RUNNING" as const, stage: "QUEUED", progress_pct: 2 };
    vi.mocked(pipelineClient.startOneClickPipeline).mockResolvedValue({ run: running });

    renderWorkbench(queryClient);
    expect(await screen.findByRole("heading", { name: "从完整原稿开始制作" })).toBeTruthy();
    const file = new File(["小说正文内容测试"], "test.txt", { type: "text/plain" });
    const uploadLabel = await screen.findByText("选择小说或剧本文档");
    const fileInput = uploadLabel.closest("label")?.querySelector('input[type="file"]');
    expect(fileInput).toBeTruthy();
    fireEvent.change(fileInput as HTMLInputElement, { target: { files: [file] } });
    await screen.findByText("test.txt");

    fireEvent.click(screen.getByRole("button", { name: "开始 AI 制作" }));
    await waitFor(() => expect(pipelineClient.preflightStoryPipeline).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(pipelineClient.startOneClickPipeline).toHaveBeenCalledWith(
      "proj-1",
      expect.objectContaining({ source_document_version_id: "ver-1", target_episode_duration_seconds: 120 }),
    ));
  });

  it("uses the project default instead of an episode override", async () => {
    vi.mocked(pipelineClient.getLatestPipeline).mockResolvedValue({ run: null });
    vi.mocked(apiGenerated.getProjectOverviewV2).mockResolvedValue({
      project: { id: "proj-1", code: "project-a", title: "Project A", status: "ACTIVE", revision: 2, target_duration_ms: 120_000 },
      seasons: [{ episodes: [{ target_duration_ms: 60_000 }] }],
    } as never);
    vi.mocked(apiGenerated.uploadScriptDocument).mockResolvedValue({
      import: { source_document_version_id: "ver-1", preview: { character_count: 5000, paragraph_count: 50 } },
    } as never);
    vi.mocked(pipelineClient.preflightStoryPipeline).mockResolvedValue({
      safe_mode: true, ai: { required: true, ready: true, provider: "OLLAMA_LOOPBACK", model: "qwen-test" },
      source: { label: "test.txt", character_count: 5000, paragraph_count: 50, chapter_count: 4, sha256: "hash" },
      existing: { episodes: 0, bibles: 0, assets: 0, shots: 0 }, estimated_episode_count: 4, warnings: [], effects: { generation: "轻量规划", apply: "自动写入" },
    });
    vi.mocked(pipelineClient.startOneClickPipeline).mockResolvedValue({ run: { ...completedRun(), state: "RUNNING", stage: "QUEUED", progress_pct: 2 } });

    renderWorkbench(queryClient);
    const duration = await screen.findByRole("combobox", { name: "单集时长" });
    await waitFor(() => expect((duration as HTMLSelectElement).value).toBe("120"));
    const file = new File(["小说正文内容测试"], "test.txt", { type: "text/plain" });
    const input = (await screen.findByText("选择小说或剧本文档")).closest("label")?.querySelector('input[type="file"]');
    fireEvent.change(input as HTMLInputElement, { target: { files: [file] } });
    await screen.findByText("test.txt");
    fireEvent.click(screen.getByRole("button", { name: "开始 AI 制作" }));
    await waitFor(() => expect(pipelineClient.startOneClickPipeline).toHaveBeenCalledWith("proj-1", expect.objectContaining({ target_episode_duration_seconds: 120 })));
  });

  it("automatically adopts a READY plan without section checkboxes", async () => {
    queryClient.setQueryData(queryKeys.seasons.catalog("proj-1"), { catalog: { seasons: [] } });
    queryClient.setQueryData(queryKeys.episodes.list("season-1"), { items: [{ title: "第 1 集" }] });
    const run = completedRun();
    vi.mocked(pipelineClient.getLatestPipeline).mockResolvedValue({ run });
    vi.mocked(pipelineClient.applyPipelineRun).mockResolvedValue({
      run: { ...run, revision: 8, apply_state: "APPLIED", applied_sections: ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"] },
      created: { episodes: 2, bible_revisions: 1, asset_proposals: 0, creative_dossiers: 3, breakdown_drafts: 0 },
      sections: ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
    });

    renderWorkbench(queryClient);
    expect(await screen.findByRole("heading", { name: "AI 分析摘要" })).toBeTruthy();
    await waitFor(() => expect(pipelineClient.applyPipelineRun).toHaveBeenCalledWith(
      "proj-1", "pipe-456", 7, ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
    ));
    expect(screen.queryByText(/应用已选内容/)).toBeNull();
    expect(await screen.findByRole("link", { name: "进入分集制作" })).toBeTruthy();
    expect(queryClient.getQueryState(queryKeys.seasons.catalog("proj-1"))?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(queryKeys.episodes.list("season-1"))?.isInvalidated).toBe(true);
  });

  it("pauses only when quality checking requests attention", async () => {
    const run = completedRun("REVIEW_REQUIRED");
    vi.mocked(pipelineClient.getLatestPipeline).mockResolvedValue({ run });
    vi.mocked(pipelineClient.applyPipelineRun).mockResolvedValue({
      run: { ...run, revision: 8, apply_state: "APPLIED" },
      created: { episodes: 2, bible_revisions: 1, asset_proposals: 0, creative_dossiers: 3, breakdown_drafts: 0 },
      sections: ["STORY_PLAN", "STORY_BIBLE", "ASSET_PROPOSALS"],
    });

    renderWorkbench(queryClient);
    expect(await screen.findByText("需要你确认")).toBeTruthy();
    expect(pipelineClient.applyPipelineRun).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认并进入分集制作" }));
    await waitFor(() => expect(pipelineClient.applyPipelineRun).toHaveBeenCalledTimes(1));
  });
});
