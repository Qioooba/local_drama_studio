import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Job } from "../../generated/api";
import { JobsPanel } from "./JobsPanel";

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
    expect(screen.getAllByRole("button", { name: "查看详情和产物" })).toHaveLength(12);
    fireEvent.click(screen.getByRole("button", { name: "继续显示任务（12/14）" }));
    expect(screen.getAllByRole("button", { name: "查看详情和产物" })).toHaveLength(14);

    view.rerender(<MemoryRouter><JobsPanel jobs={jobs(2)} loading={false} scopeKey="project-1" /></MemoryRouter>);
    expect(screen.getAllByRole("button", { name: "查看详情和产物" })).toHaveLength(14);

    view.rerender(<MemoryRouter><JobsPanel jobs={jobs(2)} loading={false} scopeKey="project-2" /></MemoryRouter>);
    expect(screen.getAllByRole("button", { name: "查看详情和产物" })).toHaveLength(12);
  });

  it("shows an executable recovery command only when queued work has no worker", () => {
    const capacity = { queued_count: 3, active_worker_count: 0 } as never;
    render(<MemoryRouter><JobsPanel jobs={jobs()} loading={false} scopeKey="project-1" capacity={capacity} /></MemoryRouter>);
    expect(screen.getByRole("status").textContent).toContain("3 个任务排队");
    expect(screen.getByText(".\\scripts\\start-worker.ps1")).toBeTruthy();
    expect(screen.getByRole("button", { name: "复制后台服务启动命令" })).toBeTruthy();
  });

  it("shows persisted phase and numeric progress inline without inventing a percentage", () => {
    const active = jobs().slice(0, 2);
    active[0] = { ...active[0], state: "RUNNING", progress: { phase: "ENCODING", percent: 0.58 } };
    active[1] = { ...active[1], state: "RUNNING", progress: { phase: "VERIFYING_SOURCE" } };
    render(<MemoryRouter><JobsPanel jobs={active} loading={false} scopeKey="project-1" /></MemoryRouter>);

    expect(screen.getByRole("progressbar", { name: "生成对白配音进度 58%" })).toBeTruthy();
    expect(screen.getByText(/正在编码媒体 · 58%/)).toBeTruthy();
    expect(screen.getByText(/正在校验输入文件 · 优先级/)).toBeTruthy();
  });

  it("offers deletion only for terminal tasks", () => {
    const items = jobs().slice(0, 2);
    items[1] = { ...items[1], state: "RUNNING" };
    render(<MemoryRouter><JobsPanel jobs={items} loading={false} scopeKey="project-1" /></MemoryRouter>);
    expect(screen.getAllByRole("button", { name: "删除记录" })).toHaveLength(1);
  });
});
