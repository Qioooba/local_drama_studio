import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { Job } from "../../generated/api";
import { reconcileJobs } from "../../generated/api";
import { JobsPanel } from "./JobsPanel";

vi.mock("../../generated/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../generated/api")>();
  return { ...original, reconcileJobs: vi.fn() };
});

function jobs(revision = 1): Job[] {
  return Array.from({ length: 14 }, (_, index) => ({
    id: `job-${index + 1}`,
    type: "TTS_GENERATION",
    project_id: "project-1",
    state: "SUCCEEDED",
    channel: "CPU",
    priority: 100,
    max_attempts: 1,
    revision,
  }));
}

describe("JobsPanel", () => {
  it("keeps the expanded row count across live fact refreshes and resets only when scope changes", () => {
    const view = render(<MemoryRouter><JobsPanel jobs={jobs()} loading={false} scopeKey="project-1" /></MemoryRouter>);
    expect(screen.getAllByRole("button", { name: "详情 · 产物" })).toHaveLength(12);
    fireEvent.click(screen.getByRole("button", { name: "继续显示任务（12/14）" }));
    expect(screen.getAllByRole("button", { name: "详情 · 产物" })).toHaveLength(14);

    view.rerender(<MemoryRouter><JobsPanel jobs={jobs(2)} loading={false} scopeKey="project-1" /></MemoryRouter>);
    expect(screen.getAllByRole("button", { name: "详情 · 产物" })).toHaveLength(14);

    view.rerender(<MemoryRouter><JobsPanel jobs={jobs(2)} loading={false} scopeKey="project-2" /></MemoryRouter>);
    expect(screen.getAllByRole("button", { name: "详情 · 产物" })).toHaveLength(12);
  });

  it("reports a completed lease scan even when nothing needed recovery", async () => {
    vi.mocked(reconcileJobs).mockResolvedValue({ result: { reconciled: 0, items: [] } });
    render(<MemoryRouter><JobsPanel jobs={[]} loading={false} scopeKey="project-1" /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "扫描过期租约" }));
    expect((await screen.findByRole("status")).textContent).toContain("没有需要接管的 Attempt");
  });

  it("shows an executable recovery command only when queued work has no worker", () => {
    const capacity = { queued_count: 3, active_worker_count: 0 } as never;
    render(<MemoryRouter><JobsPanel jobs={jobs()} loading={false} scopeKey="project-1" capacity={capacity} /></MemoryRouter>);
    expect(screen.getByRole("status").textContent).toContain("3 个任务排队");
    expect(screen.getByText(".\\scripts\\start-worker.ps1")).toBeTruthy();
    expect(screen.getByRole("button", { name: "复制 Worker 启动命令" })).toBeTruthy();
  });

  it("shows persisted phase and numeric progress inline without inventing a percentage", () => {
    const active = jobs().slice(0, 2);
    active[0] = { ...active[0], state: "RUNNING", progress: { phase: "ENCODING", percent: 58 } };
    active[1] = { ...active[1], state: "RUNNING", progress: { phase: "VERIFYING_SOURCE" } };
    render(<MemoryRouter><JobsPanel jobs={active} loading={false} scopeKey="project-1" /></MemoryRouter>);

    expect(screen.getByRole("progressbar", { name: "TTS_GENERATION进度 58%" })).toBeTruthy();
    expect(screen.getByText(/ENCODING · 58%/)).toBeTruthy();
    expect(screen.getByText(/VERIFYING_SOURCE · 优先级/)).toBeTruthy();
  });
});
