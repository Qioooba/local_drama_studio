import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listProfiles, listProjects } from "../generated/api";
import { ProjectsPage } from "./ProjectsPage";

vi.mock("../features/projects/ProjectCreateWizard", () => ({ ProjectCreateWizard: () => null }));
vi.mock("../features/projects/OneSentenceVideoWizard", () => ({ OneSentenceVideoWizard: () => null }));
vi.mock("../generated/api", () => ({ listProfiles: vi.fn(), listProjects: vi.fn() }));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><ProjectsPage /></MemoryRouter></QueryClientProvider>);
}

describe("ProjectsPage recent projects", () => {
  beforeEach(() => {
    vi.mocked(listProfiles).mockResolvedValue({ items: [] } as never);
    vi.mocked(listProjects).mockResolvedValue({ items: [
      { id: "p2", code: "P2", title: "Second", status: "ACTIVE", revision: 2, updated_at: "2026-08-19T00:00:00Z" },
      { id: "p5", code: "P5", title: "Oldest", status: "ARCHIVED", revision: 1, updated_at: "2026-08-01T00:00:00Z" },
      { id: "p1", code: "P1", title: "Newest", status: "ACTIVE", revision: 5, updated_at: "2026-08-20T00:00:00Z" },
      { id: "p4", code: "P4", title: "Fourth", status: "PAUSED", revision: 3, updated_at: "2026-08-17T00:00:00Z" },
      { id: "p3", code: "P3", title: "Third", status: "DRAFT", revision: 4, updated_at: "2026-08-18T00:00:00Z" },
    ] } as never);
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

    fireEvent.change(screen.getByRole("textbox", { name: "搜索项目" }), { target: { value: "oldest" } });
    await waitFor(() => expect(document.querySelectorAll(".project-card")).toHaveLength(1));
    expect(screen.getByText("Oldest")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "其他项目" })).toBeNull();
  });
});
