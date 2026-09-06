import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import * as api from "../../generated/api";
import type { Job } from "../../generated/api";
import { JobsPanel } from "./JobsPanel";

vi.mock("./JobDetailsPanel", () => ({ JobDetailsPanel: () => <div>JobDetailsPanel</div> }));

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

  it("explains automatic recovery when queued work has no executor", () => {
    const capacity = { queued_count: 3, active_worker_count: 0 } as never;
    render(<MemoryRouter><JobsPanel jobs={jobs()} loading={false} scopeKey="project-1" capacity={capacity} /></MemoryRouter>);
    expect(screen.getByRole("status").textContent).toContain("3 个任务等待本机执行器恢复");
    expect(screen.getByText(/无需运行命令/)).toBeTruthy();
    expect(screen.queryByText(/start-worker/)).toBeNull();
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

  it("keeps the newest submission first and does not reorder when the focused row changes", () => {
    const base = { project_id: "project-1", state: "SUCCEEDED", channel: "CPU", priority: 100, max_attempts: 1, revision: 1 } as const;
    const newest: Job = { ...base, id: "job-new", type: "EPISODE_COMPOSE", created_at: "2026-09-06T10:02:00+00:00" } as Job;
    const older: Job = { ...base, id: "job-old", type: "DELIVERY_BUILD", created_at: "2026-09-06T10:01:00+00:00" } as Job;
    const view = render(<MemoryRouter><JobsPanel jobs={[older, newest, older]} loading={false} scopeKey="project-1" focusJobId="job-old" /></MemoryRouter>);

    let rows = screen.getAllByRole("button", { name: "查看详情和产物" }).map((button) => button.closest(".job-row")?.querySelector("strong")?.textContent);
    expect(rows).toEqual(["合成整集视频", "制作交付包"]);

    view.rerender(<MemoryRouter><JobsPanel jobs={[newest, older]} loading={false} scopeKey="project-1" focusJobId="job-new" /></MemoryRouter>);
    rows = screen.getAllByRole("button", { name: "查看详情和产物" }).map((button) => button.closest(".job-row")?.querySelector("strong")?.textContent);
    expect(rows).toEqual(["合成整集视频", "制作交付包"]);
  });

  it("displays global quick actions with proper counts and triggers global batch handlers", async () => {
    const pauseSpy = vi.spyOn(api, "batchPauseJobs").mockResolvedValue({ paused_count: 2, jobs: [] });
    const resumeSpy = vi.spyOn(api, "batchResumeJobs").mockResolvedValue({ resumed_count: 1, jobs: [] });
    const cancelSpy = vi.spyOn(api, "batchCancelJobs").mockResolvedValue({ cancelled_count: 3, jobs: [] });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    const items: Job[] = [
      { id: "j-1", type: "TTS_GENERATION", project_id: "project-1", state: "QUEUED", channel: "CPU", priority: 100, max_attempts: 1, revision: 1 } as Job,
      { id: "j-2", type: "TTS_GENERATION", project_id: "project-1", state: "RUNNING", channel: "CPU", priority: 100, max_attempts: 1, revision: 1 } as Job,
      { id: "j-3", type: "TTS_GENERATION", project_id: "project-1", state: "PAUSED", channel: "CPU", priority: 100, max_attempts: 1, revision: 1 } as Job,
    ];

    render(<MemoryRouter><JobsPanel jobs={items} loading={false} scopeKey="project-1" /></MemoryRouter>);

    const resumeBtn = screen.getByRole("button", { name: "一键开始 (1)" });
    const pauseBtn = screen.getByRole("button", { name: "一键暂停 (2)" });
    const cancelBtn = screen.getByRole("button", { name: "一键取消 (3)" });

    expect(resumeBtn).toBeTruthy();
    expect(pauseBtn).toBeTruthy();
    expect(cancelBtn).toBeTruthy();

    await act(async () => {
      fireEvent.click(resumeBtn);
    });
    expect(resumeSpy).toHaveBeenCalledWith({ project_id: "project-1" });

    await act(async () => {
      fireEvent.click(pauseBtn);
    });
    expect(pauseSpy).toHaveBeenCalledWith({ project_id: "project-1" });

    await act(async () => {
      fireEvent.click(cancelBtn);
    });
    expect(cancelSpy).toHaveBeenCalledWith({ project_id: "project-1" });
  });

  it("supports multi-selection and displays batch action toolbar", async () => {
    const batchPauseSpy = vi.spyOn(api, "batchPauseJobs").mockResolvedValue({ paused_count: 1, jobs: [] });
    const batchResumeSpy = vi.spyOn(api, "batchResumeJobs").mockResolvedValue({ resumed_count: 1, jobs: [] });

    const items: Job[] = [
      { id: "j-1", type: "TTS_GENERATION", project_id: "project-1", state: "QUEUED", channel: "CPU", priority: 100, max_attempts: 1, revision: 1 } as Job,
      { id: "j-2", type: "TTS_GENERATION", project_id: "project-1", state: "PAUSED", channel: "CPU", priority: 100, max_attempts: 1, revision: 1 } as Job,
    ];

    render(<MemoryRouter><JobsPanel jobs={items} loading={false} scopeKey="project-1" /></MemoryRouter>);

    expect(screen.queryByRole("group", { name: "批量操作栏" })).toBeNull();

    const selectAllCheckbox = screen.getByRole("checkbox", { name: "全选当前显示任务" });
    await act(async () => {
      fireEvent.click(selectAllCheckbox);
    });

    expect(screen.getByRole("group", { name: "批量操作栏" })).toBeTruthy();
    expect(screen.getByText(/已选/)).toBeTruthy();

    const batchStartBtn = screen.getByRole("button", { name: "多选开始 (1)" });
    const batchPauseBtn = screen.getByRole("button", { name: "多选暂停 (1)" });

    await act(async () => {
      fireEvent.click(batchPauseBtn);
    });
    expect(batchPauseSpy).toHaveBeenCalledWith({ job_ids: expect.arrayContaining(["j-1", "j-2"]) });

    // Re-select because batch action clears selection
    await act(async () => {
      fireEvent.click(selectAllCheckbox);
    });
    const freshBatchStartBtn = screen.getByRole("button", { name: "多选开始 (1)" });
    await act(async () => {
      fireEvent.click(freshBatchStartBtn);
    });
    expect(batchResumeSpy).toHaveBeenCalledWith({ job_ids: expect.arrayContaining(["j-1", "j-2"]) });
  });

  it("provides row-level pause and start/resume buttons", async () => {
    const pauseSpy = vi.spyOn(api, "pauseJob").mockResolvedValue({ job: {} as never });
    const resumeSpy = vi.spyOn(api, "resumeJob").mockResolvedValue({ job: {} as never });

    const items: Job[] = [
      { id: "j-queued", type: "TTS_GENERATION", project_id: "project-1", state: "QUEUED", channel: "CPU", priority: 100, max_attempts: 1, revision: 1 } as Job,
      { id: "j-paused", type: "TTS_GENERATION", project_id: "project-1", state: "PAUSED", channel: "CPU", priority: 100, max_attempts: 1, revision: 1 } as Job,
    ];

    render(<MemoryRouter><JobsPanel jobs={items} loading={false} scopeKey="project-1" /></MemoryRouter>);

    const pauseBtn = screen.getByRole("button", { name: "暂停" });
    const resumeBtn = screen.getByRole("button", { name: "继续/开始" });

    await act(async () => {
      fireEvent.click(pauseBtn);
    });
    expect(pauseSpy).toHaveBeenCalledWith("j-queued");

    await act(async () => {
      fireEvent.click(resumeBtn);
    });
    expect(resumeSpy).toHaveBeenCalledWith("j-paused");
  });
});
