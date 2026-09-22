import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listProjects } from "../generated/api";
import { ProjectsPage } from "./ProjectsPage";

vi.mock("../features/projects/ProjectCreateWizard", () => ({ ProjectCreateWizard: () => null }));
vi.mock("../generated/api", () => ({ listProjects: vi.fn() }));

const ALL_PROJECTS = [
  { id: "p2", code: "P2", title: "Second", status: "ACTIVE", revision: 2, updated_at: "2026-08-19T00:00:00Z" },
  { id: "p5", code: "P5", title: "Oldest", status: "ARCHIVED", revision: 1, updated_at: "2026-08-01T00:00:00Z" },
  { id: "p1", code: "P1", title: "Newest", status: "ACTIVE", revision: 5, updated_at: "2026-08-20T00:00:00Z" },
  { id: "p4", code: "P4", title: "Fourth", status: "PAUSED", revision: 3, updated_at: "2026-08-17T00:00:00Z" },
  { id: "p3", code: "P3", title: "Third", status: "DRAFT", revision: 4, updated_at: "2026-08-18T00:00:00Z" },
];

/** 101 projects: page 1 carries 50, page 2 carries 50, page 3 carries the 101st. */
function hundredAndOne() {
  return Array.from({ length: 101 }, (_, index) => ({
    id: `p${index + 1}`, code: `PRJ${index + 1}`, title: `第 ${index + 1} 部作品`, status: "ACTIVE", revision: 1,
    updated_at: new Date(Date.UTC(2026, 0, 1) + index * 60_000).toISOString(),
  }));
}

function pageOf(items: ReturnType<typeof hundredAndOne>, cursor: number, limit = 50) {
  const slice = items.slice(cursor, cursor + limit);
  const next = cursor + limit < items.length ? cursor + limit : null;
  return { items: slice, page: { cursor, limit, next_cursor: next, has_more: next !== null } };
}

function mockServer(items: ReturnType<typeof hundredAndOne>) {
  vi.mocked(listProjects).mockImplementation(async (filters = {}) => {
    const term = (filters.search ?? "").trim().toLocaleLowerCase();
    const match = term ? items.filter((project) => `${project.title} ${project.code}`.toLocaleLowerCase().includes(term)) : items;
    return pageOf(match, filters.cursor ?? 0, filters.limit ?? 50);
  });
}

function renderPage(entry = "/projects") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><ProjectsPage /></MemoryRouter></QueryClientProvider>);
}

describe("ProjectsPage recent projects", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listProjects).mockResolvedValue({ items: ALL_PROJECTS } as never);
  });

  it("renders a newest-first recent section, keeps the remainder, and preserves one action per card", async () => {
    renderPage();
    const recent = await screen.findByRole("region", { name: "最近项目" });
    const other = screen.getByRole("region", { name: "其他项目" });
    expect([...recent.querySelectorAll(".project-card h3")].map((node) => node.textContent)).toEqual(["Newest", "Second", "Third", "Fourth"]);
    expect([...other.querySelectorAll(".project-card h3")].map((node) => node.textContent)).toEqual(["Oldest"]);
    for (const card of document.querySelectorAll(".project-card")) {
      expect(within(card as HTMLElement).getAllByRole("link")).toHaveLength(1);
    }
    const draftCard = screen.getByText("Third").closest(".project-card") as HTMLElement;
    expect(within(draftCard).getByText("草稿")).toBeTruthy();
    expect(within(draftCard).getByRole("link", { name: "Third：打开项目概览" })).toBeTruthy();
    expect(within(screen.getByText("Fourth").closest(".project-card") as HTMLElement).getByRole("link", { name: "Fourth：打开项目概览" })).toBeTruthy();
    expect(within(screen.getByText("Oldest").closest(".project-card") as HTMLElement).getByRole("link", { name: "Oldest：打开项目概览" })).toBeTruthy();
  });

  it("searches on the server instead of filtering only the loaded page", async () => {
    const items = hundredAndOne();
    mockServer(items);
    renderPage();
    await screen.findByText("第 1 部作品");

    // Project 101 is outside page 1, so a local filter could never find it.
    fireEvent.change(screen.getByRole("textbox", { name: "搜索项目" }), { target: { value: "第 101 部作品" } });
    fireEvent.click(screen.getByRole("button", { name: "搜索全部项目" }));

    expect(await screen.findByText("第 101 部作品")).toBeTruthy();
    expect(document.querySelectorAll(".project-card")).toHaveLength(1);
    expect(listProjects).toHaveBeenLastCalledWith(expect.objectContaining({ search: "第 101 部作品", cursor: 0 }));
  });

  it("reaches the last of 101 projects, keeps loaded pages, and never duplicates a row", async () => {
    mockServer(hundredAndOne());
    renderPage();
    await screen.findByText("第 1 部作品");
    expect(document.querySelectorAll(".project-card")).toHaveLength(50);
    expect(screen.getByText(/已加载 50 个项目（还有更多）/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(listProjects).toHaveBeenCalledWith(expect.objectContaining({ cursor: 50 })));
    await waitFor(() => expect(document.querySelectorAll(".project-card")).toHaveLength(100));

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(listProjects).toHaveBeenCalledWith(expect.objectContaining({ cursor: 100 })));
    await screen.findByText("第 101 部作品");
    expect(document.querySelectorAll(".project-card")).toHaveLength(101);
    expect(new Set([...document.querySelectorAll(".project-card h3")].map((node) => node.textContent)).size).toBe(101);
    expect(screen.getByText(/已加载 101 个项目（已到末页）/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "下一页" }).hasAttribute("disabled")).toBe(true);
  });

  it("restores the requested page from the URL after a refresh", async () => {
    mockServer(hundredAndOne());
    renderPage("/projects?page=2");
    await waitFor(() => expect(listProjects).toHaveBeenCalledWith(expect.objectContaining({ cursor: 50 })));
    await waitFor(() => expect(document.querySelectorAll(".project-card")).toHaveLength(100));
    expect(screen.getByText(/第 2 页/)).toBeTruthy();
  });

  it("falls back to a valid page marker without dropping already loaded rows when the last page empties", async () => {
    const items = hundredAndOne().slice(0, 100);
    mockServer(items);
    renderPage("/projects?page=2");
    await waitFor(() => expect(listProjects).toHaveBeenCalledWith(expect.objectContaining({ cursor: 50 })));
    await waitFor(() => expect(document.querySelectorAll(".project-card")).toHaveLength(100));
    expect(screen.getByText(/第 2 页/)).toBeTruthy();

    // The 100th project is deleted, so page 2 no longer exists on the server.
    mockServer(items.slice(0, 99));
    fireEvent.click(screen.getByRole("button", { name: "上一页" }));

    await waitFor(() => expect(screen.getByText(/第 1 页/)).toBeTruthy());
    // Already-loaded rows are never dropped by the fallback.
    expect(document.querySelectorAll(".project-card").length).toBeGreaterThanOrEqual(50);
    expect(screen.queryByText(/第 2 页/)).toBeNull();
  });

  it("shows a clear-filter empty state that does not pretend the library is empty", async () => {
    mockServer(hundredAndOne());
    renderPage();
    await screen.findByText("第 1 部作品");
    fireEvent.change(screen.getByRole("textbox", { name: "搜索项目" }), { target: { value: "不存在的项目" } });
    fireEvent.click(screen.getByRole("button", { name: "搜索全部项目" }));

    expect(await screen.findByText("没有符合条件的项目")).toBeTruthy();
    expect(screen.getByRole("button", { name: "清除筛选" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    expect(await screen.findByText("第 1 部作品")).toBeTruthy();
  });
});
