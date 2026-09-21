import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createVideoUpscalePreset, getVideoUpscaleOptions, updateProjectVideoUpscaleSettings } from "../../generated/api";
import { VideoUpscaleDefaultsPanel } from "./VideoUpscaleDefaultsPanel";

vi.mock("../../generated/api", () => ({
  createVideoUpscalePreset: vi.fn(),
  getVideoUpscaleOptions: vi.fn(),
  updateProjectVideoUpscaleSettings: vi.fn(),
}));

const response = {
  project_id: "project-1",
  settings: { project_id: "project-1", preset_id: "preset", preset_title: "漫剧标准", preset_version_id: "preset-v1", overrides: { pipeline: {}, model: {} }, revision: 0, inherited: true },
  presets: [{ id: "preset", code: "ANIME", title: "漫剧标准", builtin: true, current_version_id: "preset-v1", version_id: "preset-v1", version_no: 1, profile_version_id: "profile-v1", pipeline_options: { source_policy: "PREFER_FINAL_DELIVERY", chunk_frames: 240, crf: 18 }, model_options: { tile_size: 0 }, available: true, unavailable_reason: null }],
  profiles: [], pipeline_contract: {}, model_contract: {}, runtime_contacted: false as const, network_contacted: false as const, mutated: false as const,
};

describe("VideoUpscaleDefaultsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getVideoUpscaleOptions).mockResolvedValue(response);
    vi.mocked(updateProjectVideoUpscaleSettings).mockResolvedValue({ settings: { ...response.settings, revision: 1, inherited: false } });
    vi.mocked(createVideoUpscalePreset).mockResolvedValue({ preset: response.presets[0] });
  });

  it("saves a versioned project default without editing the builtin preset", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter><VideoUpscaleDefaultsPanel projectId="project-1" /></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByRole("heading", { name: "AI 视频超分" })).toBeTruthy();
    fireEvent.change(await screen.findByLabelText("来源优先级"), { target: { value: "APPROVED_COMPOSE" } });
    fireEvent.change(screen.getByLabelText("Tile"), { target: { value: "256" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为项目默认" }));
    await waitFor(() => expect(updateProjectVideoUpscaleSettings).toHaveBeenCalledWith("project-1", expect.objectContaining({
      preset_version_id: "preset-v1",
      pipeline_overrides: expect.objectContaining({ source_policy: "APPROVED_COMPOSE" }),
      model_overrides: { tile_size: 256 },
      expected_revision: 0,
    })));
  });
});
