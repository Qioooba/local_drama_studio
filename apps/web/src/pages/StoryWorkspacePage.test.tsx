import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { StoryWorkspacePage } from "./StoryWorkspacePage";

vi.mock("../features/pipeline/OneClickPipelineWorkbench", () => ({
  OneClickPipelineWorkbench: ({ projectId }: { projectId: string }) => <div>pipeline:{projectId}</div>,
}));

describe("StoryWorkspacePage", () => {
  afterEach(() => cleanup());

  it("uses one AI production entry instead of exposing internal review stages", () => {
    render(
      <MemoryRouter initialEntries={["/projects/project-1/story"]}>
        <Routes>
          <Route path="/projects/:projectId/story" element={<StoryWorkspacePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "上传原稿，AI 自动完成全剧规划" })).toBeTruthy();
    expect(screen.getByText("pipeline:project-1")).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "故事工作流" })).toBeNull();
    expect(screen.queryByText("审核拆解")).toBeNull();
    expect(screen.queryByText("资产建档")).toBeNull();
    expect(screen.queryByText("故事圣经")).toBeNull();
    expect(screen.getByRole("link", { name: "返回项目" }).getAttribute("href")).toBe("/projects/project-1");
  });

  it("does not render the pipeline without a project scope", () => {
    render(<MemoryRouter><StoryWorkspacePage /></MemoryRouter>);
    expect(screen.getByRole("alert").textContent).toContain("缺少项目上下文");
    expect(screen.queryByText(/^pipeline:/)).toBeNull();
  });
});
