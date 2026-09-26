/**
 * 第 1 步 behaviour that the design fixes as non-negotiable (§B2, §C1, §C4.1):
 *
 * * the first screen asks “你现在有什么” with exactly two primary cards and
 *   从主题开始 as the secondary entry, and the card decides `script_policy`
 *   while 粘贴 / 上传 only decide the input channel;
 * * `PRESERVE_ORIGINAL` never calls an AI rewrite, registers the exact original
 *   text, and exposes no target-duration control that could silently rewrite or
 *   truncate a finished script;
 * * one body editor: the spoken text and the dictionary appear only after
 *   发音修正, and the fact sources open in a drawer;
 * * a failed GET keeps the last known content and says 更新失败;
 * * adopting a new paragraph draft states the real downstream impact and keeps
 *   the old revision.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  breakdownExplainerStory,
  createExplainerScriptRevision,
  listCapabilityOptions,
  preflightExplainerPlan,
  startExplainerRun,
} from "../../generated/api";
import { draftRegistry } from "../drafts/draftRegistry";
import { ExplainerActionBarProvider, ExplainerStepActionBar } from "./ExplainerStepActionBar";
import { explainerStepStatuses } from "./ExplainerSteps";
import {
  useExplainerOverview,
  useExplainerScript,
  useExplainerSegments,
} from "./useExplainerQueries";
import { ExplainerScriptPage } from "./ScriptPage";

vi.mock("../../generated/api", () => ({
  patchExplainerSegment: vi.fn(),
  createExplainerScriptRevision: vi.fn(),
  freezeExplainerScript: vi.fn(),
  getExplainerClaimEvidence: vi.fn(),
  importExplainerSource: vi.fn(),
  startExplainerResearchRun: vi.fn(),
  breakdownExplainerStory: vi.fn(),
  preflightExplainerPlan: vi.fn(),
  startExplainerRun: vi.fn(),
  listCapabilityOptions: vi.fn(),
}));

vi.mock("./useExplainerQueries", () => ({
  useExplainerScript: vi.fn(),
  useExplainerOverview: vi.fn(),
  useExplainerSegments: vi.fn(),
}));

function capabilityOptions() {
  return {
    capability: "LLM_STORY_PARSE",
    scope: { project_id: "project-1", episode_id: null, shot_id: null },
    selection: { mode: "AUTO", source: "PROJECT", profile_version_id: "profile-1", ready: true, option: null, blockers: [] },
    options: [],
    configured_runtime: null,
    summary: { total_count: 1, selectable_count: 1, blocked_count: 0 },
    repair_href: "/system/capabilities",
    read_only: true,
    runtime_contacted: false,
    network_contacted: false,
    mutated: false,
  };
}

function scriptFact(overrides: Record<string, unknown> = {}) {
  return {
    revision: { id: "rev-2", revision_no: 2, status: "DRAFT", content_hash: "h".repeat(64) },
    segments: [
      {
        id: "seg-a",
        canonical_segment_id: "seg_001",
        ordinal: 0,
        revision: 1,
        display_text: "第一段原文内容，用来验证正文编辑器。",
        spoken_text: "第一段原文内容，用来验证正文编辑器。",
        statement_type: "FACT",
        claim_ids_json: ["C001", "C002"],
        pronunciation_map_json: [{ display: "1962年", spoken: "一九六二年" }],
        content_locked_by_human: false,
        target_duration_ms: null,
      },
      {
        id: "seg-b",
        canonical_segment_id: "seg_002",
        ordinal: 1,
        revision: 1,
        display_text: "第二段原文内容。",
        spoken_text: "第二段原文内容。",
        statement_type: "ORIGINAL_EXPLANATION",
        claim_ids_json: [],
        pronunciation_map_json: [],
        content_locked_by_human: false,
        target_duration_ms: null,
      },
    ],
    claims: [],
    sources: [{ id: "src-1", title: "示例来源", body_sha256: "b".repeat(64) }],
    open_core_conflicts: [],
    validity: {},
    ...overrides,
  };
}

function overviewFact(policy: string | null) {
  return {
    project_id: "project-1",
    video: {
      id: "video-1",
      title: "灯塔最后一页值班记录",
      input_payload_json: policy ? { script_policy: policy } : {},
      automation_mode: "AUTO_WITH_EXCEPTIONS",
      revision: 3,
      current_script_revision_id: "rev-2",
      source_locale: "zh-CN",
      aspect_ratio: "16:9",
      target_seconds: 180,
      inference_mode: "LOCAL_ONLY",
    },
    editions: [],
    beat_count: 4,
    render_type_counts: {},
    latest_run: null,
    open_issues: [],
    open_issue_count: 0,
    blocking_issue_count: 0,
    active_decisions: [],
    authority_labels: { machine: "", human: "", publication: "" },
    capability_snapshot: { probed: true },
  };
}

/** Mirrors the workspace shell: the page declares the bar, the shell renders it. */
function Harness() {
  return (
    <ExplainerActionBarProvider>
      <div className="explainer-workspace">
        <ExplainerScriptPage />
      </div>
      <ExplainerStepActionBar projectId="project-1" activePage="script" statuses={explainerStepStatuses(null)} />
    </ExplainerActionBarProvider>
  );
}

