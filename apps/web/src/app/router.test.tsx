import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AppShell } from "../layouts/AppShell";
import { ProjectHomePage } from "../pages/ProjectHomePage";
import { AssetBiblePage } from "../pages/AssetBiblePage";
import { DirectorDeskPage } from "../pages/DirectorDeskPage";
import { EpisodePlanPage } from "../pages/EpisodePlanPage";
import { EpisodeRunPage } from "../pages/EpisodeRunPage";
import { EpisodeReviewPage } from "../pages/EpisodeReviewPage";
import { AudioPage } from "../pages/AudioPage";
import { TimelinePage } from "../pages/TimelinePage";
import { ProductionSettingsPage } from "../pages/ProductionSettingsPage";
import { MediaLabPage } from "../pages/MediaLabPage";

vi.mock("../generated/api", () => ({
  listProjects: () => ({ items: [] }),
  getProjectHealth: () => ({ project_id: "p", status: "OK", root_exists: true, database_integrity: "ok", media: { referenced_count: 0, missing: [], size_mismatch: [], hash_mismatch: [] }, orphan_files: [], orphan_count: 0, disk: { free_bytes: 0, total_bytes: 0 }, blockers: [], runtime_contacted: false, network_contacted: false, mutated: false }),
  listSeasons: () => ({ items: [] }),
  listEpisodes: () => ({ items: [] }),
  listDialogueLines: () => ({ items: [] }),
  listEpisodeAudioBindings: () => ({ items: [] }),
  getEpisodeProduction: () => ({
    items: [{ id: "shot-42", code: "S042", status: "DRAFT" }],
  }),
  listProfiles: () => ({ items: [] }),
  listVoiceProfileVersions: () => ({ items: [] }),
  reviewInbox: () => ({ items: [] }),
  getEpisodeTimelineStatus: () => ({ status: undefined }),
}));

vi.mock("../features/episode-run-v2/EpisodeRunPanel", () => ({
  EpisodeRunPanel: ({ projectId, episodeId }: { projectId: string; episodeId: string }) => <section aria-label="整集生产工作台">{projectId}/{episodeId}</section>,
}));

vi.mock("../features/freshness/FreshnessPanel", () => ({
  FreshnessPanel: ({ scopeType, scopeId }: { scopeType: string; scopeId: string }) => <section aria-label={`Freshness ${scopeType}`}>{scopeId}</section>,
}));

vi.mock("../features/episode-review-v2/EpisodeReviewWorkspace", () => ({
  EpisodeReviewWorkspace: ({ projectId, episodeId }: { projectId: string; episodeId: string }) => <section aria-label="整集审核工作台">{projectId}/{episodeId}</section>,
}));

vi.mock("../features/audio-v2/AudioEpisodeOverview", () => ({ AudioEpisodeOverview: () => <section aria-label="声音概况" /> }));
vi.mock("../features/status/AudioTrackPanel", () => ({ AudioTrackPanel: () => <section aria-label="音轨编排" /> }));
vi.mock("../features/status/DialogueTTSPanel", () => ({ DialogueTTSPanel: () => <section aria-label="台词与语音" /> }));
vi.mock("../features/timeline-v2/TimelineComposer", () => ({ TimelineComposer: () => <section aria-label="时间线编排器" /> }));
vi.mock("../features/timeline-v2/TimelineExportPanel", () => ({ TimelineExportPanel: () => <section aria-label="时间线导出" /> }));
vi.mock("../features/production/SubtitleRevisionPanel", () => ({ SubtitleRevisionPanel: () => <section aria-label="字幕修订" /> }));
vi.mock("../features/status/ReadinessPanels", () => ({ TimelineStatusPanel: () => <section aria-label="时间线事实" /> }));
vi.mock("../features/shared/ComfyLabPanel", () => ({ ComfyLabPanel: () => <section aria-label="本地素材实验沙盒" /> }));

vi.mock("../features/director-v2/DirectorDeskClient", () => ({
  loadDirectorDesk: (_projectId: string, _episodeId: string, shotId?: string) => {
    const selectedId = shotId ?? "shot-1";
    const shot = {
      id: selectedId,
      code: selectedId === "shot-1" ? "S001" : selectedId,
      order_key: "0001",
      scene_id: null,
      group_id: null,
      thumbnail_media_version_id: null,
      status: "DRAFT",
      continuity_status: "PENDING",
      job_status: null,
      target_duration_ms: 3000,
      shot_type: "中景",
      revision: 1,
    };
    return Promise.resolve({
      project: { id: "proj-1", code: "P1", name: "测试项目", aspect_ratio: "9:16" },
      episode: { id: "ep-9", code: "EP09", title: "第九集", status: "DRAFT", shot_count: 1, approved_count: 0, blocked_count: 0 },
      shot_nav: { items: [shot], total: 1, selected_index: 0, window_start: 0, window_end: 1, has_previous: false, has_next: false },
      current_shot: {
        shot,
        current_revision: { id: "rev-1", revision_no: 1, is_frozen: false, fields: {} },
        source_context: {},
        assets: [],
        asset_states: [],
        selected_variant: null,
        current_media: null,
        candidates: [1, 2].map((take) => ({
          id: `variant-${take}`, intent_id: "intent-1", variant_no: take, variant_type: "KEYFRAME", parent_variant_id: null,
          branch_reason: take === 1 ? "ORIGINAL" : "USER_REROLL", status: "SUCCEEDED", is_stale: false, stale_reason: null,
          media_asset_id: `asset-${take}`, media_kind: "IMAGE", media_version_id: `media-${take}`, version_no: 1,
          take_no: take, stage: "KEYFRAME", rel_path: null, mime_type: "image/webp", duration_ms: null,
          integrity_status: "VERIFIED", selected: take === 1, approved: false, created_at: "2026-08-20T00:00:00Z",
        })),
        frame_bridge: { previous: null, current_start: null, current_end: null, next: null, compatibility: "UNKNOWN", stale: false },
        qc_summary: {},
        review_summary: {},
        generation_preferences: {},
        active_jobs: [],
        blockers: [],
      },
      permissions: { can_edit: true, can_generate: true, can_approve: false },
      read_only: false,
      request_shape: "bounded_director_desk_read_model",
    });
  },
  approveFormalCandidate: vi.fn(),
  rerollDirectorCandidate: vi.fn(),
  selectDirectorCandidate: vi.fn(),
}));

