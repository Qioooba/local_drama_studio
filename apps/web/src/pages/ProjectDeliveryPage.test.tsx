import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  commitEpisodeRenderReviewBatch,
  commitEpisodeDeliverySelections,
  commitVideoUpscaleCleanup,
  createVideoUpscaleBatch,
  createVideoUpscalePlan,
  createVideoUpscalePreview,
  getProjectConfiguration,
  getVideoUpscaleOptions,
  getVideoUpscalePlan,
  getVideoUpscaleRun,
  listEpisodeDeliveryVersions,
  listReviewTemplates,
  listProjectDeliveryEpisodes,
  listVideoUpscaleBatches,
  listVideoUpscaleDeliveryBuildBatches,
  planEpisodeDeliverySelections,
  planEpisodeRenderReviewBatch,
  planVideoUpscaleCleanup,
  resolveVideoUpscaleSelection,
} from "../generated/api";
import { ProjectDeliveryPage } from "./ProjectDeliveryPage";

vi.mock("../generated/api", () => ({
  commitEpisodeRenderReviewBatch: vi.fn(),
  commitVideoUpscaleCleanup: vi.fn(),
  commitEpisodeDeliverySelections: vi.fn(),
  controlVideoUpscaleBatch: vi.fn(),
  createVideoUpscaleBatch: vi.fn(),
  createVideoUpscalePlan: vi.fn(),
  createVideoUpscalePreview: vi.fn(),
  getProjectConfiguration: vi.fn(),
  getVideoUpscaleOptions: vi.fn(),
  getVideoUpscalePlan: vi.fn(),
  getVideoUpscaleRun: vi.fn(),
  listEpisodeDeliveryVersions: vi.fn(),
  listReviewTemplates: vi.fn(),
  listProjectDeliveryEpisodes: vi.fn(),
  listVideoUpscaleBatches: vi.fn(),
  listVideoUpscaleDeliveryBuildBatches: vi.fn(),
  planEpisodeDeliverySelections: vi.fn(),
  planEpisodeRenderReviewBatch: vi.fn(),
  planVideoUpscaleCleanup: vi.fn(),
  planVideoUpscaleDeliveryBuildBatch: vi.fn(),
  resolveVideoUpscaleSelection: vi.fn(),
  retryFailedVideoUpscaleDeliveryBuildBatch: vi.fn(),
  submitVideoUpscaleDeliveryBuildBatch: vi.fn(),
}));

const episode = {
  episode: { id: "episode-1", code: "EP01", title: "第一集", number: 1, season_id: "season-1", season_number: 1, season_title: "第一季" },
  compose: { id: "render-1", revision: 1, integrity_status: "VERIFIED", sha256: "a".repeat(64), duration_ms: 60_000, width: 854, height: 480, approval: { id: "approval-1", decision: "APPROVED", is_stale: false, valid: true } },
  source_choices: [],
  recommended_source: { kind: "EPISODE_RENDER", geometry: { target: { width: 1920, height: 1080 } } },
  selectable: true,
  blockers: [],
  warnings: [],
  derived_version_count: 0,
  delivery_selections: [],
};

const readyPlan = {
  id: "plan-1",
  project_id: "project-1",
  status: "READY" as const,
  plan_hash: "b".repeat(64),
  expires_at: "2099-01-01T00:00:00Z",
  revision: 2,
  items: [{ episode_id: "episode-1", disposition: "NEW", geometry: {}, frame_count: 1440, blockers: [], warnings: [] }],
};

