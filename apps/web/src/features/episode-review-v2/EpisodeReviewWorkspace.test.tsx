import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  createReviewDecisionV2,
  getEpisodePostOverviewV2,
  listEpisodeReviewTargetsV2,
  revokeReviewDecisionV2,
  type EpisodeReviewTarget,
} from "../../generated/api";
import { EpisodeReviewWorkspace } from "./EpisodeReviewWorkspace";

vi.mock("../../generated/api", () => ({
  createReviewDecisionV2: vi.fn(),
  getEpisodePostOverviewV2: vi.fn(),
  listEpisodeReviewTargetsV2: vi.fn(),
  revokeReviewDecisionV2: vi.fn(),
}));

const mediaTarget: EpisodeReviewTarget = {
  target_kind: "MEDIA_VERSION",
  target_id: "media-1",
  project_id: "project-1",
  episode_id: "episode-1",
  shot_id: "shot-1",
  label: "S001",
  media_kind: "IMAGE",
  stage: "KEYFRAME",
  duration_ms: null,
  subject_revision: 3,
  integrity_status: "VERIFIED",
  machine_status: "PASS",
  template_version_id: "template-image",
  template_code: "image_asset",
  template_items: [{ id: "identity", label: "人物身份", required: true }],
  latest_decision_id: null,
  latest_decision: null,
  latest_decision_revision: null,
  latest_decision_stale: false,
  blocker_codes: [],
  allowed_actions: ["SUBMIT_REVIEW_DECISION"],
  created_at: "2026-08-26T00:00:00+00:00",
};

function mount(path = "/") {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><EpisodeReviewWorkspace projectId="project-1" episodeId="episode-1" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("EpisodeReviewWorkspace v2", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getEpisodePostOverviewV2).mockResolvedValue({
      overview: {
        episode_id: "episode-1", project_id: "project-1", episode_code: "EP01", episode_title: "第一集", next_action: "OPEN_REVIEW",
        review: { state: "ATTENTION", target_count: 1, pending_count: 1, blocked_count: 0, stale_count: 0, approved_render_id: null },
        audio: { state: "EMPTY", dialogue_line_count: 0, adopted_tts_count: 0, binding_count: 0, verified_license_count: 0 },
        edit: { state: "EMPTY", timeline_revision_count: 0, latest_timeline_id: null, latest_timeline_revision_no: null, latest_timeline_status: null, frozen_timeline_id: null, subtitle_revision_count: 0, latest_subtitle_id: null },
        delivery: { state: "EMPTY", render_count: 0, verified_render_count: 0, latest_render_id: null, latest_render_revision: null, latest_render_integrity: null, package_count: 0, latest_package_id: null, latest_package_status: null },
        blockers: [], allowed_actions: ["OPEN_REVIEW"],
      }, read_only: true, request_shape: "episode_post_overview_v2",
    });
    vi.mocked(listEpisodeReviewTargetsV2).mockImplementation(async (_episode, options) => {
      const resolved = options ?? {};
      return {
        items: resolved.targetKinds?.[0] === "EPISODE_RENDER_VERSION" ? [] : [mediaTarget],
        cursor: 0, limit: 100, total: resolved.targetKinds?.[0] === "EPISODE_RENDER_VERSION" ? 0 : 1,
        next_cursor: null, target_kinds: resolved.targetKinds ?? [], include_resolved: Boolean(resolved.includeResolved),
        read_only: true, request_shape: "bounded_episode_review_targets_v2",
      };
    });
    vi.mocked(createReviewDecisionV2).mockResolvedValue({ decision: {
      id: "decision-1", target_kind: "MEDIA_VERSION", target_id: "media-1", decision: "APPROVED",
      subject_revision: 3, revision: 1, is_stale: false, created_at: "now", updated_at: "now", idempotent_replay: false,
    } });
    vi.mocked(revokeReviewDecisionV2).mockResolvedValue({ decision: {
      id: "decision-1", target_kind: "MEDIA_VERSION", target_id: "media-1", decision: "VOIDED",
      subject_revision: 3, revision: 2, is_stale: true, created_at: "now", updated_at: "later", idempotent_replay: false,
    } });
  });

  it("loads a bounded typed queue and switches target kinds", async () => {
    mount();
    expect(await screen.findByRole("button", { name: /S001/ })).toBeTruthy();
    expect(screen.getByText("人物身份 *")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "整集成片" }));
    expect(await screen.findByText("当前筛选下没有待处理目标。")).toBeTruthy();
    expect(listEpisodeReviewTargetsV2).toHaveBeenLastCalledWith("episode-1", {
      targetKinds: ["EPISODE_RENDER_VERSION"], includeResolved: false, limit: 100,
    });
  });

  it("restores an exact typed target deep link", async () => {
    mount("/?targetKind=MEDIA_VERSION&targetId=media-1");
    expect((await screen.findByRole("button", { name: /S001/ })).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("revision 3")).toBeTruthy();
  });

  it("requires the structured checklist before approving and writes through v2", async () => {
    mount();
    const save = await screen.findByRole("button", { name: "保存批准" });
    expect((save as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "人物身份 *" }));
    expect((save as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(createReviewDecisionV2).toHaveBeenCalled());
    expect(vi.mocked(createReviewDecisionV2).mock.calls[0][0]).toMatchObject({
      target_kind: "MEDIA_VERSION", target_id: "media-1", template_version_id: "template-image",
      expected_revision: 3, decision: "APPROVED", checks: [{ item_id: "identity", result: "PASS" }],
    });
    expect(await screen.findByText("审核决定已保存并写入审计记录。")).toBeTruthy();
  });
});
