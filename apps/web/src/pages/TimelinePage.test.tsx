import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { TimelinePage } from "./TimelinePage";

vi.mock("../features/edit-v2/EpisodeEditWorkspace", () => ({ EpisodeEditWorkspace: ({ projectId, episodeId }: { projectId: string; episodeId: string }) => <section aria-label="编辑聚合">{projectId}/{episodeId}</section> }));

describe("TimelinePage v2", () => {
  it("mounts the single canonical edit workspace and preserves neighboring task routes", () => {
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter initialEntries={["/projects/project-1/episodes/ep-1/post/edit"]}><Routes><Route path="/projects/:projectId/episodes/:episodeId/post/edit" element={<TimelinePage />} /></Routes></MemoryRouter></QueryClientProvider>);
    expect(screen.getByRole("heading", { name: "播放器、多轨编排与冻结" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "编辑聚合" }).textContent).toBe("project-1/ep-1");
    expect(screen.getByRole("link", { name: "返回声音" }).getAttribute("href")).toBe("/projects/project-1/episodes/ep-1/post/audio");
    expect(screen.getByRole("link", { name: "前往交付" }).getAttribute("href")).toBe("/projects/project-1/episodes/ep-1/delivery");
  });
});
