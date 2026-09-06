import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { EpisodeProductionShot, StoryboardShot } from "../../generated/api";
import { EpisodeShotBoard } from "./EpisodeShotBoard";

const stage = (stageCode: "SHOT_PLANNING" | "COMPOSE_QC", state: "READY" | "STALE") => ({
  stage_code: stageCode,
  state,
  reason_code: state === "READY" ? "SHOT_REVISION_READY" : "TIMELINE_MISSING_CURRENT_VIDEO",
  active_job_id: null,
  allowed_actions: ["OPEN_SHOT_STUDIO"],
});

const shot = (withReadiness = true): EpisodeProductionShot => ({
  shot_id: "shot-001",
  shot_code: "S001",
  order_key: "1",
  shot_readiness: withReadiness
    ? { status: "READY", ready: true, allowed_actions: ["OPEN_SHOT_STUDIO"] }
    : undefined as never,
  overall_state: "STALE",
  next_action: "OPEN_POST_EDIT",
  stages: [stage("SHOT_PLANNING", "READY"), stage("COMPOSE_QC", "STALE")],
  material_slots: [
    { kind: "KEYFRAME", candidate_count: 0, selected_version_id: null, machine_qc_state: null, human_decision_id: null },
    { kind: "VIDEO", candidate_count: 0, selected_version_id: null, machine_qc_state: null, human_decision_id: null },
    { kind: "AUDIO", candidate_count: 0, selected_version_id: null, machine_qc_state: null, human_decision_id: null },
  ],
  freshness_edges: [],
  blockers: [],
});

function renderBoard(value: EpisodeProductionShot) {
  return render(
    <MemoryRouter>
      <EpisodeShotBoard projectId="project-1" episodeId="episode-1" shots={[value]} />
    </MemoryRouter>,
  );
}

describe("EpisodeShotBoard readiness projection", () => {
  it("uses current dialogue after sound editing instead of stale director fields", () => {
    const detail: StoryboardShot = {
      id: "shot-001", code: "S001", order_key: "1", target_duration_ms: 5000,
      shot_type: "OTHER", status: "READY", revision: 1, current_revision_id: "revision-1",
      current_revision_no: 1, is_frozen: 0, display_ordinal: 1, timeline_start_ms: 0, timeline_end_ms: 5000,
      fields: { dialogue: "旧台词" }, current_dialogue: "林晚：谁发出的文件？",
    };
    render(<MemoryRouter><EpisodeShotBoard projectId="project-1" episodeId="episode-1" shots={[shot()]} storyboard={[detail]} /></MemoryRouter>);
    expect(screen.getByText("对白：林晚：谁发出的文件？")).toBeTruthy();
    expect(screen.queryByText("对白：旧台词")).toBeNull();
  });
  it("counts shot readiness separately from a stale aggregate", () => {
    const { container } = renderBoard(shot());

    expect(container.querySelector(".episode-shot-board dl")?.textContent).toContain("可生成1");
    expect(container.querySelector(".episode-shot-board dl")?.textContent).toContain("待处理0");
    const shotLink = screen.getAllByRole("link")[0];
    expect(shotLink.textContent).toContain("可生成");
    expect(shotLink.textContent).toContain("整段：整段需更新");
    expect(shotLink.textContent).toContain("生成候选");
  });

  it("keeps the planning-stage fallback for old responses", () => {
    const { container } = renderBoard(shot(false));

    expect(container.querySelector(".episode-shot-board dl")?.textContent).toContain("可生成1");
    expect(screen.getAllByRole("link")[0].textContent).toContain("生成候选");
  });
});
