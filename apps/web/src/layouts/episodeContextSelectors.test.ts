import { describe, expect, it } from "vitest";
import type { EpisodePostOverview, EpisodeProductionOverview } from "../generated/api";
import { selectEpisodeStageFacts } from "./episodeContextSelectors";

describe("selectEpisodeStageFacts", () => {
  it("keeps route selection separate from authoritative completion and blocking facts", () => {
    const overview = {
      stage_summary: {
        SHOT_PLANNING: { total: 2, completed: 2, running: 0, attention: 0, stale: 0, requires_confirmation: 0 },
        SHOT_IMAGE: { total: 2, completed: 2, running: 0, attention: 0, stale: 0, requires_confirmation: 0 },
        VIDEO: { total: 2, completed: 1, running: 0, attention: 1, stale: 0, requires_confirmation: 0 },
      },
    } as EpisodeProductionOverview;
    const post = {
      edit: { frozen_timeline_id: "timeline-1" }, delivery: { verified_render_count: 1, package_count: 1, latest_package_status: "VERIFIED" }, blockers: [],
    } as unknown as EpisodePostOverview;
    const facts = selectEpisodeStageFacts(overview, post);
    expect(facts.MAKE).toEqual({ state: "complete", label: "2/2 已完成" });
    expect(facts.SHOTS).toEqual({ state: "blocked", label: "1 项待处理" });
    expect(facts.POST.state).toBe("complete");
    expect(facts.DELIVER).toEqual({ state: "blocked", label: "交付待人工批准" });
  });

  it("shows unknown instead of a green state when facts were not checked", () => {
    const facts = selectEpisodeStageFacts();
    expect(Object.values(facts).every((fact) => fact.state === "unknown")).toBe(true);
  });
});
