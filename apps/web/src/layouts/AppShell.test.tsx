import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, Link, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";
import { notifyDraftDirty } from "../features/drafts/draftGuard";

vi.mock("../generated/api", () => ({
  listProjects: vi.fn().mockResolvedValue({ items: [{ id: "project-1", title: "测试项目" }] }),
  getProjectEpisodeCatalog: vi.fn().mockResolvedValue({ catalog: { seasons: [] } }),
}));

describe("AppShell collapsible sidebar", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    window.localStorage.clear();
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  const renderShell = (initialPath = "/projects/project-1") => {
    const router = createMemoryRouter(
      [{
        path: "/projects/:projectId",
        element: <AppShell />,
        children: [
          { index: true, element: <div data-testid="workspace-child">工作区内容</div> },
          { path: "episodes/:episodeId/studio", element: <div data-testid="studio-board">镜头总览</div> },
          { path: "episodes/:episodeId/studio/:shotId", element: <div data-testid="studio-shot">单镜工作台</div> },
        ],
      }],
      { initialEntries: [initialPath] },
    );
    return render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
  };

  const renderDraftShell = () => {
    const router = createMemoryRouter([{
      path: "/projects/:projectId",
      element: <AppShell />,
      children: [
        { index: true, element: <><button onClick={() => notifyDraftDirty(true, { ownerId: "a", entityKey: "草稿 A", registrationToken: "a1", version: 1, save: async () => ({ status: "saved", savedVersion: 1 }), discard: () => true })}>注册 A</button><button onClick={() => notifyDraftDirty(true, { ownerId: "b", entityKey: "草稿 B", registrationToken: "b1", version: 1, discard: () => true })}>注册 B</button><Link to="next">下一页</Link></> },
        { path: "next", element: <div>下一页内容</div> },
      ],
    }], { initialEntries: ["/projects/project-1"] });
    return render(<QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider>);
  };

  it("renders with sidebar expanded by default with topbar toggle button", async () => {
    renderShell();
    const nav = document.getElementById("studio-sidebar-nav")!;
    expect(nav).toBeInTheDocument();
    expect(nav).not.toHaveClass("collapsed");
    const toggleBtn = screen.getByRole("button", { name: "收起侧边栏" });
    expect(toggleBtn).toBeInTheDocument();
    expect(toggleBtn).toHaveAttribute("aria-expanded", "true");
  });

  it("toggles sidebar when clicking the unified topbar toggle button", async () => {
    renderShell();
    const nav = document.getElementById("studio-sidebar-nav")!;
    const toggleBtn = screen.getByRole("button", { name: "收起侧边栏" });

    // Collapse sidebar
    fireEvent.click(toggleBtn);
    expect(nav).toHaveClass("collapsed");
    expect(nav).toHaveAttribute("aria-hidden", "true");
    expect(window.localStorage.getItem("local-drama:sidebar-collapsed:v1")).toBe("true");

    // Only the unified topbar toggle exists - no redundant floating tabs
    const expandButtons = screen.getAllByRole("button", { name: "展开侧边栏" });
    expect(expandButtons.length).toBe(1);
    expect(expandButtons[0]).toHaveAttribute("aria-expanded", "false");
    expect(expandButtons[0]).toHaveAttribute("title", "展开侧边栏 (Ctrl+B)");

    // Expand sidebar again
    fireEvent.click(expandButtons[0]);
    expect(nav).not.toHaveClass("collapsed");
    expect(window.localStorage.getItem("local-drama:sidebar-collapsed:v1")).toBe("false");
    expect(screen.getByRole("button", { name: "收起侧边栏" })).toHaveAttribute("aria-expanded", "true");
  });

  it("toggles sidebar using Ctrl+B shortcut", async () => {
    renderShell();
    const nav = document.getElementById("studio-sidebar-nav")!;
    expect(nav).not.toHaveClass("collapsed");

    // Press Ctrl+B to collapse
    fireEvent.keyDown(window, { key: "b", ctrlKey: true });
    expect(nav).toHaveClass("collapsed");

    // Press Ctrl+B to expand
    fireEvent.keyDown(window, { key: "b", ctrlKey: true });
    expect(nav).not.toHaveClass("collapsed");
  });

  it("restores collapsed state from localStorage", async () => {
    window.localStorage.setItem("local-drama:sidebar-collapsed:v1", "true");
    renderShell();
    const nav = document.getElementById("studio-sidebar-nav")!;
    expect(nav).toHaveClass("collapsed");
  });

  it("does not apply studio-desk-mode on shotStudio overview route so it can scroll vertically", () => {
    renderShell("/projects/project-1/episodes/episode-1/studio");
    const workspace = document.getElementById("v2-workspace-content")!;
    expect(workspace).toBeInTheDocument();
    expect(workspace).not.toHaveClass("studio-desk-mode");
  });

  it("applies studio-desk-mode on shotStudioShot single shot desk route", () => {
    renderShell("/projects/project-1/episodes/episode-1/studio/shot-1");
    const workspace = document.getElementById("v2-workspace-content")!;
    expect(workspace).toBeInTheDocument();
    expect(workspace).toHaveClass("studio-desk-mode");
  });

  it("aggregates multiple draft owners and does not let an old cleanup clear a newer registration", async () => {
    renderDraftShell();
    fireEvent.click(screen.getByRole("button", { name: "注册 A" }));
    notifyDraftDirty(true, { ownerId: "a", entityKey: "草稿 A", registrationToken: "a2", version: 2, save: async () => ({ status: "saved", savedVersion: 2 }), discard: () => true });
    notifyDraftDirty(false, { ownerId: "a", entityKey: "草稿 A", registrationToken: "a1", version: 1 });
    fireEvent.click(screen.getByRole("link", { name: "下一页" }));
    expect(await screen.findByRole("dialog", { name: "当前页面有未保存内容" })).toBeInTheDocument();
  });

  it("keeps save-and-switch unavailable when any dirty owner has no formal save", async () => {
    renderDraftShell();
    fireEvent.click(screen.getByRole("button", { name: "注册 A" }));
    fireEvent.click(screen.getByRole("button", { name: "注册 B" }));
    fireEvent.click(screen.getByRole("link", { name: "下一页" }));
    const dialog = await screen.findByRole("dialog", { name: "当前页面有未保存内容" });
    expect(within(dialog).getByRole("button", { name: "保存并切换" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "放弃并切换" })).toBeEnabled();
  });
});