function renderPage(initialEntry = "/projects/project-1/delivery") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes><Route path="/projects/:projectId/delivery" element={<ProjectDeliveryPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProjectDeliveryPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    vi.mocked(listProjectDeliveryEpisodes).mockResolvedValue({ project_id: "project-1", items: [episode], page: { cursor: 0, limit: 50, total: 1, next_cursor: null } } as never);
    vi.mocked(getVideoUpscaleOptions).mockResolvedValue({
      project_id: "project-1",
      settings: { project_id: "project-1", preset_id: "preset", preset_title: "漫剧 1080p", preset_version_id: "preset-v1", overrides: { pipeline: {}, model: {} }, revision: 0, inherited: true },
      presets: [{ id: "preset", code: "ANIME", title: "漫剧 1080p", builtin: true, current_version_id: "preset-v1", version_id: "preset-v1", version_no: 1, profile_version_id: "profile-v1", pipeline_options: { chunk_frames: 240, crf: 18 }, model_options: { tile_size: 0 }, available: true, unavailable_reason: null }],
      profiles: [{ profile_version_id: "profile-v1", title: "Real-ESRGAN NCNN", version_no: 1, adapter_code: "ncnn.realesrgan.video.v1", ready: true, payload: {} }],
      pipeline_contract: {}, model_contract: {}, runtime_contacted: false, network_contacted: false, mutated: false,
    });
    vi.mocked(listVideoUpscaleBatches).mockResolvedValue({ items: [], page: {} });
    vi.mocked(listVideoUpscaleDeliveryBuildBatches).mockResolvedValue({ items: [], page: {} });
    vi.mocked(resolveVideoUpscaleSelection).mockResolvedValue({ selection: { selection_hash: "c".repeat(64), count: 1, items: [{ episode_id: "episode-1", source: {} }], blocked: [] } });
    vi.mocked(createVideoUpscalePlan).mockResolvedValue({ plan: { ...readyPlan, status: "CHECKING" }, job: {} as never, idempotent_replay: false });
    vi.mocked(getVideoUpscalePlan).mockResolvedValue({ plan: readyPlan });
    vi.mocked(createVideoUpscaleBatch).mockResolvedValue({ batch: {} as never, jobs: [], idempotent_replay: false });
    vi.mocked(createVideoUpscalePreview).mockResolvedValue({ run: { id: "preview-1" } as never, idempotent_replay: false });
    vi.mocked(getVideoUpscaleRun).mockResolvedValue({ run: { id: "preview-1", purpose: "PREVIEW", project_id: "project-1", episode_id: "episode-1", job_id: "job-1", job_state: "SUCCEEDED", output_render_id: null, output_rel_path: "preview.mp4", output_sha256: "d".repeat(64), sample_start_ms: 0, sample_duration_ms: 5000, content_url: "/api/v1/video-upscale-previews/preview-1/content", source_content_url: "/api/v1/video-upscale-previews/preview-1/source-content", progress: {}, progress_summary: {} } });
    vi.mocked(planVideoUpscaleCleanup).mockResolvedValue({ plan: { schema_version: "localdrama.video-upscale-cleanup-plan.v1", project_id: "project-1", retention_days: 7, eligible_before: "2026-09-14T00:00:00+00:00", plan_hash: "e".repeat(64), candidate_count: 2, reclaimable_bytes: 2_621_440, candidates: [], skipped: [], mutated: false, runtime_contacted: false, network_contacted: false } });
    vi.mocked(commitVideoUpscaleCleanup).mockResolvedValue({ result: { deleted_count: 2, released_bytes: 2_621_440, mutated: true } });
    vi.mocked(getProjectConfiguration).mockResolvedValue({ configuration: { delivery_targets: [], selected_delivery_target_version_id: null } } as never);
    vi.mocked(listEpisodeDeliveryVersions).mockResolvedValue({ versions: { episode_id: "episode-1", project_id: "project-1", current_root_compose_render_id: "render-1", items: [], selections: [] } } as never);
    vi.mocked(listReviewTemplates).mockResolvedValue({ items: [{ id: "upscale-template-v1", code: "episode_upscale", version_no: 1, subject_type: "EPISODE_RENDER_VERSION", items: [{ id: "faces_identity", label: "人物脸部与身份稳定", required: true }, { id: "audio_sync", label: "声音同步且音轨完整", required: true }] }] });
    vi.mocked(planEpisodeRenderReviewBatch).mockResolvedValue({ plan: { plan_id: "review-plan-1", plan_token: "secure-review-plan-token-value", plan_hash: "a".repeat(64), expires_at: "2099-01-01T00:00:00Z", status: "READY", items: [], would_create_review_count: 2, mutated_reviews: false } });
    vi.mocked(commitEpisodeRenderReviewBatch).mockResolvedValue({ commit: { plan_id: "review-plan-1", plan_hash: "a".repeat(64), status: "COMMITTED", items: [], review_count: 2, atomic: true } });
  });

  it("selects an approved episode, freezes a plan, and creates a non-deliverable five-second preview", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "整剧交付" })).toBeTruthy();
    expect(await screen.findByText("854×480")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 EP01 第一集" }));
    fireEvent.click(screen.getByRole("button", { name: "检查所选 1 集" }));
    await waitFor(() => expect(createVideoUpscalePlan).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("预检：READY")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "试跑首集 5 秒" }));
    await waitFor(() => expect(createVideoUpscalePreview).toHaveBeenCalledWith(
      "project-1",
      expect.objectContaining({ episode_id: "episode-1", duration_ms: 5000 }),
      expect.any(String),
    ));
    expect(await screen.findByLabelText("AI 超分五秒样片")).toBeTruthy();
    expect(screen.getByText(/不会登记为正式成片/)).toBeTruthy();
    expect(screen.getByText(/不使用低码率预览代理/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "进入 100% 像素裁切" }));
    expect(screen.getByRole("button", { name: "退出 100% 像素裁切" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("group", { name: "两侧同步裁切位置" })).toBeTruthy();
  });

  it("exposes a mixed state when only part of the selectable page is checked", async () => {
    const second = { ...episode, episode: { ...episode.episode, id: "episode-2", code: "EP02", number: 2 } };
    vi.mocked(listProjectDeliveryEpisodes).mockResolvedValue({ project_id: "project-1", items: [episode, second], page: { cursor: 0, limit: 50, total: 2, next_cursor: null } } as never);
    renderPage();
    fireEvent.click(await screen.findByRole("checkbox", { name: "选择 EP01 第一集" }));
    expect(screen.getByRole("checkbox", { name: "选择本页可处理项" }).getAttribute("aria-checked")).toBe("mixed");
  });

  it("restores the exact project-scoped selection after a page reload", async () => {
    const first = renderPage();
    fireEvent.click(await screen.findByRole("checkbox", { name: "选择 EP01 第一集" }));
    expect(window.sessionStorage.getItem("localdrama.project-delivery.selection.project-1")).toContain("episode-1");
    first.unmount();

    renderPage();
    await waitFor(() => expect((screen.getByRole("checkbox", { name: "选择 EP01 第一集" }) as HTMLInputElement).checked).toBe(true));
  });

  it("adopts each orientation into an exact matching target instead of the project-wide target", async () => {
    vi.mocked(getProjectConfiguration).mockResolvedValue({ configuration: {
      delivery_targets: [
        { target_id: "landscape", code: "landscape", title: "横屏 1080p", transport: "LOCAL_FILESYSTEM", target_status: "INACTIVE", version_id: "target-landscape", version_no: 1, version_status: "RETIRED", spec: { width: 1920, height: 1080 }, delivery_package_count: 0 },
        { target_id: "portrait", code: "portrait", title: "竖屏 1080p", transport: "LOCAL_FILESYSTEM", target_status: "ACTIVE", version_id: "target-portrait", version_no: 1, version_status: "ACTIVE", spec: { width: 1080, height: 1920 }, delivery_package_count: 0 },
      ],
      selected_delivery_target_version_id: "target-landscape",
    } } as never);
    vi.mocked(listEpisodeDeliveryVersions).mockResolvedValue({ versions: {
      episode_id: "episode-1", project_id: "project-1", current_root_compose_render_id: "render-1", selections: [],
      items: [{ id: "portrait-sr", render_kind: "SUPER_RESOLUTION", parent_render_version_id: "render-1", integrity_status: "VERIFIED", approved: true, machine_qc_passed: true, source_current: true, adoptable: true, stale_reason: null, revision: 1, created_at: "2026-09-21T00:00:00Z", probe: { streams: [{ codec_type: "video", width: 1080, height: 1920 }] } }],
    } } as never);
    vi.mocked(planEpisodeDeliverySelections).mockResolvedValue({ plan: { plan_hash: "f".repeat(64) } } as never);
    vi.mocked(commitEpisodeDeliverySelections).mockResolvedValue({ commit: {} } as never);

    renderPage("/projects/project-1/delivery?view=versions");
    expect(await screen.findByText(/目标 竖屏 1080p/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "采用到匹配交付目标" }));
    await waitFor(() => expect(planEpisodeDeliverySelections).toHaveBeenCalledWith(
      "project-1",
      [expect.objectContaining({ target_slot: "target-portrait", selected_render_id: "portrait-sr" })],
    ));
  });

  it("previews and explicitly confirms safe cleanup of expired intermediates", async () => {
    renderPage("/projects/project-1/delivery?view=queue");
    fireEvent.click(await screen.findByRole("button", { name: "预览过期中间文件" }));
    expect(await screen.findByText(/2 个运行目录/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "确认清理 2 项" }));
    await waitFor(() => expect(commitVideoUpscaleCleanup).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/已安全清理 2 个运行目录/)).toBeTruthy();
  });

  it("requires independent per-episode checks before atomically approving a batch", async () => {
    const second = { ...episode, episode: { ...episode.episode, id: "episode-2", code: "EP02", title: "第二集", number: 2 } };
    vi.mocked(listProjectDeliveryEpisodes).mockResolvedValue({ project_id: "project-1", items: [episode, second], page: { cursor: 0, limit: 50, total: 2, next_cursor: null } } as never);
    const page = (episodeId: string, renderId: string) => ({ versions: {
      episode_id: episodeId, project_id: "project-1", current_root_compose_render_id: `root-${episodeId}`, selections: [],
      items: [{ id: renderId, render_kind: "SUPER_RESOLUTION", parent_render_version_id: `root-${episodeId}`, integrity_status: "VERIFIED", approved: false, machine_qc_passed: true, source_current: true, adoptable: false, stale_reason: null, revision: 1, created_at: "2026-09-21T00:00:00Z", probe: { streams: [{ codec_type: "video", width: 1920, height: 1080 }] } }],
    } });
    vi.mocked(listEpisodeDeliveryVersions).mockImplementation(async (episodeId) => page(episodeId, episodeId === "episode-1" ? "sr-1" : "sr-2") as never);
    window.sessionStorage.setItem("localdrama.project-delivery.selection.project-1", JSON.stringify(["episode-1", "episode-2"]));

    renderPage("/projects/project-1/delivery?view=versions&episodeId=episode-1");
    const firstReview = await screen.findByRole("group", { name: /1\. EP01 · 第一集/ });
    const secondReview = screen.getByRole("group", { name: /2\. EP02 · 第二集/ });
    const planButton = screen.getByRole("button", { name: "检查 2 集审核计划" });
    expect((planButton as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: /全部通过/ })).toBeNull();

    within(firstReview).getAllByRole("checkbox").forEach((checkbox) => fireEvent.click(checkbox));
    expect((planButton as HTMLButtonElement).disabled).toBe(true);
    within(secondReview).getAllByRole("checkbox").forEach((checkbox) => fireEvent.click(checkbox));
    expect((planButton as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(planButton);
    await waitFor(() => expect(planEpisodeRenderReviewBatch).toHaveBeenCalledWith(
      "project-1",
      [
        expect.objectContaining({ render_id: "sr-1", checks: expect.arrayContaining([expect.objectContaining({ item_id: "faces_identity" }), expect.objectContaining({ item_id: "audio_sync" })]) }),
        expect.objectContaining({ render_id: "sr-2", checks: expect.arrayContaining([expect.objectContaining({ item_id: "faces_identity" }), expect.objectContaining({ item_id: "audio_sync" })]) }),
      ],
    ));
    fireEvent.click(await screen.findByRole("button", { name: "确认原子批准 2 集" }));
    await waitFor(() => expect(commitEpisodeRenderReviewBatch).toHaveBeenCalledWith("secure-review-plan-token-value", "a".repeat(64)));
    expect(await screen.findByText(/已原子批准 2 集超分成片/)).toBeTruthy();
  });

  it("pages through 51 delivery episodes, reports the server total, and keeps the selection across pages", async () => {
    const many = Array.from({ length: 51 }, (_, index) => ({
      ...episode,
      episode: { ...episode.episode, id: `episode-${index + 1}`, code: `EP${String(index + 1).padStart(2, "0")}`, title: `第 ${index + 1} 集` },
    }));
    vi.mocked(listProjectDeliveryEpisodes).mockImplementation(async (_projectId: string, options: { cursor?: number } = {}) => {
      const cursor = options.cursor ?? 0;
      const items = many.slice(cursor, cursor + 50);
      const next = cursor + 50 < many.length ? cursor + 50 : null;
      return { project_id: "project-1", items, page: { cursor, limit: 50, total: many.length, next_cursor: next } } as never;
    });

    renderPage();
    await screen.findByText(/已加载 50 集 \/ 共 51 集（还有更多）/);
    // Cross-page selection: page-1 selection must survive loading page 2.
    fireEvent.click(screen.getByRole("checkbox", { name: /第 1 集/ }));
    expect(screen.getByText(/已选/).textContent).toContain("1");

    fireEvent.click(screen.getByRole("button", { name: /加载更多分集/ }));
    await waitFor(() => expect(listProjectDeliveryEpisodes).toHaveBeenCalledWith("project-1", expect.objectContaining({ cursor: 50 })));
    expect(await screen.findByText(/已加载 51 集 \/ 共 51 集（已到末页）/)).toBeTruthy();
    expect(screen.getByText(/已选/).textContent).toContain("1");
    expect(screen.queryByRole("button", { name: /加载更多分集/ })).toBeNull();
  });

  it("search is sent to the server for the whole delivery set", async () => {
    renderPage();
    await screen.findByText(/已加载 1 集/);
    fireEvent.change(screen.getByRole("textbox", { name: "搜索分集" }), { target: { value: "第八十一集" } });
    await waitFor(() => expect(listProjectDeliveryEpisodes).toHaveBeenLastCalledWith("project-1", expect.objectContaining({ search: "第八十一集", cursor: 0 })));
  });
});