vi.mock("../features/director-v2/DirectorIntentEditor", () => ({
  DirectorIntentEditor: () => <div>镜头意图编辑器</div>,
}));

vi.mock("../features/director-v2/FrameBridgeControls", () => ({
  FrameBridgeControls: () => <div>镜头桥控制</div>,
}));

function renderAt(path: string) {
  const router = createMemoryRouter(
    [
      { path: "/", element: <div>v2-root-boundary</div> },
      {
        path: "/projects/:projectId",
        element: <AppShell />,
        children: [
          { index: true, element: <ProjectHomePage /> },
          { path: "assets", element: <AssetBiblePage /> },
          { path: "production-settings", element: <ProductionSettingsPage /> },
          { path: "lab", element: <MediaLabPage /> },
          { path: "episodes/:episodeId/plan", element: <EpisodePlanPage /> },
          { path: "episodes/:episodeId/direct/:shotId?", element: <DirectorDeskPage /> },
          { path: "episodes/:episodeId/run", element: <EpisodeRunPage /> },
          { path: "episodes/:episodeId/review", element: <EpisodeReviewPage /> },
          { path: "episodes/:episodeId/audio", element: <AudioPage /> },
          { path: "episodes/:episodeId/timeline", element: <TimelinePage /> },
        ],
      },
    ],
    { initialEntries: [path] },
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
}

describe("V2 router foundation", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("deep-links into a project home page", async () => {
    renderAt("/projects/proj-1");
    expect((await screen.findAllByText("项目总览")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("分集")).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "选择一集进入生产流程" })).toBeTruthy();
  });

  it("deep-links into the asset bible page", async () => {
    renderAt("/projects/proj-1/assets");
    expect((await screen.findAllByText("资产圣经")).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "角色 / 场景 / 道具 / 服装" })).toBeTruthy();
  });

  it("deep-links into episode plan and director desk with optional shot id", async () => {
    renderAt("/projects/proj-1/episodes/ep-9/plan");
    expect(await screen.findByRole("tablist", { name: "分集策划任务" })).toBeTruthy();
    cleanup();
    renderAt("/projects/proj-1/episodes/ep-9/direct");
    expect(await screen.findByRole("heading", { name: "先选择要精修的镜头" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /S042/ }).getAttribute("href")).toBe(
      "/projects/proj-1/episodes/ep-9/direct/shot-42",
    );
    expect(screen.queryByRole("button", { name: /生成|重抽|批准/ })).toBeNull();
    cleanup();
    renderAt("/projects/proj-1/episodes/ep-9/direct/shot-42");
    expect(await screen.findByRole("heading", { name: /shot-42/ })).toBeTruthy();
  });

  it("uses the documented Director shortcuts without triggering approval", async () => {
    renderAt("/projects/proj-1/episodes/ep-9/direct/shot-1");
    const first = await screen.findByRole("button", { name: /查看 Take 1/ });
    const second = screen.getByRole("button", { name: /查看 Take 2/ });
    expect(first.getAttribute("aria-pressed")).toBe("true");
    fireEvent.keyDown(window, { key: "]" });
    expect(second.getAttribute("aria-pressed")).toBe("true");
    fireEvent.keyDown(window, { key: "[" });
    expect(first.getAttribute("aria-pressed")).toBe("true");
    fireEvent.keyDown(window, { key: "a" });
    expect(screen.getByRole("tab", { name: "角色场景" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    fireEvent.keyDown(window, { key: "c" });
    expect(screen.getByRole("dialog", { name: "并排比较候选" })).toBeTruthy();
  });

  it("keeps root owned by the V2 routing boundary", async () => {
    renderAt("/");
    expect(await screen.findByText("v2-root-boundary")).toBeTruthy();
  });

  it("keeps scoped freshness reachable through the project owner and the episode run task URL", async () => {
    renderAt("/projects/proj-1/production-settings?view=freshness");
    expect((await screen.findByRole("region", { name: "Freshness PROJECT" })).textContent).toContain("proj-1");
    cleanup();
    renderAt("/projects/proj-1/episodes/ep-9/run?view=freshness");
    expect((await screen.findByRole("region", { name: "Freshness EPISODE" })).textContent).toContain("ep-9");
  });

  it("deep-links into the expert material lab without mixing it into formal production", async () => {
    renderAt("/projects/proj-1/lab");
    expect(await screen.findByRole("heading", { name: "素材实验室" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "本地素材实验沙盒" })).toBeTruthy();
    expect(screen.getByText(/不会自动进入正式资产/)).toBeTruthy();
  });

  it.each([
    ["run", "整集生产工作台"],
    ["review", "整集审核工作台"],
    ["audio", "台词与语音"],
    ["timeline?view=export", "时间线导出"],
  ])("deep-links into the episode %s workspace", async (route, regionName) => {
    renderAt(`/projects/proj-1/episodes/ep-9/${route}`);
    expect(await screen.findByRole("region", { name: regionName })).toBeTruthy();
  });
});
