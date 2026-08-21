import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { AssetBibleItem } from "./api";
import { createStoryAssetState } from "./api";
import { SceneBiblePanel } from "./SceneBiblePanel";

vi.mock("./api", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("./api")>();
  return { ...original, createStoryAssetState: vi.fn().mockResolvedValue({ state: { id: "night-state" } }) };
});

const item: AssetBibleItem = {
  asset: { id: "scene-asset", kind: "SCENE", code: "SC_WAREHOUSE", name: "旧仓库", description: "", status: "ACTIVE", revision: 1, canonical_media_version_id: null },
  states: [{ id: "day-state", code: "DAY", label: "日景", state_kind: "TIME_OF_DAY", description: "", state: {}, references: [] }],
  base_references: [{ id: "wide-ref", project_id: "p1", story_asset_id: "scene-asset", asset_state_id: null, media_version_id: "wide media", reference_kind: "SCENE_WIDE", label: "", priority: 100, is_locked: true, yaw_deg: null, pitch_deg: null, status: "ACTIVE", revision: 1 }],
  active_state_id: null,
  voice: null,
  usage: { episode_ids: ["e1"], episodes: ["E01"], shot_count: 2, shots: [
    { binding_id: "b1", shot_id: "s1", shot_code: "SH010", episode_code: "E01", scene_id: "scene-1", scene_code: "SC01", scene_title: "仓库内", role_in_shot: "location" },
    { binding_id: "b2", shot_id: "s2", shot_code: "SH020", episode_code: "E01", scene_id: "scene-1", scene_code: "SC01", scene_title: "仓库内", role_in_shot: "location" },
  ] },
  readiness: { level: "BASIC", missing: [] },
};

function renderPanel(onChooseReference = vi.fn(), onChanged = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><SceneBiblePanel item={item} onChooseReference={onChooseReference} onChanged={onChanged} /></QueryClientProvider>);
  return { onChooseReference, onChanged };
}

describe("SceneBiblePanel", () => {
  it("shows real scene/shot usage and thumbnail-only reference slots", () => {
    renderPanel();
    expect(screen.getByText("SC01 · 仓库内")).toBeTruthy();
    expect(screen.getByText("SH010")).toBeTruthy();
    expect(screen.getByText("1 使用场景 · 2 镜头")).toBeTruthy();
    const image = screen.getByAltText("大全景 / Wide 缩略图") as HTMLImageElement;
    expect(image.src).toContain("/media-versions/wide%20media/thumbnail?size=small&frame=poster");
    expect(image.src).not.toContain("/content");
  });

  it("creates a missing NIGHT state through the existing 0042 command", async () => {
    const { onChanged } = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "创建 NIGHT" }));
    await waitFor(() => expect(createStoryAssetState).toHaveBeenCalledWith("scene-asset", expect.objectContaining({ code: "NIGHT", state_kind: "TIME_OF_DAY" })));
    expect(onChanged).toHaveBeenCalled();
  });

  it("routes a shortcut slot into the existing reference picker", () => {
    const { onChooseReference } = renderPanel();
    const lightSlot = screen.getByText("光线参考 / Light").closest("article") as HTMLElement;
    fireEvent.click(lightSlot.querySelector("button") as HTMLButtonElement);
    expect(onChooseReference).toHaveBeenCalledWith("LIGHTING_REFERENCE");
  });
});
