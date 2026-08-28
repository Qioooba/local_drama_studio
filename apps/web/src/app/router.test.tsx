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
import { SystemWorkflowsPage } from "../pages/SystemWorkflowsPage";
import { PostShell, ProjectSettingsShell, SystemShell } from "../pages/WorkspaceShells";

vi.mock("../generated/api", () => ({
  listProjects: () => ({ items: [] }),
  getProjectHealth: () => ({ project_id: "p", status: "OK", root_exists: true, database_integrity: "ok", media: { referenced_count: 0, missing: [], size_mismatch: [], hash_mismatch: [] }, orphan_files: [], orphan_count: 0, disk: { free_bytes: 0, total_bytes: 0 }, blockers: [], runtime_contacted: false, network_contacted: false, mutated: false }),
  getProjectCreatorSetup: () => ({ setup: { project_id: "proj-1", milestones: { episode_count: { ready: false, count: 0 }, production_plan_count: { ready: false, count: 0 }, published_profile_binding_count: { ready: false, count: 0 }, reviewable_story_draft_count: { ready: false, count: 0 }, active_story_asset_count: { ready: false, count: 0 }, shot_intent_count: { ready: false, count: 0 }, shot_generation_job_count: { ready: false, count: 0 } }, operations: { worker_ready: false, active_worker_count: 0 }, completed_count: 0, total_count: 7, observed_at: "2026-08-24T00:00:00Z", read_only: true, runtime_contacted: false, network_contacted: false, mutated: false } }),
  listSeasons: () => ({ items: [] }),
  listEpisodes: () => ({ items: [] }),
  getProjectEpisodeCatalog: () => ({ catalog: { project_id: "proj-1", seasons: [], read_only: true, runtime_contacted: false, network_contacted: false, mutated: false } }),
  getStoryboardWorkspace: () => ({ storyboard: { episode: { id: "ep-9", title: "第九集" }, items: [], views: ["TABLE"], identity_invariant: "stable", total_duration_ms: 0 } }),
  listDialogueLines: () => ({ items: [] }),
  listEpisodeProductionShotsV2: () => ({
    items: [{ shot_id: "shot-42", shot_code: "S042", overall_state: "EMPTY" }],
  }),
  getShotStudioV2: (_episodeId: string, shotId: string) => {
    const shot = {
      id: shotId, code: shotId === "shot-1" ? "S001" : shotId, order_key: "0001",
      scene_id: null, scene_code: null, scene_title: null, group_id: null, group_code: null, group_title: null,
      thumbnail_media_version_id: null, current_video_media_version_id: null, status: "DRAFT",
      continuity_status: "PENDING", job_status: null, target_duration_ms: 3000, shot_type: "中景", revision: 1,
    };
    return Promise.resolve({
      project: { id: "proj-1", code: "P1", name: "测试项目", aspect_ratio: "9:16" },
      episode: { id: "ep-9", code: "EP09", title: "第九集", status: "DRAFT", shot_count: 1, approved_count: 0, blocked_count: 0 },
      shot_nav: { items: [shot], total: 1, selected_index: 0, window_start: 0, window_end: 1, has_previous: false, has_next: false },
      current_shot: {
        shot, current_revision: { id: "rev-1", revision_no: 1, is_frozen: false, fields: {} },
        source_context: { scene_id: null, source_range: null, source_text: null },
        intent_suggestions: { environment: null, continuity: null, script: null }, assets: [], asset_states: [],
        selected_variant: null, current_media: null,
        candidates: [1, 2].map((take) => ({
          id: `variant-${take}`, intent_id: "intent-1", variant_no: take, variant_type: "KEYFRAME", parent_variant_id: null,
          branch_reason: take === 1 ? "ORIGINAL" : "USER_REROLL", status: "SUCCEEDED", is_stale: false, stale_reason: null,
          media_asset_id: `asset-${take}`, media_kind: "IMAGE", media_version_id: `media-${take}`, version_no: 1,
          take_no: take, stage: "KEYFRAME", rel_path: null, mime_type: "image/webp", duration_ms: null,
          integrity_status: "VERIFIED", selected: take === 1, approved: false, created_at: "2026-08-20T00:00:00Z",
        })),
        frame_bridge: { previous: null, current_start: null, current_end: null, next: null, compatibility: "UNKNOWN", stale: false },
        qc_summary: { subject_id: null, latest_run: null, results: [] }, review_summary: { subject_id: null, count: 0, latest: null },
        generation_preferences: { resolutions: [], available: false }, capability_options: [], active_jobs: [], blockers: [],
      },
      allowed_actions: { edit_draft: true, mark_ready: true, generate: true, adopt_working_version: true, write_review_decision: false },
      review_handoff: { subject_type: null, subject_id: null, route_kind: "REVIEW", write_owner: "REVIEW_WORKSPACE" },
      read_only: true, runtime_contacted: false, network_contacted: false, mutated: false, request_shape: "bounded_shot_studio_v2",
    });
  },
  listProfiles: () => ({ items: [] }),
  listVoiceProfileVersions: () => ({ items: [] }),
  reviewInbox: () => ({ items: [] }),
  getEpisodeTimelineStatus: () => ({ status: undefined }),
  listWorkflowVersions: () => ({ items: [] }),
}));

