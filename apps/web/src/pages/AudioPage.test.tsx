import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listDialogueLines, listEpisodeAudioBindings, listProfiles, listVoiceProfileVersions, reviewInbox } from "../generated/api";
import { AudioPage } from "./AudioPage";

vi.mock("../generated/api", () => ({
  listDialogueLines: vi.fn(),
  listVoiceProfileVersions: vi.fn(),
  listProfiles: vi.fn(),
  listEpisodeAudioBindings: vi.fn(),
  reviewInbox: vi.fn(),
}));

vi.mock("../features/audio-v2/AudioEpisodeOverview", () => ({
  AudioEpisodeOverview: () => <div>AudioEpisodeOverview</div>,
}));
vi.mock("../features/status/DialogueTTSPanel", () => ({
  DialogueTTSPanel: () => <div>DialogueTTSPanel</div>,
}));
vi.mock("../features/status/AudioTrackPanel", () => ({
  AudioTrackPanel: () => <div>AudioTrackPanel</div>,
}));

describe("AudioPage (009D)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listDialogueLines).mockResolvedValue({ items: [] } as never);
    vi.mocked(listVoiceProfileVersions).mockResolvedValue({ items: [] } as never);
    vi.mocked(listProfiles).mockResolvedValue({ items: [] } as never);
    vi.mocked(listEpisodeAudioBindings).mockResolvedValue({ items: [] } as never);
    vi.mocked(reviewInbox).mockResolvedValue({ items: [] } as never);
  });

  it("mounts only the selected audio task and switches owners without stacking", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/audio"]}>
          <Routes>
            <Route path="/projects/:projectId/episodes/:episodeId/audio" element={<AudioPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(screen.getByRole("heading", { name: "台词、角色声音与音轨编排" })).toBeTruthy();
    expect(screen.getByText("DialogueTTSPanel")).toBeTruthy();
    expect(screen.queryByText("AudioEpisodeOverview")).toBeNull();
    expect(screen.queryByText("AudioTrackPanel")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "音效与配乐" }));
    expect(screen.getByText("AudioTrackPanel")).toBeTruthy();
    expect(screen.queryByText("DialogueTTSPanel")).toBeNull();
    expect(screen.queryByText("AudioEpisodeOverview")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "缺口与证据" }));
    expect(screen.getByText("AudioEpisodeOverview")).toBeTruthy();
    expect(screen.queryByText("AudioTrackPanel")).toBeNull();
    expect(screen.getByRole("link", { name: "进入时间线" }).getAttribute("href")).toBe("/projects/project-1/episodes/ep-1/timeline");
  });

  it("restores the selected task from the URL", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/audio?view=tracks"]}>
          <Routes><Route path="/projects/:projectId/episodes/:episodeId/audio" element={<AudioPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(screen.getByRole("tab", { name: "音效与配乐" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("AudioTrackPanel")).toBeTruthy();
    expect(screen.queryByText("DialogueTTSPanel")).toBeNull();
  });
});
