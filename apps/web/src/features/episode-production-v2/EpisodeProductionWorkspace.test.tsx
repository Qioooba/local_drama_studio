import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EpisodeProductionWorkspace } from "./EpisodeProductionWorkspace";

const api = vi.hoisted(() => ({
  overview: vi.fn(), shots: vi.fn(), start: vi.fn(), transition: vi.fn(),
}));
vi.mock("../../generated/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../generated/api")>();
  return {
    ...actual,
    getEpisodeProductionOverviewV2: api.overview,
    listEpisodeProductionShotsV2: api.shots,
    startEpisodeProductionRunV2: api.start,
    transitionEpisodeProductionRunV2: api.transition,
  };
});
vi.mock("../events/useProjectEventInvalidation", () => ({ useProjectEventInvalidation: () => undefined }));

const shot = {
  shot_id: "shot-1", shot_code: "S001", order_key: "1", overall_state: "BLOCKED" as const,
  next_action: "OPEN_SHOT_STUDIO",
  stages: [
    { stage_code: "SHOT_PLANNING" as const, state: "BLOCKED" as const, reason_code: "SHOT_INTENT_INCOMPLETE", active_job_id: null, allowed_actions: ["OPEN_SHOT_STUDIO"] },
    { stage_code: "SHOT_IMAGE" as const, state: "EMPTY" as const, reason_code: "NO_CANDIDATES", active_job_id: null, allowed_actions: ["OPEN_SHOT_STUDIO"] },
    { stage_code: "VIDEO" as const, state: "EMPTY" as const, reason_code: "NO_CANDIDATES", active_job_id: null, allowed_actions: ["OPEN_SHOT_STUDIO"] },
    { stage_code: "AUDIO_SUBTITLE" as const, state: "EMPTY" as const, reason_code: "NO_DIALOGUE_LINES", active_job_id: null, allowed_actions: ["OPEN_SHOT_STUDIO"] },
    { stage_code: "COMPOSE_QC" as const, state: "EMPTY" as const, reason_code: "WORKING_VIDEO_REQUIRED", active_job_id: null, allowed_actions: ["OPEN_SHOT_STUDIO"] },
  ],
  material_slots: [
    { kind: "KEYFRAME" as const, candidate_count: 0, selected_version_id: null, machine_qc_state: null, human_decision_id: null },
    { kind: "VIDEO" as const, candidate_count: 0, selected_version_id: null, machine_qc_state: null, human_decision_id: null },
    { kind: "AUDIO" as const, candidate_count: 0, selected_version_id: null, machine_qc_state: null, human_decision_id: null },
  ],
  freshness_edges: [],
  blockers: [{ code: "SHOT_INTENT_INCOMPLETE", message: "镜头意图尚未达到可生产状态。", owner_route: "SHOT_STUDIO" as const, repair_action: "OPEN_DESIGN" }],
};

function mount(path = "/projects/p1/episodes/e1/production") {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter initialEntries={[path]}><EpisodeProductionWorkspace projectId="p1" episodeId="e1" /></MemoryRouter>
  </QueryClientProvider>);
}

describe("EpisodeProductionWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.overview.mockResolvedValue({ overview: { episode_id: "e1", project_id: "p1", episode_code: "EP01", episode_title: "第一集", shot_count: 1, attention_count: 1, active_job_count: 0, next_action: "RESOLVE_ATTENTION", state_counts: { BLOCKED: 1 }, active_run: null, allowed_actions: ["START_PRODUCTION_RUN"] }, read_only: true, request_shape: "episode_production_overview_v2" });
    api.shots.mockResolvedValue({ items: [shot], cursor: 0, limit: 50, total: 1, next_cursor: null, filters: ["BLOCKED"], read_only: true, request_shape: "bounded_episode_production_shots_v2" });
    api.start.mockResolvedValue({ run: { id: "run-1", episode_id: "e1", project_id: "p1", status: "RUNNING", revision: 1, updated_at: null, outcome: "STARTED", affected_job_count: 0, idempotent_replay: false } });
    api.transition.mockResolvedValue({ run: { id: "run-1", episode_id: "e1", project_id: "p1", status: "PAUSED_HITL", revision: 4, updated_at: null, outcome: "PAUSED", affected_job_count: 0, idempotent_replay: false } });
  });

  it("uses one canonical projection for attention and all-shot views", async () => {
    mount();
    expect(await screen.findByText("EP01 · 本集生产")).toBeTruthy();
    expect(screen.getByRole("region", { name: "待处理镜头" })).toBeTruthy();
    expect(screen.getByText("S001")).toBeTruthy();
    expect(screen.getByText("先处理异常镜头")).toBeTruthy();
    expect(screen.getByText("镜头策划未完成")).toBeTruthy();
    expect(screen.queryByText("RESOLVE_ATTENTION")).toBeNull();
    expect(api.shots).toHaveBeenCalledWith("e1", expect.objectContaining({ states: ["BLOCKED", "FAILED", "NEEDS_REVIEW", "STALE"] }));
    fireEvent.click(screen.getByRole("tab", { name: "全部镜头" }));
    await waitFor(() => expect(api.shots).toHaveBeenLastCalledWith("e1", expect.objectContaining({ states: undefined })));
  });

  it("submits the typed v2 start command", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "检查并开始" }));
    await waitFor(() => expect(api.start).toHaveBeenCalledWith("e1", expect.objectContaining({ production_mode: "BALANCED", tts_enabled: true, checkpoint_policy: "ON_EXCEPTION", idempotency_key: expect.any(String) })));
  });

  it("sends active run revision through the pause command", async () => {
    api.overview.mockResolvedValue({ overview: { episode_id: "e1", project_id: "p1", episode_code: "EP01", episode_title: "第一集", shot_count: 1, attention_count: 1, active_job_count: 1, next_action: "MONITOR_ACTIVE_JOBS", state_counts: { RUNNING: 1 }, active_run: { id: "run-1", status: "RUNNING", revision: 3, updated_at: null }, allowed_actions: ["PAUSE_RUN"] }, read_only: true, request_shape: "episode_production_overview_v2" });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "暂停" }));
    await waitFor(() => expect(api.transition).toHaveBeenCalledWith("run-1", "pause", expect.objectContaining({ expected_revision: 3, reason: "CREATOR_PAUSE", idempotency_key: expect.any(String) })));
  });
});
