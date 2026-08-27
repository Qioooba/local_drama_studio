import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AudioPage } from "./AudioPage";

vi.mock("../features/audio-v2/EpisodeAudioWorkspace", () => ({
  EpisodeAudioWorkspace: ({ projectId, episodeId }: { projectId: string; episodeId: string }) => <div>Audio v2 {projectId}/{episodeId}</div>,
}));

describe("AudioPage v2", () => {
  it("mounts the single typed workspace and keeps Review/Edit handoffs", () => {
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/post/audio"]}><Routes><Route path="/projects/:projectId/episodes/:episodeId/post/audio" element={<AudioPage />} /></Routes></MemoryRouter></QueryClientProvider>);
    expect(screen.getByRole("heading", { name: "对白引用、音乐、音效与混音" })).toBeTruthy();
    expect(screen.getByText("Audio v2 project-1/ep-1")).toBeTruthy();
    expect(screen.getByRole("link", { name: "声音审核" }).getAttribute("href")).toBe("/projects/project-1/episodes/ep-1/post/review");
    expect(screen.getByRole("link", { name: "进入编辑" }).getAttribute("href")).toBe("/projects/project-1/episodes/ep-1/post/edit");
  });
});
