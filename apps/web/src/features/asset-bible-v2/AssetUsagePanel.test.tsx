import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { AssetBibleItem } from "./api";
import { AssetUsagePanel } from "./AssetUsagePanel";

const base: AssetBibleItem = {
  asset: { id: "a1", kind: "CHARACTER", code: "CHAR_A", name: "阿宁", description: "", status: "ACTIVE", revision: 1, canonical_media_version_id: null },
  states: [], base_references: [], active_state_id: null, voice: null,
  usage: { episode_ids: ["episode-3"], episodes: ["EP03"], shot_count: 1, shots: [{ binding_id: "bind-1", episode_id: "episode-3", episode_code: "EP03", shot_id: "shot-12", shot_code: "S012", scene_code: "SC04", scene_title: "屋顶", role_in_shot: "main" }] },
  readiness: { level: "EMPTY", missing: ["HERO"] },
};

describe("AssetUsagePanel", () => {
  it("deep-links real usage facts to the exact Director shot without exposing ids", () => {
    render(<MemoryRouter><AssetUsagePanel item={base} projectId="project-1" /></MemoryRouter>);
    expect(screen.getByText("EP03 · S012")).toBeTruthy();
    expect(screen.getByText("SC04 · 屋顶 · main")).toBeTruthy();
    expect(screen.getByRole("link", { name: "打开 EP03 · S012 镜头工作台" }).getAttribute("href")).toBe("/projects/project-1/episodes/episode-3/studio/shot-12");
    expect(screen.queryByText("episode-3")).toBeNull();
    expect(screen.queryByText("shot-12")).toBeNull();
  });

  it("has an explicit empty state", () => {
    render(<MemoryRouter><AssetUsagePanel item={{ ...base, usage: { episode_ids: [], episodes: [], shot_count: 0, shots: [] } }} projectId="project-1" /></MemoryRouter>);
    expect(screen.getByText("此资产尚未绑定到镜头")).toBeTruthy();
  });
});
