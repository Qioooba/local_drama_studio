import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import * as apiGenerated from "../../generated/api";
import { draftRegistry } from "../drafts/draftRegistry";
import { settleDirtyDrafts } from "../drafts/settleDirtyDrafts";
import { OneClickPipelineWorkbench } from "./OneClickPipelineWorkbench";
import * as pipelineClient from "./pipelineClient";

/**
 * FE-01 (late parse result must not destroy a newer pasted draft),
 * FE-02 (the manuscript registers in the shared draftRegistry and survives
 * unmount/remount), FE-03 (retry / apply-preview / apply failures are visible
 * with code, request id and a retry affordance) and FE-04 (a failed read is not
 * rendered as "nothing created yet").
 *
 * The auditor's reproductions asserted the buggy behaviour; these tests assert
 * the fixed behaviour.
 */

vi.mock("./pipelineClient", () => ({
  applyPipelineRun: vi.fn(),
  cancelPipelineRun: vi.fn(),
  getPipelineRun: vi.fn(),
  getLatestPipeline: vi.fn(),
  listPipelineRuns: vi.fn(),
  preflightStoryPipeline: vi.fn(),
  previewPipelineApply: vi.fn(),
  retryPipelineRun: vi.fn(),
  getWholeDramaStatus: vi.fn(),
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

import { listAdaptationSources } from "../story-adaptation/adaptationPlanClient";

function apiFailure(status: number, code: string, requestId: string | null = null) {
  return {
    name: "ApiRequestError",
    message: `服务拒绝：${code}`,
    status,
    code,
    requestId,
    retryable: false,
    suggestedAction: null,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function fileUploadResult(versionId: string) {
  return { import: { source_document_version_id: versionId, preview: { character_count: 5000, paragraph_count: 50 } } } as never;
}

function completedRun(): pipelineClient.PipelineRun {
  const now = new Date().toISOString();
  return {
    run_id: "pipe-1", project_id: "proj-1", state: "SUCCEEDED", stage: "REVIEW_READY",
    stage_label: "全剧规划完成", progress_pct: 100, revision: 7,
    visual_style: "国风", target_episode_duration_seconds: 120, voice_preset: "DEFAULT_VOX_CPM2",
    auto_run_rendering: false, source_document_version_id: "ver-1",
    application_authorization: { endpoint: "DRAFT_ONLY", sections: [] },
    apply_continuation: { state: "NOT_AUTHORIZED", job_id: null, last_error_code: null },
    episodes_count: 1, characters_count: 0, scenes_count: 0, props_count: 0, shots_count: 0,
    episodes: [{ code: "EP01", title: "第一集", summary: "开始。" }],
    assets: { characters: [], scenes: [], props: [] },
    draft: { schema_version: "v3", story_plan: { episodes: [] }, story_bible: undefined, breakdowns: [] },
    quality_report: { status: "READY", blockers: [], warnings: [], checks: [] },
    apply_state: "NOT_APPLIED", applied_sections: [], extraction_method: undefined,
    created_at: now, updated_at: now, error_message: null,
  } as unknown as pipelineClient.PipelineRun;
}

function renderWorkbench(projectId = "proj-1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter><OneClickPipelineWorkbench projectId={projectId} /></MemoryRouter>
    </QueryClientProvider>,
  );
  return { client, ...view };
}

async function selectFile(name: string) {
  const input = (await screen.findByText("选择小说或剧本文档")).closest("label")?.querySelector('input[type="file"]');
  const file = new File(["文件正文"], name, { type: "text/plain" });
  fireEvent.change(input as HTMLInputElement, { target: { files: [file] } });
}

describe("OneClickPipelineWorkbench manuscript drafts", () => {
  beforeEach(() => {
    window.localStorage.clear();
    draftRegistry.clear();
    vi.clearAllMocks();
    vi.mocked(pipelineClient.listPipelineRuns).mockResolvedValue({ runs: [] });
    vi.mocked(pipelineClient.getLatestPipeline).mockResolvedValue({ run: null });
    vi.mocked(apiGenerated.getProjectOverviewV2).mockResolvedValue({ seasons: [] } as never);
    vi.mocked(listAdaptationSources).mockResolvedValue({ items: [] } as never);
  });

  afterEach(() => {
    cleanup();
    draftRegistry.clear();
    window.localStorage.clear();
  });

  it("keeps the newly pasted manuscript when a late upload response arrives", async () => {
    const upload = deferred<unknown>();
    vi.mocked(apiGenerated.uploadScriptDocument).mockReturnValue(upload.promise as never);

    renderWorkbench();
    await screen.findByRole("heading", { name: "从完整原稿开始制作" });
    await selectFile("A.txt");
    expect(await screen.findByText(/正在解析上传的文档/)).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "粘贴正文" }));
    const textarea = screen.getByPlaceholderText("粘贴小说正文或剧本内容");
    fireEvent.change(textarea, { target: { value: "这是用户上传期间新粘贴的原稿 B。" } });

    upload.resolve(fileUploadResult("ver-A"));
    await waitFor(() => expect(screen.queryByText(/正在解析上传的文档/)).toBeNull());

    expect((screen.getByPlaceholderText("粘贴小说正文或剧本内容") as HTMLTextAreaElement).value)
      .toBe("这是用户上传期间新粘贴的原稿 B。");
    // The stale parse result must not become the selected source either.
    fireEvent.click(screen.getByRole("tab", { name: "上传文档" }));
    expect(screen.queryByText("A.txt")).toBeNull();
    expect(screen.getByText("选择小说或剧本文档")).toBeTruthy();
  });

  it("keeps the newly pasted manuscript when a late launch response arrives", async () => {
    // The success callback used to clear the draft and mark the *current* text as
    // the submitted baseline, so a manuscript typed while the preflight was in
    // flight was reported as already submitted and lost its protection.
    const launch = deferred<unknown>();
    vi.mocked(pipelineClient.preflightStoryPipeline).mockResolvedValue({ ai: { ready: true, message: "" } } as never);
    vi.mocked(pipelineClient.startOneClickPipeline).mockReturnValue(launch.promise as never);

    renderWorkbench();
    await screen.findByRole("heading", { name: "从完整原稿开始制作" });
    const pasteTab = await screen.findByRole("tab", { name: "粘贴正文" });
    fireEvent.click(pasteTab);
    const textarea = () => screen.getByPlaceholderText("粘贴小说正文或剧本内容") as HTMLTextAreaElement;
    fireEvent.change(textarea(), { target: { value: "提交中的原稿 A：这是一段足够长的正文内容。" } });
    fireEvent.click(screen.getByRole("button", { name: "开始 AI 制作" }));
    await waitFor(() => expect(vi.mocked(pipelineClient.startOneClickPipeline)).toHaveBeenCalledTimes(1));
    // ... the operator keeps editing while the request is in flight ...
    fireEvent.change(textarea(), { target: { value: "预检期间输入的新原稿 B：这同样是一段足够长的正文内容。" } });

    launch.resolve({ run: completedRun() });
    await waitFor(() => expect(draftRegistry.get("one-click-pipeline:proj-1")?.dirty).toBe(true));
    await act(async () => {});

    // B survives, stays dirty and keeps its navigation protection.
    const owner = draftRegistry.get("one-click-pipeline:proj-1")!;
    expect(owner.dirty).toBe(true);
    expect(draftRegistry.getDirty().map((item) => item.ownerId)).toContain("one-click-pipeline:proj-1");
    // Saving the still-dirty draft persists the *newer* manuscript, not the
    // submitted one, and an unsuccessful launch keeps it too.
    await act(async () => {
      await owner.save!(owner.version);
    });
    expect(
      String(window.localStorage.getItem("local-drama:pipeline-manuscript:v1:proj-1")),
    ).toContain("预检期间输入的新原稿 B");
    // What was actually submitted is A, not B.
    const submitted = vi.mocked(pipelineClient.startOneClickPipeline).mock.calls[0][1] as Record<string, unknown>;
    expect(String(submitted.raw_text ?? submitted.paste_text ?? "")).toContain("提交中的原稿 A");
  });

  it("applies only the newest of two source selections", async () => {
    const first = deferred<unknown>();
    const second = deferred<unknown>();
    vi.mocked(apiGenerated.uploadScriptDocument)
      .mockReturnValueOnce(first.promise as never)
      .mockReturnValueOnce(second.promise as never);

    renderWorkbench();
    await screen.findByRole("heading", { name: "从完整原稿开始制作" });
    await selectFile("A.txt");
    fireEvent.click(screen.getByRole("tab", { name: "粘贴正文" }));
    fireEvent.click(screen.getByRole("tab", { name: "上传文档" }));
    await selectFile("B.txt");

    second.resolve(fileUploadResult("ver-B"));
    expect(await screen.findByText("B.txt")).toBeTruthy();

    first.resolve(fileUploadResult("ver-A"));
    await waitFor(() => expect(screen.queryByText(/正在解析上传的文档/)).toBeNull());
    expect(screen.getByText("B.txt")).toBeTruthy();
    expect(screen.queryByText("A.txt")).toBeNull();
  });

  it("registers the pasted manuscript as a dirty draft and restores it after a remount", async () => {
    const { unmount } = renderWorkbench();
    fireEvent.click(await screen.findByRole("tab", { name: "粘贴正文" }));
    fireEvent.change(screen.getByPlaceholderText("粘贴小说正文或剧本内容"), {
      target: { value: "第一章\n尚未提交的正文。" },
    });

    const owner = draftRegistry.get("one-click-pipeline:proj-1");
    expect(owner?.dirty).toBe(true);
    expect(owner?.entityKey).toBe("原始文稿");
    expect(draftRegistry.getDirty().length).toBe(1);

    unmount();
    renderWorkbench();
    fireEvent.click(await screen.findByRole("tab", { name: "粘贴正文" }));
    expect((screen.getByPlaceholderText("粘贴小说正文或剧本内容") as HTMLTextAreaElement).value)
      .toBe("第一章\n尚未提交的正文。");
    // A merely typed draft stays dirty: it has not been kept anywhere durable.
    expect(draftRegistry.get("one-click-pipeline:proj-1")?.dirty).toBe(true);
  });

  it("keeps drafts per project and supports the explicit save/discard contract", async () => {
    const { unmount } = renderWorkbench("proj-1");
    fireEvent.click(await screen.findByRole("tab", { name: "粘贴正文" }));
    fireEvent.change(screen.getByPlaceholderText("粘贴小说正文或剧本内容"), {
      target: { value: "项目一的草稿" },
    });

    const owner = draftRegistry.get("one-click-pipeline:proj-1")!;
    let saved: unknown;
    await act(async () => { saved = await owner.save!(owner.version); });
    expect(saved).toMatchObject({ status: "saved" });
    expect(window.localStorage.getItem("local-drama:pipeline-manuscript:v1:proj-1")).toContain("项目一的草稿");
    expect(draftRegistry.get("one-click-pipeline:proj-1")?.dirty).toBe(false);

    unmount();
    renderWorkbench("proj-2");
    fireEvent.click(await screen.findByRole("tab", { name: "粘贴正文" }));
    expect((screen.getByPlaceholderText("粘贴小说正文或剧本内容") as HTMLTextAreaElement).value).toBe("");

    cleanup();
    renderWorkbench("proj-1");
    fireEvent.click(await screen.findByRole("tab", { name: "粘贴正文" }));
    expect((screen.getByPlaceholderText("粘贴小说正文或剧本内容") as HTMLTextAreaElement).value).toBe("项目一的草稿");
    expect(draftRegistry.get("one-click-pipeline:proj-1")?.dirty).toBe(false);

    fireEvent.change(screen.getByPlaceholderText("粘贴小说正文或剧本内容"), { target: { value: "项目一的草稿（改）" } });
    const dirtyOwner = draftRegistry.get("one-click-pipeline:proj-1")!;
    expect(dirtyOwner.dirty).toBe(true);
    let discarded: unknown;
    await act(async () => { discarded = await dirtyOwner.discard!(dirtyOwner.version); });
    expect(discarded).toMatchObject({ status: "discarded" });
    expect((screen.getByPlaceholderText("粘贴小说正文或剧本内容") as HTMLTextAreaElement).value).toBe("项目一的草稿");
    expect(draftRegistry.get("one-click-pipeline:proj-1")?.dirty).toBe(false);
  });

  it("arms the shared navigation guard and keeps the pasted text after settling", async () => {
    const { unmount } = renderWorkbench();
    fireEvent.click(await screen.findByRole("tab", { name: "粘贴正文" }));
    fireEvent.change(screen.getByPlaceholderText("粘贴小说正文或剧本内容"), {
      target: { value: "导航保护下的草稿" },
    });
    expect(draftRegistry.getDirty().map((owner) => owner.entityKey)).toEqual(["原始文稿"]);

    const settled = await act(async () => settleDirtyDrafts(draftRegistry, "save"));
    expect(settled.allowed).toBe(true);
    expect(draftRegistry.getDirty().length).toBe(0);
    expect(window.localStorage.getItem("local-drama:pipeline-manuscript:v1:proj-1")).toContain("导航保护下的草稿");

    unmount();
    renderWorkbench();
    fireEvent.click(await screen.findByRole("tab", { name: "粘贴正文" }));
    expect((screen.getByPlaceholderText("粘贴小说正文或剧本内容") as HTMLTextAreaElement).value).toBe("导航保护下的草稿");
  });
});

describe("OneClickPipelineWorkbench action failures", () => {
  beforeEach(() => {
    window.localStorage.clear();
    draftRegistry.clear();
    vi.clearAllMocks();
    vi.mocked(pipelineClient.listPipelineRuns).mockResolvedValue({ runs: [] });
    vi.mocked(apiGenerated.getProjectOverviewV2).mockResolvedValue({ seasons: [] } as never);
    vi.mocked(listAdaptationSources).mockResolvedValue({ items: [] } as never);
    vi.mocked(pipelineClient.getWholeDramaStatus).mockResolvedValue({
      project_id: "proj-1", project_code: "P1", project_title: "Project",
      overall_status: "NOT_STARTED", state_counts: {}, total_episodes: 0, episodes: [],
    });
  });

  afterEach(() => {
    cleanup();
    draftRegistry.clear();
    window.localStorage.clear();
  });

  it("shows a persistent alert with code and request id when retry fails, and can retry", async () => {
    vi.mocked(pipelineClient.getLatestPipeline).mockResolvedValue({
      run: { ...completedRun(), state: "FAILED", error_message: "解析中断" },
    });
    vi.mocked(pipelineClient.retryPipelineRun).mockRejectedValue(
      apiFailure(500, "PIPELINE_RETRY_FAILED", "req-retry-1"),
    );

    renderWorkbench();
    fireEvent.click(await screen.findByRole("button", { name: "自动重试" }));

    const alerts = await screen.findAllByRole("alert");
    const alert = alerts.find((node) => node.textContent?.includes("PIPELINE_RETRY_FAILED"));
    expect(alert).toBeTruthy();
    expect(alert?.textContent).toContain("req-retry-1");
    expect(pipelineClient.retryPipelineRun).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "用最新修订再次重试" }));
    await waitFor(() => expect(pipelineClient.retryPipelineRun).toHaveBeenCalledTimes(2));
  });

  it("reports a failed impact preview and keeps the current result visible", async () => {
    vi.mocked(pipelineClient.getLatestPipeline).mockResolvedValue({ run: completedRun() });
    vi.mocked(pipelineClient.previewPipelineApply).mockRejectedValue(
      apiFailure(422, "PIPELINE_IMPACT_UNSUPPORTED", null),
    );

    renderWorkbench();
    expect(await screen.findByRole("heading", { name: "AI 分析摘要" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "查看应用影响" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("PIPELINE_IMPACT_UNSUPPORTED");
    expect(alert.textContent).toContain("影响预览失败");
    // The analysed run and the current selection are still visible.
    expect(screen.getByRole("heading", { name: "AI 分析摘要" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "重新预览影响" }));
    await waitFor(() => expect(pipelineClient.previewPipelineApply).toHaveBeenCalledTimes(2));
  });

  it("refreshes the revision and drops the stale impact hash after a 409 apply failure", async () => {
    vi.mocked(pipelineClient.getLatestPipeline).mockResolvedValue({ run: completedRun() });
    vi.mocked(pipelineClient.previewPipelineApply).mockResolvedValue({
      impact: {
        schema_version: "pipeline-apply-impact/v1", project_id: "proj-1", run_id: "pipe-1", run_revision: 7,
        sections: ["STORY_PLAN"], episodes: { add: [], update: [], preserve: [], skip: [] },
        story_bible: { will_create: false, will_switch_current_revision: false },
        assets: { reuse: [], add: [] }, produced_episode_context_changes: [],
        requires_confirmation: false, writes_performed: false, impact_sha256: "a".repeat(64),
      },
      quality_report: completedRun().quality_report,
      can_apply: true,
    });
    vi.mocked(pipelineClient.applyPipelineRun).mockRejectedValue(
      apiFailure(409, "PIPELINE_REVISION_CONFLICT", "req-apply-1"),
    );

    renderWorkbench();
    await screen.findByRole("heading", { name: "AI 分析摘要" });
    fireEvent.click(screen.getByRole("button", { name: "查看应用影响" }));
    expect(await screen.findByRole("heading", { name: "应用影响预览" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "确认以上影响并进入分集制作" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("PIPELINE_REVISION_CONFLICT");
    expect(alert.textContent).toContain("req-apply-1");
    // The stale impact hash is discarded and the user must re-confirm.
    await waitFor(() => expect(screen.queryByRole("heading", { name: "应用影响预览" })).toBeNull());
    expect(await screen.findByText(/任务修订已刷新/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "刷新修订并重新预览" })).toBeTruthy();
  });
});

describe("OneClickPipelineWorkbench read failures", () => {
  beforeEach(() => {
    window.localStorage.clear();
    draftRegistry.clear();
    vi.clearAllMocks();
    vi.mocked(pipelineClient.listPipelineRuns).mockResolvedValue({ runs: [] });
    vi.mocked(apiGenerated.getProjectOverviewV2).mockResolvedValue({ seasons: [] } as never);
    vi.mocked(listAdaptationSources).mockResolvedValue({ items: [] } as never);
  });

  afterEach(() => {
    cleanup();
    draftRegistry.clear();
    window.localStorage.clear();
  });

  it("never shows the new-project form when reading the existing run fails", async () => {
    vi.mocked(pipelineClient.getLatestPipeline)
      .mockRejectedValueOnce(apiFailure(500, "PIPELINE_READ_FAILED", "req-read-1"))
      .mockResolvedValue({ run: completedRun() });

    renderWorkbench();
    expect(await screen.findByRole("heading", { name: "无法确认当前项目是否已有 AI 制作任务" })).toBeTruthy();
    expect(screen.queryByText("选择小说或剧本文档")).toBeNull();
    expect(screen.queryByText("还没创建")).toBeNull();
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("PIPELINE_READ_FAILED");

    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByRole("heading", { name: "AI 分析摘要" })).toBeTruthy();
  });

  it("reports a failed source list instead of rendering it as an empty project", async () => {
    vi.mocked(pipelineClient.getLatestPipeline).mockResolvedValue({ run: null });
    vi.mocked(listAdaptationSources).mockRejectedValue(apiFailure(500, "SOURCE_LIST_FAILED", null));

    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <OneClickPipelineWorkbench projectId="proj-1" sourceDocumentVersionId="ver-1" />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("项目原稿列表读取失败");
    expect(alert.textContent).toContain("SOURCE_LIST_FAILED");
    expect(screen.queryByText(/当前项目还没有已解析的原稿版本/)).toBeNull();
  });
});




