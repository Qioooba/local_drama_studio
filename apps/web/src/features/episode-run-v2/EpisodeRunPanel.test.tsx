import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { getEpisodeRun, preflightEpisodeRun, recoverEpisodeRun, resumeEpisodeRun, startEpisodeRun, type EpisodeProductionRun } from "./api";
import { EpisodeRunPanel } from "./EpisodeRunPanel";

vi.mock("./api", async (load) => {
  const actual = await load<typeof import("./api")>();
  return { ...actual, getEpisodeRun: vi.fn(), preflightEpisodeRun: vi.fn(), recoverEpisodeRun: vi.fn(), resumeEpisodeRun: vi.fn(), startEpisodeRun: vi.fn() };
});
vi.mock("../events/useProjectEventInvalidation", () => ({ useProjectEventInvalidation: vi.fn() }));

describe("EpisodeRunPanel", () => {
  it("renders the eight creator stages and exposes HITL separately from failures", async () => {
    const codes = ["STORY_ANALYSIS", "ASSET_EXTRACTION", "ASSET_COMPLETION", "SHOT_PLANNING", "SHOT_IMAGE", "VIDEO", "AUDIO_SUBTITLE", "COMPOSE_QC"] as const;
    const labels = ["故事解析", "资产提取", "资产补全", "分集 / 分镜规划", "镜头画面", "视频", "声音 / 字幕", "合成 / QC"];
    const pausedRun: EpisodeProductionRun = {
      id: "run-1", episode_id: "e1", project_id: "p1", status: "PAUSED_HITL",
      input_fingerprint: "f", production_mode: "BALANCED", mode_policy: { target_take_count: 2, label: "平衡", intent: "" },
      checkpoint_policy: "ON_EXCEPTION",
      stages: codes.map((code, index) => ({ ordinal: index + 1, code, label: labels[index]!, background_stages: [], status: index === 7 ? "PAUSED" : "PENDING", completed: 0, total: 1, remaining_count: 1, running_jobs: 0, failed: 0, failed_jobs: 0, needs_human_decision: index === 7 ? 1 : 0, hitl_jobs: index === 7 ? 1 : 0, estimated_remaining_seconds: null, estimate_status: "NOT_AVAILABLE", jobs: index === 7 ? [{ task_id: "task-compose", job_id: "job-1", job_state: "PAUSED_HITL", item_key: "episode-1", status: "WAITING" }] : [] })),
      pending_gate: { type: "HITL", reason: "等待整集审核" }, started_at: null, completed_at: null, updated_at: null, revision: 1,
      recovery: { recoverable: true, recoverable_jobs: [{ task_id: "task-compose", job_id: "job-1", job_state: "NEEDS_ATTENTION" }] },
      local_only: true as const, queue_reused: true as const,
    };
    vi.mocked(getEpisodeRun).mockResolvedValue(pausedRun);
    vi.mocked(resumeEpisodeRun).mockResolvedValue({ ...pausedRun, status: "RUNNING", pending_gate: null });
    vi.mocked(recoverEpisodeRun).mockResolvedValue(pausedRun);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<MemoryRouter initialEntries={["/?run=run-1"]}><QueryClientProvider client={client}><EpisodeRunPanel projectId="p1" episodeId="e1" /></QueryClientProvider></MemoryRouter>);

    expect(await screen.findByText("八阶段生产进度")).toBeTruthy();
    expect(screen.getAllByRole("tab")).toHaveLength(8);
    labels.forEach((label) => expect(screen.getByRole("tab", { name: new RegExp(label.replace(/[ /]/g, ".*")) })).toBeTruthy());
    expect(screen.getByText("1 项待人工决定")).toBeTruthy();
    expect(screen.getAllByText("尚无本机估时")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /批准 Gate 并继续/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /检查租约并恢复/ }));
    await waitFor(() => expect(recoverEpisodeRun).toHaveBeenCalledWith("run-1"));
    expect(await screen.findByText("租约与可恢复任务已检查；当前仍停在人工 Gate，未自动批准。")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "阻塞与任务事件" }));
    expect(screen.getByRole("dialog", { name: "合成 / QC · 阻塞与任务事件" })).toBeTruthy();
    expect(screen.getByText("task-compose")).toBeTruthy();
    expect(screen.getByText("等待整集审核")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭抽屉" }));

    fireEvent.click(screen.getByRole("tab", { name: /1 故事解析/ }));
    expect(screen.queryByText("1 项待人工决定")).toBeNull();
    expect(screen.getAllByText("尚无本机估时")).toHaveLength(1);
  });

  it("freezes the selected creator checkpoint into preflight and start", async () => {
    vi.mocked(preflightEpisodeRun).mockResolvedValue({
      episode: { id: "e1", code: "EP01", title: "第一集", project_id: "p1", project_title: "项目" },
      status: "PASS", checks: [], blockers: [], input_fingerprint: "f", tts_enabled: true,
      production_mode: "BALANCED", mode_policy: { target_take_count: 2, label: "平衡", intent: "" },
      checkpoint_policy: "BEFORE_VIDEO", would_create_jobs: false, runtime_contacted: false, network_contacted: false, mutated: false,
    });
    vi.mocked(startEpisodeRun).mockResolvedValue({
      id: "run-2", episode_id: "e1", project_id: "p1", status: "RUNNING", input_fingerprint: "f",
      production_mode: "BALANCED", mode_policy: { target_take_count: 2, label: "平衡", intent: "" }, checkpoint_policy: "BEFORE_VIDEO",
      stages: [], pending_gate: null, started_at: null, completed_at: null, updated_at: null, revision: 1, local_only: true, queue_reused: true,
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<MemoryRouter><QueryClientProvider client={client}><EpisodeRunPanel projectId="p1" episodeId="e1" /></QueryClientProvider></MemoryRouter>);

    fireEvent.change(screen.getByLabelText("人工确认节点"), { target: { value: "BEFORE_VIDEO" } });
    await waitFor(() => expect(preflightEpisodeRun).toHaveBeenLastCalledWith("e1", true, "BALANCED", "BEFORE_VIDEO"));
    fireEvent.click(await screen.findByRole("button", { name: "开始整集生产" }));
    await waitFor(() => expect(startEpisodeRun).toHaveBeenCalledWith("e1", true, "BALANCED", "BEFORE_VIDEO"));
  });

  it("hands a completed automation run to the human review, audio, and timeline chain", async () => {
    const completedRun: EpisodeProductionRun = {
      id: "run-complete", episode_id: "e1", project_id: "p1", status: "COMPLETED", input_fingerprint: "f",
      production_mode: "BALANCED", mode_policy: { target_take_count: 2, label: "平衡", intent: "" }, checkpoint_policy: "ON_EXCEPTION",
      stages: [{ ordinal: 8, code: "COMPOSE_QC", label: "合成 / QC", background_stages: [], status: "COMPLETED", completed: 1, total: 1, remaining_count: 0, running_jobs: 0, failed: 0, failed_jobs: 0, needs_human_decision: 0, hitl_jobs: 0, estimated_remaining_seconds: 0, estimate_status: "AVAILABLE", jobs: [] }],
      pending_gate: null, started_at: null, completed_at: "2026-08-24T00:00:00Z", updated_at: "2026-08-24T00:00:00Z", revision: 2, local_only: true, queue_reused: true,
    };
    vi.mocked(getEpisodeRun).mockResolvedValue(completedRun);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<MemoryRouter initialEntries={["/?run=run-complete"]}><QueryClientProvider client={client}><EpisodeRunPanel projectId="p1" episodeId="e1" /></QueryClientProvider></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "自动生产已结束，进入人工成片链路" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "1 审核候选" }).getAttribute("href")).toBe("/projects/p1/episodes/e1/review");
    expect(screen.getByRole("link", { name: "2 确认声音" }).getAttribute("href")).toBe("/projects/p1/episodes/e1/audio");
    expect(screen.getByRole("link", { name: "3 创建并冻结时间线" }).getAttribute("href")).toBe("/projects/p1/episodes/e1/timeline");
  });

  it("links a blocked stage to the exact shot that needs repair", async () => {
    const blockedRun: EpisodeProductionRun = {
      id: "run-blocked", episode_id: "e1", project_id: "p1", status: "NEEDS_ATTENTION", input_fingerprint: "f",
      production_mode: "BALANCED", mode_policy: { target_take_count: 2, label: "平衡", intent: "" }, checkpoint_policy: "ON_EXCEPTION",
      stages: [{
        ordinal: 6, code: "VIDEO", label: "视频", background_stages: [], status: "BLOCKED", completed: 0, total: 1,
        remaining_count: 1, running_jobs: 0, failed: 1, failed_jobs: 1, needs_human_decision: 0, hitl_jobs: 0,
        estimated_remaining_seconds: null, estimate_status: "NOT_AVAILABLE", jobs: [],
        issues: [{ shot_id: "shot-7", shot_code: "SH-007", status: "BLOCKED", code: "VIDEO_CANDIDATE_REQUIRED", job_id: "job-7" }],
      }],
      pending_gate: null, started_at: null, completed_at: null, updated_at: null, revision: 3, local_only: true, queue_reused: true,
    };
    vi.mocked(getEpisodeRun).mockResolvedValue(blockedRun);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<MemoryRouter initialEntries={["/?run=run-blocked"]}><QueryClientProvider client={client}><EpisodeRunPanel projectId="p1" episodeId="e1" /></QueryClientProvider></MemoryRouter>);

    const repairLink = await screen.findByRole("link", { name: "打开此镜头" });
    expect(repairLink.getAttribute("href")).toBe("/projects/p1/episodes/e1/direct/shot-7");
    expect(screen.getByText("SH-007")).toBeTruthy();
  });
});
