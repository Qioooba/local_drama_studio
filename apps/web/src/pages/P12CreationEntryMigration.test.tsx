import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getEpisodePostOverviewV2, getEpisodeTimelineStatus, getG8Readiness, getProjectConfiguration, reviewInbox } from "../generated/api";
import { DeliveryPage } from "./DeliveryPage";
import { EpisodePlanPage } from "./EpisodePlanPage";

vi.mock("../generated/api", () => ({ getEpisodePostOverviewV2: vi.fn(), getEpisodeTimelineStatus: vi.fn(), getG8Readiness: vi.fn(), getProjectConfiguration: vi.fn(), reviewInbox: vi.fn() }));
vi.mock("../features/projects/ScriptImportPanel", () => ({ ScriptImportPanel: () => null }));
vi.mock("../features/episode-production-v2/EpisodeProductionWorkspace", () => ({ EpisodeProductionWorkspace: () => <section aria-label="本集 Agent 制作">单集自动生产</section> }));
vi.mock("../features/projects/AIDraftReviewPanel", () => ({ AIDraftReviewPanel: () => null }));
vi.mock("../features/projects/StoryboardBatchWorkbench", () => ({ StoryboardBatchWorkbench: () => null }));
vi.mock("../features/episode-plan-v2/ShotGroupPlanner", () => ({ ShotGroupPlanner: () => null }));
vi.mock("../features/episode-plan-v2/SelectedBeatReplanPanel", () => ({ SelectedBeatReplanPanel: () => null }));
vi.mock("../features/episode-plan-v2/AssetProposalReviewPanel", () => ({ AssetProposalReviewPanel: () => null }));
vi.mock("../features/source-passage/EpisodeSourcePassage", () => ({ EpisodeSourcePassage: () => null }));
vi.mock("../features/projects/EpisodeSceneRanges", () => ({ EpisodeSceneRanges: ({ episodeId }: { episodeId: string }) => <div>场次范围 {episodeId}</div> }));
vi.mock("../features/production/PromptTemplatePanel", () => ({ PromptTemplatePanel: ({ shot, profiles }: { shot?: Record<string, unknown>; profiles: Array<Record<string, unknown>> }) => <div>提示词 {String(shot?.code ?? "未选择")} · {String(profiles[0]?.title ?? "无 Profile")}</div> }));
vi.mock("../features/production/DeliveryWorkflowPanel", () => ({ DeliveryWorkflowPanel: () => <div>交付工作流</div> }));
vi.mock("../features/production/EpisodeContactSheetAction", () => ({ EpisodeContactSheetAction: ({ episodeId }: { episodeId: string }) => <div>联系表 {episodeId}</div> }));
vi.mock("../features/generation/PostProcessPanel", () => ({ PostProcessPanel: ({ videos }: { videos: Array<Record<string, unknown>> }) => <div>增强视频 {videos.map((item) => item.shot_code).join("、")}</div> }));

function renderRoute(path: string, element: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><Routes><Route path="/projects/:projectId/episodes/:episodeId/:page" element={element} /></Routes></MemoryRouter></QueryClientProvider>);
}

describe("P12 creation entry migration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getEpisodeTimelineStatus).mockResolvedValue({ status: { timeline: { latest: null }, renders: { latest: null }, delivery: { latest: null } } } as never);
    vi.mocked(getEpisodePostOverviewV2).mockResolvedValue({ overview: { review: { approved_render_id: null } } } as never);
    vi.mocked(getProjectConfiguration).mockResolvedValue({ configuration: {} } as never);
    vi.mocked(getG8Readiness).mockResolvedValue({ readiness: {} } as never);
    vi.mocked(reviewInbox).mockResolvedValue({ items: [{ media_version_id: "opaque-media", media_asset_id: "asset", project_id: "project-1", episode_id: "episode-1", episode_code: "EP01", shot_code: "S012", media_kind: "VIDEO", stage: "PROXY", decision: null, is_stale: 0 }] });
  });

  it("routes Episode Plan through one Agent workspace and retires duplicate expert tabs", async () => {
    renderRoute("/projects/project-1/episodes/episode-1/plan", <EpisodePlanPage />);
    expect(await screen.findByRole("region", { name: "本集 Agent 制作" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "场景与分组" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "提示词快照" })).toBeNull();
  });

  it("mounts contact-sheet and current-episode post-process tools in Delivery", async () => {
    renderRoute("/projects/project-1/episodes/episode-1/delivery?view=package", <DeliveryPage />);
    expect(await screen.findByText("联系表 episode-1")).toBeTruthy();
    expect(await screen.findByText("增强视频 S012")).toBeTruthy();
    expect(reviewInbox).toHaveBeenCalledWith("project-1", "", { episode_id: "episode-1", media_kind: "VIDEO" });
  });
});
