import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { getEpisodeRun, preflightEpisodeRun, startEpisodeRun } from "./api";
import { EpisodeRunPanel } from "./EpisodeRunPanel";

vi.mock("./api", async (load) => {
  const actual = await load<typeof import("./api")>();
  return { ...actual, getEpisodeRun: vi.fn(), preflightEpisodeRun: vi.fn(), startEpisodeRun: vi.fn() };
});
vi.mock("../events/useProjectEventInvalidation", () => ({ useProjectEventInvalidation: vi.fn() }));

describe("EpisodeRunPanel", () => {
  it("renders the eight creator stages and exposes HITL separately from failures", async () => {
    const codes = ["STORY_ANALYSIS", "ASSET_EXTRACTION", "ASSET_COMPLETION", "SHOT_PLANNING", "SHOT_IMAGE", "VIDEO", "AUDIO_SUBTITLE", "COMPOSE_QC"] as const;
    const labels = ["故事解析", "资产提取", "资产补全", "分集 / 分镜规划", "镜头画面", "视频", "声音 / 字幕", "合成 / QC"];
    vi.mocked(getEpisodeRun).mockResolvedValue({
      id: "run-1", episode_id: "e1", project_id: "p1", status: "PAUSED_HITL",
      input_fingerprint: "f", production_mode: "BALANCED", mode_policy: { target_take_count: 2, label: "平衡", intent: "" },
      checkpoint_policy: "ON_EXCEPTION",
      stages: codes.map((code, index) => ({ ordinal: index + 1, code, label: labels[index]!, background_stages: [], status: index === 7 ? "PAUSED" : "PENDING", completed: 0, total: 1, remaining_count: 1, running_jobs: 0, failed: 0, failed_jobs: 0, needs_human_decision: index === 7 ? 1 : 0, hitl_jobs: index === 7 ? 1 : 0, estimated_remaining_seconds: null, estimate_status: "NOT_AVAILABLE", jobs: index === 7 ? [{ task_id: "task-compose", job_id: "job-1", job_state: "PAUSED_HITL", item_key: "episode-1", status: "WAITING" }] : [] })),
      pending_gate: { type: "HITL", reason: "等待整集审核" }, started_at: null, completed_at: null, updated_at: null, revision: 1, local_only: true, queue_reused: true,
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<MemoryRouter initialEntries={["/?run=run-1"]}><QueryClientProvider client={client}><EpisodeRunPanel projectId="p1" episodeId="e1" /></QueryClientProvider></MemoryRouter>);

    expect(await screen.findByText("八阶段生产进度")).toBeTruthy();
    expect(screen.getAllByRole("tab")).toHaveLength(8);
    labels.forEach((label) => expect(screen.getByRole("tab", { name: new RegExp(label.replace(/[ /]/g, ".*")) })).toBeTruthy());
    expect(screen.getByText("1 项待人工决定")).toBeTruthy();
    expect(screen.getAllByText("尚无本机估时")).toHaveLength(1);

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
});