vi.mock("../features/episode-production-v2/EpisodeProductionWorkspace", () => ({
  EpisodeProductionWorkspace: ({ projectId, episodeId }: { projectId: string; episodeId: string }) => <section aria-label="整集生产工作台">{projectId}/{episodeId}</section>,
}));

vi.mock("../features/episode-review-v2/EpisodeReviewWorkspace", () => ({
  EpisodeReviewWorkspace: ({ projectId, episodeId }: { projectId: string; episodeId: string }) => <section aria-label="整集审核工作台">{projectId}/{episodeId}</section>,
}));

vi.mock("../features/edit-v2/EpisodeEditWorkspace", () => ({
  EpisodeEditWorkspace: ({ projectId, episodeId }: { projectId: string; episodeId: string }) => <section aria-label="时间线编排器">{projectId}/{episodeId}</section>,
}));

vi.mock("../features/audio-v2/EpisodeAudioWorkspace", () => ({
  EpisodeAudioWorkspace: ({ projectId, episodeId }: { projectId: string; episodeId: string }) => <section aria-label="整集声音工作台">{projectId}/{episodeId}</section>,
}));

vi.mock("../features/timeline-v2/TimelineExportPanel", () => ({ TimelineExportPanel: () => <section aria-label="时间线导出" /> }));
vi.mock("../features/production/SubtitleRevisionPanel", () => ({ SubtitleRevisionPanel: () => <section aria-label="字幕修订" /> }));
vi.mock("../features/status/ReadinessPanels", () => ({ TimelineStatusPanel: () => <section aria-label="时间线事实" /> }));
vi.mock("../features/shared/ComfyLabPanel", () => ({ ComfyLabPanel: () => <section aria-label="本地素材实验沙盒" /> }));

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
          { path: "settings", element: <ProjectSettingsShell />, children: [
            { path: "production", element: <ProductionSettingsPage /> },
            { path: "data", element: <ProductionSettingsPage /> },
          ] },
          { path: "episodes/:episodeId/plan", element: <EpisodePlanPage /> },
          { path: "episodes/:episodeId/studio/:shotId?", element: <DirectorDeskPage /> },
          { path: "episodes/:episodeId/production", element: <EpisodeRunPage /> },
          { path: "episodes/:episodeId/post", element: <PostShell />, children: [
            { path: "review", element: <EpisodeReviewPage /> },
            { path: "audio", element: <AudioPage /> },
            { path: "edit", element: <TimelinePage /> },
          ] },
        ],
      },
      { path: "/system", element: <AppShell />, children: [{ element: <SystemShell />, children: [{ path: "workflows", element: <SystemWorkflowsPage /> }] }] },
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
    expect((await screen.findAllByText("项目首页")).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "全局工作台" }).getAttribute("href")).toBe("/");
    expect(screen.getByRole("link", { name: "能力与模型" }).getAttribute("href")).toBe("/system/capabilities");
    expect(screen.getByRole("link", { name: "诊断与审计" }).getAttribute("href")).toBe("/system/diagnostics?project=proj-1");
    expect((await screen.findAllByText("分集")).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "制作进度" })).toBeTruthy();
  });

  it("deep-links into the asset bible page", async () => {
    renderAt("/projects/proj-1/assets");
    expect(screen.getByRole("heading", { name: "角色、场景、道具与服装" })).toBeTruthy();
  });

  it("deep-links into episode plan and director desk with optional shot id", async () => {
    renderAt("/projects/proj-1/episodes/ep-9/plan");
    expect(await screen.findByRole("tablist", { name: "分集策划任务" })).toBeTruthy();
    cleanup();
    renderAt("/projects/proj-1/episodes/ep-9/studio");
    expect(await screen.findByRole("heading", { name: "先选择要处理的镜头" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /S042/ }).getAttribute("href")).toBe(
      "/projects/proj-1/episodes/ep-9/studio/shot-42",
    );
    expect(screen.queryByRole("button", { name: /生成|重抽|批准/ })).toBeNull();
    cleanup();
    renderAt("/projects/proj-1/episodes/ep-9/studio/shot-42");
    expect(await screen.findByRole("heading", { name: /shot-42/ })).toBeTruthy();
  });

  it("uses the documented Director shortcuts without triggering approval", async () => {
    renderAt("/projects/proj-1/episodes/ep-9/studio/shot-1");
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

  it("keeps project import, export, and maintenance in the data owner", async () => {
    renderAt("/projects/proj-1/settings/data");
    expect(await screen.findByRole("heading", { name: "数据与维护" })).toBeTruthy();
  });

  it("deep-links into the expert material lab without mixing it into formal production", async () => {
    renderAt("/system/workflows?project=proj-1");
    expect(await screen.findByRole("heading", { name: "工作流与运行环境" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "本地素材实验沙盒" })).toBeTruthy();
    expect(screen.getByText(/项目只能消费已发布且兼容的版本/)).toBeTruthy();
  });

  it.each([
    ["production", "整集生产工作台"],
    ["post/review", "整集审核工作台"],
    ["post/audio", "整集声音工作台"],
    ["post/edit?view=export", "时间线编排器"],
  ])("deep-links into the episode %s workspace", async (route, regionName) => {
    renderAt(`/projects/proj-1/episodes/ep-9/${route}`);
    expect(await screen.findByRole("region", { name: regionName })).toBeTruthy();
  });
});
