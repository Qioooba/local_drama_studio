import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { GenerationPreferencePanel } from "./GenerationPreferencePanel";
import { putGenerationPreference } from "./api";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    listPreferenceProjects: vi.fn(async () => [{ id: "project-1", code: "P1", title: "项目一" }]),
    listPreferenceProfiles: vi.fn(async () => []),
    listPreferenceSeasons: vi.fn(async () => []),
    listPreferenceEpisodes: vi.fn(async () => []),
    listPreferenceShots: vi.fn(async () => []),
    listGenerationPreferences: vi.fn(async () => []),
    resolveGenerationPreference: vi.fn(async () => ({
      capability: "VIDEO_I2V", profile_version_id: null, source: "AUTO", native_support: false,
      fallback_support: false, warnings: [], estimated_resources: {}, blocked_reason: "NO_COMPATIBLE_PROFILE",
      preference: null, profile: null, recommendation: null,
    })),
    putGenerationPreference: vi.fn(),
  };
});

describe("GenerationPreferencePanel", () => {
  it("generates audit metadata in the background instead of blocking the creator on a reason field", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <GenerationPreferencePanel initialProjectId="project-1" />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    const save = await screen.findByRole("button", { name: "保存新版本" });
    expect((save as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByRole("textbox", { name: /变更原因/ })).toBeNull();
    fireEvent.click(save);
    await waitFor(() => expect(putGenerationPreference).toHaveBeenCalledWith("project-1", expect.objectContaining({
      reason: "项目默认：图片生成视频使用自动推荐",
    })));
  });
});
