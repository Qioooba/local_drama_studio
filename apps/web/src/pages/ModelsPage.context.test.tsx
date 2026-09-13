import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listProfiles, putGenerationPreference } from "../generated/api";
import { ModelsPage } from "./ModelsPage";

vi.mock("../generated/api", () => ({
  listProfiles: vi.fn(), putGenerationPreference: vi.fn(), listWorkflowVersions: vi.fn(), getGlobalModelRegistry: vi.fn(),
}));
vi.mock("../features/model-config/ProviderConnectionsPanel", () => ({ ProviderConnectionsPanel: () => null }));
vi.mock("../features/profiles/LocalLLMConfigurationPanel", () => ({ LocalLLMConfigurationPanel: () => null }));
vi.mock("../features/profiles/ProfileConfigurationPanel", () => ({ ProfileConfigurationPanel: () => null }));
vi.mock("../features/status/ReadinessPanels", () => ({ ModelCompatibilityPanel: () => null }));
vi.mock("../features/model-platform-v2/ModelPlatformCenter", () => ({ ModelPlatformCenter: () => null }));

describe("ModelsPage episode context", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listProfiles).mockResolvedValue({ items: [
      { id: "profile", code: "video", title: "本机视频", version_id: "video-v2", version_no: 2, capability: "VIDEO_I2V", status: "PUBLISHED" },
      { id: "profile-text", code: "text", title: "本机文本", version_id: "text-v1", version_no: 1, capability: "LLM_EPISODE_PLAN", status: "PUBLISHED" },
    ] } as never);
    vi.mocked(putGenerationPreference).mockResolvedValue({ preference: {} });
  });

  it("filters published capability choices, applies an episode override and preserves return context", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/system/capabilities?view=catalog&project=p1&episode=e1&capability=VIDEO_I2V&returnTo=%2Fprojects%2Fp1%2Fepisodes%2Fe1%2Fstudio"]}><Routes><Route path="/system/capabilities" element={<ModelsPage />} /></Routes></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByText("本机视频")).toBeTruthy();
    expect(screen.queryByText("本机文本")).toBeNull();
    expect(screen.getByRole("link", { name: "返回当前分集" }).getAttribute("href")).toBe("/projects/p1/episodes/e1/studio");
    fireEvent.click(screen.getByRole("button", { name: "用于当前集" }));
    await waitFor(() => expect(putGenerationPreference).toHaveBeenCalledWith("p1", expect.objectContaining({ owner_type: "EPISODE", owner_id: "e1", capability: "VIDEO_I2V", execution_profile_version_id: "video-v2" })));
    expect(await screen.findByText(/只影响后续新任务/)).toBeTruthy();
  });
});
