import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listProjects } from "../generated/api";
import { ProjectOperationsPage } from "./ProjectOperationsPage";

vi.mock("../generated/api", () => ({ listProjects: vi.fn() }));

describe("ProjectOperationsPage migration index", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listProjects).mockResolvedValue({
      items: [{ id: "project-1", code: "P1", title: "Project One", status: "ACTIVE" }],
    } as never);
  });

  it("routes every operational fact to one owner without mounting legacy panels", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/project-1/operations"]}>
          <Routes>
            <Route path="/projects/:projectId/operations" element={<ProjectOperationsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("heading", { name: "项目运维入口" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /生产设置/ }).getAttribute("href")).toBe(
      "/projects/project-1/production-settings",
    );
    expect(screen.getByRole("link", { name: /模型与能力/ }).getAttribute("href")).toBe(
      "/projects/project-1/models",
    );
    expect(screen.getByRole("link", { name: /任务队列/ }).getAttribute("href")).toBe(
      "/projects/project-1/jobs",
    );
    expect(screen.getByRole("link", { name: /诊断与审计/ }).getAttribute("href")).toBe(
      "/projects/project-1/diagnostics",
    );
    expect(screen.queryByText("AutomationWorkflowPanel")).toBeNull();
    expect(screen.getAllByText("打开唯一 owner")).toHaveLength(4);
  });
});
