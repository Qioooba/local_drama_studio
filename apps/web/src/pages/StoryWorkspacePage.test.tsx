import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { StoryWorkspacePage } from "./StoryWorkspacePage";

const getProjectMock = vi.hoisted(() => vi.fn());

vi.mock("../features/projects/projectClient", () => ({
  getProject: getProjectMock,
}));

vi.mock("../features/projects/CreativeLibrary", () => ({
  CreativeLibrary: ({ projectId }: { projectId: string }) => <div>creative:{projectId}</div>,
}));
vi.mock("../features/projects/ScriptImportPanel", () => ({
  ScriptImportPanel: ({ projectId, onDraftReady }: { projectId: string; onDraftReady?: () => void }) => <div>import:{projectId}<button type="button" onClick={onDraftReady}>模拟草稿完成</button></div>,
}));
vi.mock("../features/projects/AIDraftReviewPanel", () => ({
  AIDraftReviewPanel: ({ projectId }: { projectId: string }) => <div>review:{projectId}</div>,
}));
vi.mock("../features/episode-plan-v2/AssetProposalReviewPanel", () => ({
  AssetProposalReviewPanel: ({ projectId }: { projectId: string }) => <div>assets:{projectId}</div>,
}));

describe("StoryWorkspacePage", () => {
  beforeEach(() => {
    getProjectMock.mockResolvedValue({
      project: {
        id: "project-1",
        code: "project-1",
        title: "测试项目",
        status: "ACTIVE",
        revision: 1,
        absolute_root_path: "F:\\DramaProjects\\project-1",
      },
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  function renderWorkspace(path = "/projects/project-1/story") {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/projects/:projectId/story" element={<StoryWorkspacePage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("renders one task stage at a time and preserves hash-addressable navigation", () => {
    renderWorkspace();

    expect(screen.getByRole("navigation", { name: "故事工作流" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "从原稿到可生产的故事事实" })).toBeTruthy();
    expect(screen.getByText("import:project-1")).toBeTruthy();
    expect(screen.queryByText("creative:project-1")).toBeNull();
    expect(screen.queryByText("review:project-1")).toBeNull();
    expect(screen.queryByText("assets:project-1")).toBeNull();

    const rail = screen.getByRole("navigation", { name: "故事工作流" });
    fireEvent.click(within(rail).getByRole("button", { name: /审核拆解/ }));
    expect(screen.getByText("review:project-1")).toBeTruthy();
    expect(screen.queryByText("import:project-1")).toBeNull();
    expect(screen.getByRole("link", { name: "返回项目总览" }).getAttribute("href")).toBe("/projects/project-1");
  });

  it("does not render workspace mutations without a project scope", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter><StoryWorkspacePage /></MemoryRouter></QueryClientProvider>);
    expect(screen.getByRole("alert").textContent).toContain("缺少项目上下文");
    expect(screen.queryByText(/^creative:/)).toBeNull();
  });

  it("opens the review stage when the import monitor reports a newly completed draft", () => {
    renderWorkspace("/projects/project-1/story#story-import");
    expect(screen.getByText("import:project-1")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "模拟草稿完成" }));
    expect(screen.getByText("review:project-1")).toBeTruthy();
    expect(within(screen.getByRole("navigation", { name: "故事工作流" })).getByRole("button", { name: /审核拆解/ }).getAttribute("aria-current")).toBe("true");
  });

  it("shows the server absolute project path and copies it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    renderWorkspace();

    expect(await screen.findByText("F:\\DramaProjects\\project-1")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "复制路径" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("F:\\DramaProjects\\project-1"));
    expect(screen.getByRole("button", { name: "已复制" })).toBeTruthy();
  });
});