function mount(entry = "/explainers/project-1/script") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/explainers/:projectId/script" element={<Harness />} />
          <Route path="/explainers/:projectId/assets" element={<p>人物与风格页面</p>} />
          <Route path="/explainers/:projectId/audio" element={<p>配音页面</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockScript(data: unknown, extra: Record<string, unknown> = {}) {
  vi.mocked(useExplainerScript).mockReturnValue({ data, isPending: false, isError: false, refetch: vi.fn(), ...extra } as never);
}

function mockOverview(policy: string | null) {
  vi.mocked(useExplainerOverview).mockReturnValue({ data: overviewFact(policy), isPending: false, isError: false } as never);
}

function mockSegments(selectedTakes = 0, measured = null as number | null) {
  vi.mocked(useExplainerSegments).mockReturnValue({
    data: {
      video_id: "video-1",
      script_revision_id: "rev-2",
      segments: [],
      selected_takes: Array.from({ length: selectedTakes }, (_item, index) => ({ id: `take-${index}` })),
      measured_total_ms: measured,
      timing_status: "ALIGNED",
    },
    isPending: false,
    isError: false,
  } as never);
}

describe("ExplainerScriptPage step 1 (§B2)", () => {
  beforeEach(() => {
    draftRegistry.clear();
    vi.clearAllMocks();
    vi.mocked(listCapabilityOptions).mockResolvedValue(capabilityOptions() as never);
    vi.mocked(createExplainerScriptRevision).mockResolvedValue({ script_revision: { id: "rev-1" }, immutable: true } as never);
    vi.mocked(preflightExplainerPlan).mockResolvedValue({ executable: true, plan_hash: "p".repeat(64), blockers: [] } as never);
    vi.mocked(startExplainerRun).mockResolvedValue({ id: "run-1", projected_status: "QUEUED" } as never);
    vi.mocked(breakdownExplainerStory).mockResolvedValue({ model_used: "test", segment_count: 1 } as never);
  });

  afterEach(() => {
    cleanup();
    draftRegistry.clear();
  });

  it("asks 你现在有什么 with two primary cards and a secondary topic entry", async () => {
    mockScript(null);
    mockOverview("PRESERVE_ORIGINAL");
    mockSegments();
    mount();

    expect(await screen.findByRole("heading", { name: "你现在有什么" })).toBeTruthy();
    const cards = screen.getAllByRole("button", { name: /已有口播稿|故事资料/ }).filter((node) => node.className.includes("explainer-intake__card"));
    expect(cards).toHaveLength(2);
    expect(screen.getByRole("button", { name: /已有口播稿/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "从主题开始（次入口）" })).toBeTruthy();
    // The two equivalent ways to supply a finished script are both offered.
    expect(screen.getByRole("button", { name: "粘贴正文" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "上传文件" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "上传文稿" })).toHaveLength(1);
  });

  it("shows no target-duration control for 已有口播稿 and registers the exact original", async () => {
    mockScript(null);
    mockOverview("PRESERVE_ORIGINAL");
    mockSegments();
    mount();

    await screen.findByRole("heading", { name: "你现在有什么" });
    // §B2.2: a finished script only ever shows its natural duration.
    expect(screen.queryByLabelText("时长")).toBeNull();
    expect(screen.queryByLabelText("自定义分钟数")).toBeNull();
    // …and there is no AI rewrite entry point in this mode at all.
    expect(screen.queryByRole("button", { name: "AI 生成讲稿" })).toBeNull();

    fireEvent.change(screen.getByLabelText("口播稿正文"), { target: { value: "第一段原文。\n\n第二段原文。" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为讲稿版本（不调用改写）" }));

    await waitFor(() => expect(createExplainerScriptRevision).toHaveBeenCalled());
    const payload = vi.mocked(createExplainerScriptRevision).mock.calls[0][1] as { segments: Array<{ display_text: string; spoken_text: string }> };
    expect(payload.segments.map((segment) => segment.display_text)).toEqual(["第一段原文。", "第二段原文。"]);
    expect(payload.segments.every((segment) => segment.spoken_text === segment.display_text)).toBe(true);
    // PRESERVE_ORIGINAL means no AI rewrite call at all.
    expect(breakdownExplainerStory).not.toHaveBeenCalled();
  });

  it("one-click on step 1 registers the script and submits the same pipeline without rewriting", async () => {
    mockScript(null);
    mockOverview("PRESERVE_ORIGINAL");
    mockSegments();
    mount();

    fireEvent.change(await screen.findByLabelText("口播稿正文"), { target: { value: "只有一段原稿。" } });
    fireEvent.click(screen.getByRole("button", { name: "一键生成到预览" }));

    await waitFor(() => expect(startExplainerRun).toHaveBeenCalled());
    expect(createExplainerScriptRevision).toHaveBeenCalled();
    expect(breakdownExplainerStory).not.toHaveBeenCalled();
    expect(preflightExplainerPlan).toHaveBeenCalledWith("project-1", expect.objectContaining({ outputs: expect.any(Array) }));
    const runPayload = vi.mocked(startExplainerRun).mock.calls[0][1] as Record<string, unknown>;
    expect(runPayload.plan_hash).toBe("p".repeat(64));
    expect(runPayload.start_workflow).toBe(true);
    expect(screen.getByText(/已提交同一条流水线/)).toBeTruthy();
  });
  it("registers the unsaved manuscript body so leaving the page is protected", async () => {
    mockScript(null);
    mockOverview("PRESERVE_ORIGINAL");
    mockSegments();
    mount();

    fireEvent.change(await screen.findByLabelText("口播稿正文"), { target: { value: "尚未保存的原稿正文。" } });
    await waitFor(() => expect(draftRegistry.get("explainer-script-intake:project-1")?.dirty).toBe(true));
    expect(draftRegistry.get("explainer-script-intake:project-1")?.entityKey).toBe("讲稿正文");
  });

  it("shows the current revision, real character count and a labelled estimate", async () => {
    mockScript(scriptFact());
    mockOverview("ADAPT_SOURCES");
    mockSegments(0, null);
    mount();

    const toolbar = await screen.findByText((_content, element) =>
      element?.tagName === "STRONG" && /当前稿 v2/.test(element.textContent ?? ""));
    expect(toolbar.textContent).toMatch(/当前稿 v2 · 约 \d+ 字 · 预计 \d+ 分/);
  });

  it("uses the measured TTS duration once real audio exists", async () => {
    mockScript(scriptFact());
    mockOverview("ADAPT_SOURCES");
    mockSegments(2, 200_000);
    mount();

    const toolbar = await screen.findByText((_content, element) =>
      element?.tagName === "STRONG" && /当前稿 v2/.test(element.textContent ?? ""));
    expect(toolbar.textContent).toContain("实测 3 分 20 秒");
  });

  it("keeps one body editor and reveals the spoken text only for 发音修正", async () => {
    mockScript(scriptFact());
    mockOverview("ADAPT_SOURCES");
    mockSegments();
    mount();

    // Only the canonical body is visible by default.
    expect(screen.queryByLabelText("朗读文本")).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: "发音修正" })[0]);
    expect(screen.getByLabelText("朗读文本")).toBeTruthy();
    expect(screen.getByText(/1962年 ➔ 一九六二年/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "发音修正" })[0]);
    expect(screen.queryByLabelText("朗读文本")).toBeNull();
  });

  it("opens the fact sources in a drawer instead of beside the body", async () => {
    mockScript(scriptFact());
    mockOverview("ADAPT_SOURCES");
    mockSegments();
    mount();

    fireEvent.click((await screen.findAllByRole("button", { name: /来源 3/ }))[0]);
    const drawer = await screen.findByRole("dialog", { name: "来源与事实" });
    expect(drawer.textContent).toContain("引用了 2 条事实");
    expect(drawer.textContent).toContain("示例来源");
  });

  it("states the real downstream impact next to 采用新稿", async () => {
    mockScript(scriptFact());
    mockOverview("ADAPT_SOURCES");
    mockSegments(2, 120_000);
    mount();

    fireEvent.click((await screen.findAllByRole("button", { name: "改写这一段" }))[0]);
    expect(screen.getByRole("button", { name: "采用新稿" })).toBeTruthy();
    expect(screen.getByText("将更新 2 段配音、4 个镜头")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("新段落正文"), { target: { value: "改写后的第一段。" } });
    fireEvent.click(screen.getByRole("button", { name: "采用新稿" }));
    await waitFor(() => expect(createExplainerScriptRevision).toHaveBeenCalled());
    const payload = vi.mocked(createExplainerScriptRevision).mock.calls[0][1] as Record<string, unknown>;
    // The old revision is kept as the source of the new one.
    expect(payload.source_script_revision_id).toBe("rev-2");
    expect((payload.segments as Array<{ display_text: string }>)[0].display_text).toBe("改写后的第一段。");
  });

  it("keeps the last known script and says 更新失败 when the refresh fails", async () => {
    mockScript(scriptFact(), { isError: true, error: new Error("网络中断") });
    mockOverview("ADAPT_SOURCES");
    mockSegments();
    mount();

    expect(await screen.findByText(/更新失败/)).toBeTruthy();
    // The last known content is still readable — not an empty “还没有素材”.
    expect(screen.getAllByText(/第一段原文内容/).length).toBeGreaterThan(0);
  });

  it("renders an empty state, not an empty script, when no revision exists", async () => {
    mockScript(null);
    mockOverview(null);
    mockSegments();
    mount();

    expect(await screen.findByText("还没有讲稿版本")).toBeTruthy();
  });

  it("moves to 人物与风格 only in stepwise mode", async () => {
    mockScript(scriptFact());
    vi.mocked(useExplainerOverview).mockReturnValue({
      data: { ...overviewFact("ADAPT_SOURCES"), video: { ...overviewFact("ADAPT_SOURCES").video, automation_mode: "MANUAL_REVIEW" } },
      isPending: false,
      isError: false,
    } as never);
    mockSegments();
    mount();

    const primary = await screen.findByRole("button", { name: "保存并下一步：人物与风格" });
    fireEvent.click(primary);
    expect(await screen.findByText("人物与风格页面")).toBeTruthy();
  });
});
