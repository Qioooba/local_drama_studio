import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { StoryWorkspacePage } from "./StoryWorkspacePage";

vi.mock("../features/projects/CreativeLibrary", () => ({
  CreativeLibrary: ({ projectId }: { projectId: string }) => <div>creative:{projectId}</div>,
}));
vi.mock("../features/projects/ScriptImportPanel", () => ({
  ScriptImportPanel: ({ projectId }: { projectId: string }) => <div>import:{projectId}</div>,
}));
vi.mock("../features/projects/AIDraftReviewPanel", () => ({
  AIDraftReviewPanel: ({ projectId }: { projectId: string }) => <div>review:{projectId}</div>,
}));
vi.mock("../features/episode-plan-v2/AssetProposalReviewPanel", () => ({
  AssetProposalReviewPanel: ({ projectId }: { projectId: string }) => <div>assets:{projectId}</div>,
}));

describe("StoryWorkspacePage", () => {
  afterEach(() => cleanup());

  it("renders one task stage at a time and preserves hash-addressable navigation", () => {
    render(
      <MemoryRouter initialEntries={["/projects/project-1/story"]}>
        <Routes>
          <Route path="/projects/:projectId/story" element={<StoryWorkspacePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("navigation", { name: "故事工作流阶段" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "从长文原稿到可审核的生产事实" })).toBeTruthy();
    expect(screen.getByText("creative:project-1")).toBeTruthy();
    expect(screen.queryByText("import:project-1")).toBeNull();
    expect(screen.queryByText("review:project-1")).toBeNull();
    expect(screen.queryByText("assets:project-1")).toBeNull();

    const rail = screen.getByRole("navigation", { name: "工作流步骤" });
    fireEvent.click(within(rail).getByRole("button", { name: /导入长文/ }));
    expect(screen.getByText("import:project-1")).toBeTruthy();
    expect(screen.queryByText("creative:project-1")).toBeNull();

    fireEvent.click(within(rail).getByRole("button", { name: /审核拆解/ }));
    expect(screen.getByText("review:project-1")).toBeTruthy();
    expect(screen.queryByText("import:project-1")).toBeNull();
    expect(screen.getByRole("link", { name: "返回项目总览" }).getAttribute("href")).toBe("/projects/project-1");
  });

  it("does not render workspace mutations without a project scope", () => {
    render(<MemoryRouter><StoryWorkspacePage /></MemoryRouter>);
    expect(screen.getByRole("alert").textContent).toContain("缺少项目上下文");
    expect(screen.queryByText(/^creative:/)).toBeNull();
  });
});
