/**
 * Explainer factory workspace tests.
 *
 * These verify the decisions the design says must not drift structurally:
 * the explainer route branch never resolves an episode context, the six pages
 * are reachable, machine acceptance is never rendered as human review, coverage
 * layers stay separate, and the eight documented page states are expressible.
 *
 * Matchers are plain Vitest assertions; this repository does not register
 * jest-dom matchers globally.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../generated/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../generated/api")>();
  return {
    ...actual,
    listExplainers: vi.fn(),
    getExplainerOverview: vi.fn(),
    getExplainerScript: vi.fn(),
    listExplainerAssets: vi.fn(),
    listExplainerBeats: vi.fn(),
    listExplainerEditions: vi.fn(),
    getExplainerQc: vi.fn(),
    getExplainerNarration: vi.fn(),
    getExplainerSubtitles: vi.fn(),
    listExplainerSchedules: vi.fn(),
  };
});

import * as api from "../../generated/api";
import { ExplainerFactoryPage } from "./FactoryPage";
import { ExplainerWorkspaceShell } from "./ExplainerWorkspaceShell";
import { ExplainerReviewPage } from "./ReviewPage";
import { ExplainerAudioPage } from "./AudioPage";
import { StateNotice, type PageState } from "./components";
import { coverageRows, plannedVsActual, runStatusLabel, stepStatusLabel } from "./viewModels";
import { parseRouteContext, routes } from "../../app/routeRegistry";

const WORKSPACE = {
  project_id: "p1",
  video: {
    id: "v1",
    project_id: "p1",
    title: "灯塔最后一页值班记录",
    topic: "观察窗为什么短暂无光",
    content_kind: "ORIGINAL_FICTION" as const,
    status: "ACTIVE",
    target_seconds: 300,
    duration_mode: "TARGET" as const,
    tolerance_percent: 5,
    automation_mode: "AUTO_WITH_EXCEPTIONS" as const,
    inference_mode: "LOCAL_ONLY",
    research_mode: "OFFLINE_IMPORT",
    current_script_revision_id: null,
    revision: 1,
  },
  editions: [],
  beat_count: 0,
  render_type_counts: {},
  latest_run: null,
  open_issues: [],
  open_issue_count: 0,
  blocking_issue_count: 0,
  active_decisions: [],
  authority_labels: { machine: "自动检查结果", human: "人工确认", publication: "发布授权" },
  capability_snapshot: { probed: true, capabilities: [], unknown_count: 0, unavailable_count: 0 },
};

const EDITION = {
  id: "e1",
  edition_key: "zh-clean-169",
  voice_locale: "zh-CN",
  aspect_ratio: "16:9",
  subtitle_mode: "NONE",
  duration_policy: "NATURAL_NARRATION",
  subtitle_locales_json: ["zh-CN"],
};

function renderWithProviders(element: React.ReactElement, path = "/explainers/p1/overview") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listExplainers).mockResolvedValue({
    items: [],
    next_cursor: null,
    product_kind_filter: "EXPLAINER",
  });
  vi.mocked(api.getExplainerOverview).mockResolvedValue(WORKSPACE as never);
  vi.mocked(api.getExplainerScript).mockResolvedValue({
    video_id: "v1",
    revision: null,
    chapters: [],
    segments: [],
    claims: [],
    sources: [],
    empty_state: "NO_SCRIPT_REVISION",
  });
  vi.mocked(api.listExplainerAssets).mockResolvedValue({
    video_id: "v1",
    entities: [],
    entity_counts: {},
    channel_profile_version: null,
    channel_profile_is_frozen_snapshot: true,
    three_view_is_display_only: true,
  });
  vi.mocked(api.listExplainerBeats).mockResolvedValue({
    video_id: "v1",
    beats: [],
    render_type_counts: {},
    actual_render_type_counts: {},
    planned_and_actual_reported_separately: true,
  });
  vi.mocked(api.listExplainerEditions).mockResolvedValue({
    video_id: "v1",
    editions: [],
    independent_clocks: {},
    english_timing_copied_from_source_locale: false,
  });
  vi.mocked(api.getExplainerQc).mockResolvedValue({
    edition_id: "e1",
    video_id: "v1",
    subject: { kind: "EDITION", revision_id: "e1", hash: null },
    report: null,
    status: "NOT_RUN",
    coverage: {
      total_frames: 7500,
      decoded_frames: 0,
      technical_checked_frames: 0,
      semantic_checked_frames: 0,
      human_reviewed_frames: 0,
    },
    coverage_layers_reported_separately: true,
    decoded_is_not_semantic: true,
    issues: [],
    open_issues: [],
    unverified_checks: ["MEDIA_NOT_GENERATED"],
    machine_decision: null,
    human_decision: null,
    publication_decision: null,
    automatic_pass_does_not_mean_human_review: true,
  });
  vi.mocked(api.getExplainerNarration).mockResolvedValue({
    edition_id: "e1",
    video_id: "v1",
    locale: "zh-CN",
    segments: [],
    takes: [],
    alignments: [],
    measured_total_ms: null,
    independent_clock: true,
    clock_source: "NATURAL_NARRATION",
    null_means_not_generated: true,
  });
  vi.mocked(api.getExplainerSubtitles).mockResolvedValue({
    edition_id: "e1",
    locale: "zh-CN",
    revision: null,
    cues: [],
    empty_state: "NO_SUBTITLE_REVISION",
  });
  vi.mocked(api.listExplainerSchedules).mockResolvedValue({
    items: [],
    scheduling_runs_in_local_worker: true,
    browser_timer_used: false,
    trigger_key: ["schedule_id", "scheduled_for"],
  });
});

describe("explainer routing", () => {
  it("puts the explainer workspace in its own scope without an episode", () => {
    const context = parseRouteContext("/explainers/p1/storyboard");
    expect(context.scope).toBe("EXPLAINER");
    expect(context.episodeId).toBeNull();
    expect(context.shotId).toBeNull();
    expect(context.explainerPage).toBe("storyboard");
    expect(routes.explainerPage("p1", "review")).toBe("/explainers/p1/review");
  });

  it("fails closed for an unknown explainer sub-page", () => {
    expect(parseRouteContext("/explainers/p1/episodes/e1/plan").routeId).toBeNull();
    expect(parseRouteContext("/explainers/p1/not-a-page").routeId).toBeNull();
  });
});

describe("explainer factory page", () => {
  it("states that an explainer workspace is not an episode and offers creation", async () => {
    renderWithProviders(<ExplainerFactoryPage />, "/explainers");
    expect(screen.getByRole("heading", { name: "把一个事件，讲成一部完整视频" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "＋ 新建解说" })).toBeTruthy();
    await waitFor(() => expect(api.listExplainers).toHaveBeenCalled());
    expect(screen.getByText(/解说作品不显示季、集或短剧对白入口/)).toBeTruthy();
  });
});

describe("explainer workspace shell", () => {
  it("renders the six pages", async () => {
    renderWithProviders(
      <Routes>
        <Route path="/explainers/:projectId" element={<ExplainerWorkspaceShell />}>
          <Route path="overview" element={<p>overview-body</p>} />
        </Route>
      </Routes>,
    );
    for (const label of ["总览与生产", "资料与解说稿", "人物与风格", "分镜与画面", "声音与字幕", "审片与导出"]) {
      expect(screen.getByRole("link", { name: label })).toBeTruthy();
    }
    await waitFor(() => expect(screen.getByText("overview-body")).toBeTruthy());
  });

  it("never renders a season or episode context", async () => {
    renderWithProviders(
      <Routes>
        <Route path="/explainers/:projectId" element={<ExplainerWorkspaceShell />}>
          <Route path="overview" element={<p>overview-body</p>} />
        </Route>
      </Routes>,
    );
    await waitFor(() => expect(screen.getByText("overview-body")).toBeTruthy());
    expect(screen.queryByText(/分集/)).toBeNull();
    expect(screen.queryByText(/整剧交付/)).toBeNull();
  });
});

describe("explainer page states", () => {
  const states: PageState[] = [
    { kind: "loading", message: "正在载入…" },
    { kind: "empty", title: "还没有讲稿版本", body: "先导入资料。" },
    { kind: "no_capability", title: "尚未生成配音", body: "没有音频时时长保持为空。" },
    { kind: "running", title: "生产进行中", body: "覆盖报告稍后更新。", progress: 0.4 },
    { kind: "partial", title: "部分完成", body: "只补未完成部分。" },
    { kind: "failed", title: "生产失败", body: "失败步骤只锁定依赖其结果。" },
    { kind: "stale", title: "质检报告已过期", body: "对象哈希已变化。" },
  ];

  it("expresses all seven non-success documented states", () => {
    for (const state of states) {
      const { unmount } = render(<StateNotice state={state} />);
      const expected = state.kind === "loading" ? state.message : state.title;
      expect(screen.getByText(expected as string)).toBeTruthy();
      unmount();
    }
  });

  it("raises an alert only for the failed state", () => {
    const failed = render(<StateNotice state={{ kind: "failed", title: "失败", body: "原因" }} />);
    expect(screen.getByRole("alert").textContent).toContain("失败");
    failed.unmount();
    const partial = render(<StateNotice state={{ kind: "partial", title: "部分", body: "原因" }} />);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("部分");
  });

  it("renders nothing when there is no state", () => {
    const { container } = render(<StateNotice state={null} />);
    expect(container.innerHTML).toBe("");
  });
});

describe("explainer review page", () => {
  it("reports coverage layers separately and shows unresolved checks", async () => {
    vi.mocked(api.listExplainerEditions).mockResolvedValue({
      video_id: "v1",
      editions: [{ ...EDITION, current_render: { id: "render-1", integrity_status: "VERIFIED", sha256: "a".repeat(64) } }],
      independent_clocks: {},
      english_timing_copied_from_source_locale: false,
    });
    renderWithProviders(
      <Routes>
        <Route path="/explainers/:projectId/review" element={<ExplainerReviewPage />} />
      </Routes>,
      "/explainers/p1/review",
    );
    await waitFor(() => expect(screen.getByText("审查覆盖范围")).toBeTruthy());
    expect(screen.getByText("技术解码")).toBeTruthy();
    expect(screen.getByText("视觉语义")).toBeTruthy();
    expect(screen.getByText("人工审阅")).toBeTruthy();
    await waitFor(() =>
      expect(document.body.textContent ?? "").toContain("未检查项：MEDIA_NOT_GENERATED"),
    );
    expect(screen.getByText(/技术全量解码 100% 不等于全帧语义理解/)).toBeTruthy();
  });

  it("states that there is no reviewable film instead of showing an empty success", async () => {
    // With no edition yet the page must say production has not happened, not
    // render a review surface that looks complete.
    renderWithProviders(
      <Routes>
        <Route path="/explainers/:projectId/review" element={<ExplainerReviewPage />} />
      </Routes>,
      "/explainers/p1/review",
    );
    await waitFor(() => expect(screen.getByText("还没有输出版本")).toBeTruthy());
    expect(screen.getByText(/先在总览提交预检并完成生产/)).toBeTruthy();
  });

  it("distinguishes an edition without a render from a reviewable film", async () => {
    vi.mocked(api.listExplainerEditions).mockResolvedValue({
      video_id: "v1",
      editions: [EDITION],
      independent_clocks: {},
      english_timing_copied_from_source_locale: false,
    });
    renderWithProviders(
      <Routes>
        <Route path="/explainers/:projectId/review" element={<ExplainerReviewPage />} />
      </Routes>,
      "/explainers/p1/review",
    );
    await waitFor(() => expect(screen.getByText("还没有可审的成片")).toBeTruthy());
    expect(screen.getByText(/已提交渲染不等于制作成功/)).toBeTruthy();
  });

  it("never presents machine acceptance as human review", async () => {
    vi.mocked(api.listExplainerEditions).mockResolvedValue({
      video_id: "v1",
      editions: [{ ...EDITION, current_render: { id: "render-1", integrity_status: "VERIFIED", sha256: "a".repeat(64) } }],
      independent_clocks: {},
      english_timing_copied_from_source_locale: false,
    });
    renderWithProviders(
      <Routes>
        <Route path="/explainers/:projectId/review" element={<ExplainerReviewPage />} />
      </Routes>,
      "/explainers/p1/review",
    );
    await waitFor(() => expect(screen.getByText("人工确认与发布授权")).toBeTruthy());
    expect(screen.getByText("尚无审批记录")).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认当前成片" })).toBeTruthy();
    expect(screen.queryByText("已人工确认")).toBeNull();
    expect(screen.getByText(/HTTP 客户端不能自填机器接受/)).toBeTruthy();
  });
});

describe("explainer audio page", () => {
  it("shows an explicit no-capability state when no narration exists", async () => {
    vi.mocked(api.listExplainerEditions).mockResolvedValue({
      video_id: "v1",
      editions: [EDITION],
      independent_clocks: {},
      english_timing_copied_from_source_locale: false,
    });
    renderWithProviders(
      <Routes>
        <Route path="/explainers/:projectId/audio" element={<ExplainerAudioPage />} />
      </Routes>,
      "/explainers/p1/audio",
    );
    await waitFor(() => expect(screen.getByText("该版本还没有绑定讲稿")).toBeTruthy());
    expect(screen.getByText(/英文版使用独立英文 TTS 时钟/)).toBeTruthy();
  });

  it("says the duration is not measured rather than showing a fabricated value", async () => {
    vi.mocked(api.listExplainerEditions).mockResolvedValue({
      video_id: "v1",
      editions: [EDITION],
      independent_clocks: {},
      english_timing_copied_from_source_locale: false,
    });
    vi.mocked(api.getExplainerNarration).mockResolvedValue({
      edition_id: "e1",
      video_id: "v1",
      locale: "zh-CN",
      segments: [
        {
          id: "s1",
          canonical_segment_id: "seg_001",
          display_text: "一九三六年，一个虚构的冬夜。",
          spoken_text: "一九三六年，一个虚构的冬夜。",
          locale: "zh-CN",
          ordinal: 0,
        },
      ],
      takes: [],
      alignments: [],
      measured_total_ms: null,
      independent_clock: true,
      clock_source: "NATURAL_NARRATION",
      null_means_not_generated: true,
    });
    renderWithProviders(
      <Routes>
        <Route path="/explainers/:projectId/audio" element={<ExplainerAudioPage />} />
      </Routes>,
      "/explainers/p1/audio",
    );
    await waitFor(() => expect(screen.getByText("尚未生成配音")).toBeTruthy());
    expect(screen.getByText(/不会补零并标记成功/)).toBeTruthy();
    expect(screen.getAllByText("时长待实测").length).toBeGreaterThan(0);
  });
});

describe("explainer view models", () => {
  it("keeps planned and actual render types apart", () => {
    expect(plannedVsActual({ render_type: "I2V" })).toEqual({ planned: "I2V", actual: null, degraded: false });
    expect(plannedVsActual({ render_type: "I2V", render_type_actual: "STILL_MOTION" })).toEqual({
      planned: "I2V",
      actual: "STILL_MOTION",
      degraded: true,
    });
  });

  it("labels run and step states in Chinese without inventing a state", () => {
    expect(runStatusLabel("WAITING_INPUT")).toBe("等待处理");
    expect(runStatusLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
    expect(stepStatusLabel("RETRYABLE_FAILED")).toBe("可重试失败");
  });

  it("reports decode coverage and semantic coverage as separate rows", () => {
    const rows = coverageRows({
      total_frames: 7500,
      decoded_frames: 7500,
      technical_checked_frames: 7500,
      semantic_checked_frames: 750,
      human_reviewed_frames: 0,
    });
    expect(rows.map((row) => row.key)).toEqual(["decoded", "technical", "semantic", "human"]);
    expect(rows[0].detail).toContain("100%");
    expect(rows[2].detail).toContain("10%");
    expect(rows[0].note).toContain("不等于内容理解");
  });

  it("shows 未执行 rather than 0% when nothing was measured", () => {
    const rows = coverageRows({ total_frames: 0 });
    expect(rows[0].detail).toBe("未执行");
  });
});
