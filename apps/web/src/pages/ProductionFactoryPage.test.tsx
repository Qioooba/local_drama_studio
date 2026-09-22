import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getProjectOverviewV2, getStoryboardWorkspace } from "../generated/api";
import { planShotKeyframeBatch, submitShotKeyframeBatch } from "../features/director-v2/shotKeyframeBatchApi";
import {
  controlProductionSession,
  createProductionSession,
  extendProductionSessionBudget,
  getProductionSessionReview,
  listProductionSessions,
  planProductionSession,
  rerollProductionChoice,
  resolveSessionStart,
  retryProductionSessionItem,
  startProductionSession,
} from "../features/production-sessions/client";
import { ProductionFactoryPage } from "./ProductionFactoryPage";

vi.mock("../generated/api", () => ({ getProjectOverviewV2: vi.fn(), getStoryboardWorkspace: vi.fn() }));
vi.mock("../features/director-v2/shotKeyframeBatchApi", () => ({
  planShotKeyframeBatch: vi.fn(), submitShotKeyframeBatch: vi.fn(),
}));
vi.mock("../features/episode-plan-v2/AssetProposalReviewPanel", () => ({
  AssetProposalReviewPanel: () => <section aria-label="资产身份建议审核面板">资产身份建议审核面板</section>,
}));
vi.mock("../features/production-sessions/client", () => ({
  confirmProductionEpisode: vi.fn(), controlProductionSession: vi.fn(), createProductionSession: vi.fn(),
  extendProductionSessionBudget: vi.fn(), getProductionSession: vi.fn(),
  getProductionSessionReview: vi.fn(), listProductionSessions: vi.fn(), planProductionSession: vi.fn(),
  rerollProductionChoice: vi.fn(), retryProductionSessionItem: vi.fn(), resolveSessionStart: vi.fn(), startProductionSession: vi.fn(),
}));

const session = {
  id: "session-1", project_id: "project-1", scope_type: "WHOLE_DRAMA", production_mode: "BALANCED",
  checkpoint_policy: "ON_EXCEPTION", status: "RUNNING", current_stage: "VIDEO", plan_hash: "a".repeat(64),
  configuration: {}, counters: { total: 2, pending: 1, running: 1, waiting: 0, blocked: 0, failed: 0, completed: 0, cancelled: 0 },
  item_count: 2, revision: 2, allowed_actions: ["PAUSE", "CANCEL"], created_at: "2026-09-21T00:00:00Z", updated_at: "2026-09-21T00:00:00Z",
} as const;

/** Complete bounded session page: the client now preserves the server paging metadata. */
function sessionPage(page: { items: unknown[]; total: number; next_cursor?: number | null; cursor?: number; limit?: number }) {
  return {
    items: page.items,
    total: page.total,
    cursor: page.cursor ?? 0,
    limit: page.limit ?? 50,
    next_cursor: page.next_cursor ?? null,
  } as never;
}

function renderPage(entry = "/projects/project-1/factory") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[entry]}><Routes><Route path="/projects/:projectId/factory" element={<ProductionFactoryPage />} /></Routes></MemoryRouter></QueryClientProvider>);
}

describe("ProductionFactoryPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getProjectOverviewV2).mockResolvedValue({
      project: { id: "project-1", code: "DRAMA", title: "测试漫剧", status: "ACTIVE", revision: 1 },
      next_action: null, blockers: [], recent_activity: [],
      seasons: [{ id: "season-1", code: "S01", title: "第一季", number: 1, episodes: [
        { id: "episode-1", code: "EP01", title: "第一集", number: 1, production_status: "PLANNED" },
        { id: "episode-2", code: "EP02", title: "第二集", number: 2, production_status: "PLANNED" },
      ] }], observed_at: "2026-09-21T00:00:00Z", read_only: true, runtime_contacted: false, network_contacted: false, mutated: false,
    } as never);
    vi.mocked(listProductionSessions).mockResolvedValue(sessionPage({ items: [], total: 0 }));
    vi.mocked(planProductionSession).mockResolvedValue({ plan: {
      project_id: "project-1", scope_type: "WHOLE_DRAMA", production_mode: "BALANCED", checkpoint_policy: "ON_EXCEPTION",
      episode_count: 2, total_shot_count: 8, estimated_candidate_count: 16, can_create: true,
      plan_hash: "a".repeat(64), configuration: {}, stages: [], warnings: [],
      episodes: [{ episode_id: "episode-1", code: "EP01", title: "第一集", ordinal: 1, revision: 1, readiness: "READY", shot_count: 4 }],
    } });
    vi.mocked(createProductionSession).mockResolvedValue({ session: { ...session, status: "READY", revision: 1, allowed_actions: ["START", "PAUSE", "CANCEL"] } as never });
    vi.mocked(startProductionSession).mockResolvedValue({ session: session as never });
    vi.mocked(extendProductionSessionBudget).mockResolvedValue({ session: session as never });
    vi.mocked(retryProductionSessionItem).mockResolvedValue({ session: session as never });
    vi.mocked(rerollProductionChoice).mockResolvedValue({ session: session as never });
    vi.mocked(resolveSessionStart).mockResolvedValue({ state: "STARTED", session: session as never });
    vi.mocked(getStoryboardWorkspace).mockResolvedValue({
      storyboard: {
        episode: { id: "episode-1", code: "EP01", title: "第一集" },
        items: [{ id: "shot-1", revision: 3 }, { id: "shot-2", revision: 4 }],
      },
    } as never);
    vi.mocked(planShotKeyframeBatch).mockResolvedValue({
      plan: {
        episode_id: "episode-1", project_id: "project-1", targets: [], frame_strategy: "FIRST_ONLY",
        candidate_count: 2, plan_hash: "b".repeat(64), valid: true, issues: [], items: [],
        summary: { shots: 2, jobs: 4, blocked: 0 },
      },
    });
    vi.mocked(submitShotKeyframeBatch).mockResolvedValue({
      batch: {
        id: "keyframe-batch-1", episode_id: "episode-1", project_id: "project-1",
        frame_strategy: "FIRST_ONLY", candidate_count: 2, status: "QUEUED", plan_hash: "b".repeat(64),
        created_at: "2026-09-21T00:00:00Z", summary: { total: 4, succeeded: 0, failed: 0, active: 4 },
      },
    });
  });

  it("preflights before creating and starting a whole-drama session", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "让机器持续生产，最后集中人工审核" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "预检生产计划" }));
    expect(await screen.findByText("16")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "一键生成整部" }));

    await waitFor(() => expect(createProductionSession).toHaveBeenCalledTimes(1));
    expect(planProductionSession).toHaveBeenCalledWith("project-1", expect.objectContaining({ scope_type: "WHOLE_DRAMA", episode_ids: [], production_mode: "BALANCED" }));
    expect(createProductionSession).toHaveBeenCalledWith("project-1", expect.any(Object), expect.any(String));
    expect(startProductionSession).toHaveBeenCalledWith(expect.objectContaining({ id: "session-1", status: "READY" }), expect.any(String));
    expect(await screen.findByText(/生产会话已启动/)).toBeTruthy();
  });

  it("opens from an episode workspace with the single episode scope frozen", async () => {
    renderPage("/projects/project-1/factory?episode=episode-2");
    expect(await screen.findByDisplayValue("只生产一集")).toBeTruthy();
    expect((await screen.findAllByDisplayValue("EP02 · 第二集"))).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "预检生产计划" }));
    await waitFor(() => expect(planProductionSession).toHaveBeenCalledWith(
      "project-1",
      expect.objectContaining({ scope_type: "SINGLE_EPISODE", episode_ids: ["episode-2"] }),
    ));
  });

  it("fills only the selected episode keyframe gaps with one action", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "一键补齐本集关键帧" });
    const action = screen.getByRole("button", { name: "一键补齐关键帧" });
    await waitFor(() => expect(action.hasAttribute("disabled")).toBe(false));
    fireEvent.click(action);

    await waitFor(() => expect(getStoryboardWorkspace).toHaveBeenCalledWith("episode-1"));
    await waitFor(() => expect(planShotKeyframeBatch).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(submitShotKeyframeBatch).toHaveBeenCalledTimes(1));
    expect(planShotKeyframeBatch).toHaveBeenCalledWith(
      "episode-1",
      [{ shot_id: "shot-1", expected_revision: 3 }, { shot_id: "shot-2", expected_revision: 4 }],
      "FIRST_ONLY",
      2,
    );
    expect(await screen.findByText(/已提交 4 个关键帧缺口任务/)).toBeTruthy();
  });

  it("shows machine temporary assets in centralized review until a person confirms them", async () => {
    vi.mocked(listProductionSessions).mockResolvedValue(sessionPage({
      items: [{ ...session, status: "WAITING_REVIEW", current_stage: "WAITING_REVIEW", counters: { ...session.counters, running: 0, waiting: 1 } } as never],
      total: 1,
    }));
    vi.mocked(getProductionSessionReview).mockResolvedValue({
      session_id: "session-1", project_id: "project-1", session_status: "WAITING_REVIEW", session_revision: 2,
      summary: {}, cursor: 0, limit: 100, total: 1, next_cursor: null,
      read_only: true, human_approval_written: false, request_shape: "bounded_production_session_review_v2",
      items: [{
        session_item_id: "item-1", episode_id: "episode-1", episode_code: "EP01", episode_title: "第一集",
        ordinal: 1, item_revision: 2, item_state: "WAITING", current_stage: "WAITING_REVIEW", review_status: "BLOCKED",
        choices: [], asset_inputs: [{
          id: "asset-input-1", asset_proposal_id: "proposal-1", story_asset_id: "asset-1", kind: "CHARACTER",
          name: "主角", asset_code: "TMP_CHAR_001", proposal_status: "PENDING", review_status: "PENDING",
          selection_authority: "MACHINE_TEMPORARY", human_approved: false,
        }],
        timeline: null, timeline_choice_consistency: { status: "MATCH" }, preview_render: null,
        blockers: [{ code: "SESSION_ASSET_IDENTITIES_REVIEW_REQUIRED", message: "1 个机器临时资产身份仍需人工确认" }],
        repair_plan: {
          recommended_strategy: "RECOMPOSE_ONLY", summary: "当前问题需要先完成人工处理", effects: [],
          prerequisites: [{ action: "REVIEW_ASSET_IDENTITIES", message: "先确认本次生产实际使用的机器临时资产" }],
          can_retry_now: false, read_only: true, mutated: false,
        },
        allowed_actions: ["REVIEW_ASSET_IDENTITIES"],
      }],
    });

    renderPage();
    expect(await screen.findByRole("heading", { name: "逐集检查预览与机器临时选择" })).toBeTruthy();
    expect(await screen.findByRole("region", { name: "资产身份建议审核面板" })).toBeTruthy();
    expect(screen.getByText("主角 · TMP_CHAR_001")).toBeTruthy();
    expect(screen.getByText(/等待人工确认/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "只重建预览" })).toBeNull();
  });

  it("shows and submits the server-computed minimal repair plan", async () => {
    vi.mocked(listProductionSessions).mockResolvedValue(sessionPage({
      items: [{ ...session, status: "WAITING_USER", current_stage: "TIMELINE_PREVIEW" } as never], total: 1,
    }));
    vi.mocked(getProductionSessionReview).mockResolvedValue({
      session_id: "session-1", project_id: "project-1", session_status: "WAITING_USER", session_revision: 2,
      summary: {}, cursor: 0, limit: 100, total: 1, next_cursor: null,
      read_only: true, human_approval_written: false, request_shape: "bounded_production_session_review_v2",
      items: [{
        session_item_id: "item-1", episode_id: "episode-1", episode_code: "EP01", episode_title: "第一集",
        ordinal: 1, item_revision: 3, item_state: "BLOCKED", current_stage: "TIMELINE_PREVIEW", review_status: "BLOCKED",
        choices: [], asset_inputs: [], timeline: null, timeline_choice_consistency: { status: "MATCH" }, preview_render: null,
        blockers: [{ code: "EPISODE_PREVIEW_RENDER_MISSING", message: "本集还没有已验证预览成片" }],
        repair_plan: {
          recommended_strategy: "RECOMPOSE_ONLY", summary: "保留现有画面和视频，只重建时间线与预览成片",
          effects: ["KEEP_GENERATED_MEDIA", "REBUILD_TIMELINE", "RENDER_PREVIEW"], prerequisites: [],
          can_retry_now: true, read_only: true, mutated: false,
        },
        allowed_actions: ["REQUEST_LOCAL_RETRY"],
      }],
    });

    renderPage();
    expect(await screen.findByText(/返工建议：保留现有画面和视频/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "只重建预览" }));
    await waitFor(() => expect(retryProductionSessionItem).toHaveBeenCalledTimes(1));
    expect(retryProductionSessionItem).toHaveBeenCalledWith(
      expect.objectContaining({ id: "session-1" }),
      expect.objectContaining({ session_item_id: "item-1", repair_plan: expect.objectContaining({ recommended_strategy: "RECOMPOSE_ONLY" }) }),
    );
  });

  it("only increases the budget dimensions that are actually exhausted", async () => {
    const waitingSession = {
      ...session,
      status: "WAITING_USER",
      current_stage: "BUDGET_WAIT",
      allowed_actions: ["CANCEL"],
      budget: {
        limits: {
          max_duration_seconds: 86400,
          max_new_jobs: 600,
          max_attempts_total: 1200,
          max_output_bytes: 100 * 1024 ** 3,
          max_queued_gpu_jobs: 8,
          dispatch_shots_per_tick: 4,
        },
        usage: { elapsed_seconds: 86400, new_jobs: 120, attempts_total: 130, output_bytes: 1024, global_queued_gpu_jobs: 0 },
        hard_blockers: [{
          code: "PRODUCTION_SESSION_DURATION_BUDGET_EXHAUSTED",
          message: "最长运行时间已用完",
          limit_key: "max_duration_seconds",
          usage: 86400,
          limit: 86400,
        }],
      },
    } as const;
    vi.mocked(listProductionSessions).mockResolvedValue(sessionPage({ items: [waitingSession as never], total: 1 }));
    vi.mocked(getProductionSessionReview).mockResolvedValue({
      session_id: "session-1", project_id: "project-1", session_status: "WAITING_USER", session_revision: 2,
      summary: {}, cursor: 0, limit: 100, total: 0, next_cursor: null, items: [],
      read_only: true, human_approval_written: false, request_shape: "bounded_production_session_review_v2",
    });

    renderPage();
    const action = await screen.findByRole("button", { name: "提高已耗尽预算并继续" });
    fireEvent.click(action);

    await waitFor(() => expect(extendProductionSessionBudget).toHaveBeenCalledTimes(1));
    expect(extendProductionSessionBudget).toHaveBeenCalledWith(
      expect.objectContaining({ id: "session-1" }),
      { max_duration_seconds: 172800 },
    );
  });

  it("continues a creator checkpoint from the parent production session", async () => {
    const gated = {
      ...session,
      status: "WAITING_USER",
      current_stage: "VIDEO",
      allowed_actions: ["RESUME", "CANCEL"],
    } as const;
    vi.mocked(listProductionSessions).mockResolvedValue(sessionPage({ items: [gated as never], total: 1 }));
    vi.mocked(getProductionSessionReview).mockResolvedValue({
      session_id: "session-1", project_id: "project-1", session_status: "WAITING_USER", session_revision: 2,
      summary: {}, cursor: 0, limit: 100, total: 0, next_cursor: null, items: [],
      read_only: true, human_approval_written: false, request_shape: "bounded_production_session_review_v2",
    });
    vi.mocked(controlProductionSession).mockResolvedValue({
      session: { ...gated, status: "RUNNING", revision: 3, allowed_actions: ["PAUSE", "CANCEL"] } as never,
    });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "继续" }));

    await waitFor(() => expect(controlProductionSession).toHaveBeenCalledWith(
      expect.objectContaining({ id: "session-1", status: "WAITING_USER" }),
      "resume",
    ));
    expect(await screen.findByText("会话已恢复。")).toBeTruthy();
  });

  it("rerolls a review-ready episode while another episode keeps the parent waiting for user", async () => {
    const mixedSession = {
      ...session,
      status: "WAITING_USER",
      current_stage: "ASSETS",
      counters: { ...session.counters, pending: 0, running: 0, waiting: 1, blocked: 1 },
      allowed_actions: ["CANCEL"],
    } as const;
    const choice = {
      id: "choice-video-1", target_kind: "SHOT", target_id: "shot-1", shot_code: "S001",
      slot_role: "VIDEO", candidate_id: "video-1", choice_type: "MACHINE",
      selection_state: "TEMPORARY", selection_authority: "MACHINE_TEMPORARY",
      human_approved: false, available_human_approval_id: null, revision: 1,
      media: { id: "video-1", integrity_status: "VERIFIED" },
    };
    vi.mocked(listProductionSessions).mockResolvedValue(sessionPage({ items: [mixedSession as never], total: 1 }));
    vi.mocked(getProductionSessionReview).mockResolvedValue({
      session_id: "session-1", project_id: "project-1", session_status: "WAITING_USER", session_revision: 2,
      summary: {}, cursor: 0, limit: 100, total: 1, next_cursor: null,
      read_only: true, human_approval_written: false, request_shape: "bounded_production_session_review_v2",
      items: [{
        session_item_id: "item-1", episode_id: "episode-1", episode_code: "EP01", episode_title: "第一集",
        ordinal: 1, item_revision: 2, item_state: "WAITING", current_stage: "WAITING_REVIEW",
        review_status: "READY_FOR_HUMAN_REVIEW", choices: [choice], asset_inputs: [], timeline: null,
        timeline_choice_consistency: { status: "MATCH" }, preview_render: null, blockers: [],
        repair_plan: { recommended_strategy: null, summary: "无需返工", effects: [], prerequisites: [], can_retry_now: false, read_only: true, mutated: false },
        allowed_actions: ["CONFIRM_EPISODE"],
      }],
    } as never);

    renderPage();
    expect((await screen.findByRole("link", { name: "审核此候选" })).getAttribute("href")).toBe(
      "/projects/project-1/episodes/episode-1/post/review?targetKind=MEDIA_VERSION&targetId=video-1",
    );
    fireEvent.click(await screen.findByRole("button", { name: "换一个" }));

    await waitFor(() => expect(rerollProductionChoice).toHaveBeenCalledWith(
      expect.objectContaining({ id: "session-1", status: "WAITING_USER" }),
      expect.objectContaining({ id: "choice-video-1" }),
    ));
  });

  it("labels session TTS choices as dialogue audio without offering an unsupported reroll", async () => {
    const waitingReview = {
      ...session,
      status: "WAITING_REVIEW",
      current_stage: "WAITING_REVIEW",
      counters: { ...session.counters, pending: 0, running: 0, waiting: 1 },
      allowed_actions: ["CANCEL"],
    } as const;
    vi.mocked(listProductionSessions).mockResolvedValue(sessionPage({ items: [waitingReview as never], total: 1 }));
    vi.mocked(getProductionSessionReview).mockResolvedValue({
      session_id: "session-1", project_id: "project-1", session_status: "WAITING_REVIEW", session_revision: 2,
      summary: {}, cursor: 0, limit: 100, total: 1, next_cursor: null,
      read_only: true, human_approval_written: false, request_shape: "bounded_production_session_review_v2",
      items: [{
        session_item_id: "item-1", episode_id: "episode-1", episode_code: "EP01", episode_title: "第一集",
        ordinal: 1, item_revision: 2, item_state: "WAITING", current_stage: "WAITING_REVIEW",
        review_status: "READY_FOR_HUMAN_REVIEW", asset_inputs: [], timeline: null,
        choices: [{
          id: "choice-tts-1", target_kind: "DIALOGUE_LINE", target_id: "line-1", shot_code: null,
          slot_role: "TTS_AUDIO", candidate_id: "audio-1", choice_type: "MACHINE",
          selection_state: "TEMPORARY", selection_authority: "MACHINE_TEMPORARY",
          human_approved: false, available_human_approval_id: "audio-approval-1", revision: 1,
          media: { id: "audio-1", integrity_status: "VERIFIED" },
        }],
        timeline_choice_consistency: { status: "MATCH" },
        preview_render: { id: "preview-1", integrity_status: "VERIFIED", human_approval_current: false },
        blockers: [],
        repair_plan: { recommended_strategy: null, summary: "无需返工", effects: [], prerequisites: [], can_retry_now: false, read_only: true, mutated: false },
        allowed_actions: ["CONFIRM_CHOICES"],
      }],
    } as never);

    renderPage();

    expect(await screen.findByText("line-1 · 对白配音")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "换一个" })).toBeNull();
    expect(screen.getByRole("link", { name: "审核当前预览" }).getAttribute("href")).toBe(
      "/projects/project-1/episodes/episode-1/post/review?targetKind=EPISODE_RENDER_VERSION&targetId=preview-1",
    );
    expect(screen.getByRole("button", { name: "等待正式审核批准" }).getAttribute("title")).toBe("请先在正式审核中批准当前预览成片");
  });

  it("marks a confirmed episode complete and sends the operator to delivery", async () => {
    vi.mocked(listProductionSessions).mockResolvedValue(sessionPage({
      items: [{ ...session, status: "COMPLETED", current_stage: "COMPLETED", counters: { ...session.counters, pending: 0, running: 0, completed: 1 } } as never],
      total: 1,
    }));
    vi.mocked(getProductionSessionReview).mockResolvedValue({
      session_id: "session-1", project_id: "project-1", session_status: "COMPLETED", session_revision: 3,
      summary: {}, cursor: 0, limit: 100, total: 1, next_cursor: null,
      read_only: true, human_approval_written: false, request_shape: "bounded_production_session_review_v2",
      items: [{
        session_item_id: "item-1", episode_id: "episode-1", episode_code: "EP01", episode_title: "第一集",
        ordinal: 1, item_revision: 3, item_state: "COMPLETED", current_stage: "COMPLETED",
        review_status: "REVIEWED", asset_inputs: [], timeline: { id: "timeline-1", revision_no: 1, status: "FROZEN" },
        choices: [{
          id: "choice-video-1", target_kind: "SHOT", target_id: "shot-1", shot_code: "S001",
          slot_role: "VIDEO", candidate_id: "video-1", choice_type: "HUMAN",
          selection_state: "CONFIRMED", selection_authority: "HUMAN_CONFIRMED",
          human_approved: true, available_human_approval_id: "video-approval-1", revision: 2,
          media: { id: "video-1", integrity_status: "VERIFIED" },
        }],
        timeline_choice_consistency: { status: "MATCH" },
        preview_render: { id: "preview-1", integrity_status: "VERIFIED", human_approval_current: true },
        blockers: [],
        repair_plan: { recommended_strategy: null, summary: "无需返工", effects: [], prerequisites: [], can_retry_now: false, read_only: true, mutated: false },
        allowed_actions: [],
      }],
    } as never);

    renderPage();

    expect((await screen.findByRole("button", { name: "本集已确认" })).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("link", { name: "进入本集交付" }).getAttribute("href")).toBe("/projects/project-1/episodes/episode-1/delivery");
  });

  it("keeps the created READY session visible and offers to start that same session when start fails", async () => {
    const readySession = { ...session, status: "READY", revision: 1, allowed_actions: ["START", "PAUSE", "CANCEL"] } as never;
    vi.mocked(createProductionSession).mockResolvedValue({ session: readySession });
    vi.mocked(startProductionSession).mockRejectedValue(new Error("网络中断"));
    vi.mocked(resolveSessionStart).mockResolvedValue({ state: "STARTED", session: { ...session, status: "RUNNING", revision: 3 } as never });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "预检生产计划" }));
    await screen.findByText("16");
    fireEvent.click(screen.getByRole("button", { name: "一键生成整部" }));

    const recovery = await screen.findByText(/启动没有成功/);
    expect(recovery).toBeTruthy();
    expect(screen.getByText(/会话 session-1/)).toBeTruthy();
    const retry = screen.getByRole("button", { name: "启动此会话" });

    // The retry reuses the original create/start idempotency keys and never creates again.
    fireEvent.click(retry);
    await waitFor(() => expect(resolveSessionStart).toHaveBeenCalledWith("session-1", expect.any(String), 1));
    expect(createProductionSession).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByText(/启动没有成功/)).toBeNull());
  });

  it("distinguishes an already running session, a terminal session and a revision change on retry", async () => {
    const readySession = { ...session, status: "READY", revision: 1, allowed_actions: ["START", "PAUSE", "CANCEL"] } as never;
    vi.mocked(createProductionSession).mockResolvedValue({ session: readySession });
    vi.mocked(startProductionSession).mockRejectedValue(new Error("服务端拒绝"));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "预检生产计划" }));
    await screen.findByText("16");
    fireEvent.click(screen.getByRole("button", { name: "一键生成整部" }));
    await screen.findByRole("button", { name: "启动此会话" });

    vi.mocked(resolveSessionStart).mockResolvedValue({ state: "REVISION_CHANGED", session: { ...(readySession as object), revision: 4 } as never });
    fireEvent.click(screen.getByRole("button", { name: "启动此会话" }));
    expect(await screen.findByText(/会话修订已变化/)).toBeTruthy();

    vi.mocked(resolveSessionStart).mockResolvedValue({ state: "TERMINAL", session: { ...(readySession as object), status: "CANCELLED" } as never });
    fireEvent.click(screen.getByRole("button", { name: "启动此会话" }));
    expect(await screen.findByText(/终态/)).toBeTruthy();
    expect(createProductionSession).toHaveBeenCalledTimes(1);
  });

  it("offers START for any listed session whose allowed_actions contains START", async () => {
    const readySession = { ...session, status: "READY", revision: 1, allowed_actions: ["START", "PAUSE", "CANCEL"] } as never;
    vi.mocked(listProductionSessions).mockResolvedValue(sessionPage({ items: [readySession], total: 1 }));
    vi.mocked(getProductionSessionReview).mockResolvedValue({
      session_id: "session-1", project_id: "project-1", session_status: "READY", session_revision: 1,
      summary: {}, cursor: 0, limit: 100, total: 0, next_cursor: null, items: [],
      read_only: true, human_approval_written: false, request_shape: "bounded_production_session_review_v2",
    });
    vi.mocked(resolveSessionStart).mockResolvedValue({ state: "STARTED", session: { ...session, status: "RUNNING", revision: 3 } as never });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "启动此会话" }));
    await waitFor(() => expect(resolveSessionStart).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/生产会话已启动|会话已启动/)).toBeTruthy();
  });

  it("reaches older production sessions and further review items instead of stopping at the first page", async () => {
    const manySessions = Array.from({ length: 51 }, (_, index) => ({ ...session, id: `session-${index + 1}`, revision: 2 }));
    vi.mocked(listProductionSessions).mockImplementation(async (_projectId: string, page: { cursor?: number } = {}) => {
      const cursor = page.cursor ?? 0;
      const items = manySessions.slice(cursor, cursor + 50);
      const next = cursor + 50 < manySessions.length ? cursor + 50 : null;
      return sessionPage({ items, total: manySessions.length, cursor, next_cursor: next });
    });
    const manyReviewItems = Array.from({ length: 101 }, (_, index) => ({
      session_item_id: `item-${index + 1}`, episode_id: `episode-${index + 1}`, episode_code: `EP${index + 1}`, episode_title: null,
      ordinal: index + 1, item_revision: 1, item_state: "WAITING", current_stage: "WAITING_REVIEW", review_status: "BLOCKED",
      choices: [], asset_inputs: [], timeline: null, timeline_choice_consistency: { status: "MATCH" }, preview_render: null,
      blockers: [], repair_plan: { recommended_strategy: null, summary: "无需返工", effects: [], prerequisites: [], can_retry_now: false, read_only: true, mutated: false },
      allowed_actions: [],
    }));
    vi.mocked(getProductionSessionReview).mockImplementation(async (_sessionId: string, page: { cursor?: number } = {}) => {
      const cursor = page.cursor ?? 0;
      const items = manyReviewItems.slice(cursor, cursor + 100);
      const next = cursor + 100 < manyReviewItems.length ? cursor + 100 : null;
      return {
        session_id: "session-1", project_id: "project-1", session_status: "WAITING_REVIEW", session_revision: 2,
        summary: {}, cursor, limit: 100, total: manyReviewItems.length, next_cursor: next, items,
        read_only: true, human_approval_written: false, request_shape: "bounded_production_session_review_v2",
      } as never;
    });

    renderPage();
    expect(await screen.findByText(/已加载 50 条会话 \/ 共 51 条（还有更多）/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /加载更早会话/ }));
    await waitFor(() => expect(listProductionSessions).toHaveBeenCalledWith("project-1", expect.objectContaining({ cursor: 50 })));
    expect(await screen.findByText(/已加载 51 条会话 \/ 共 51 条（已到末页）/)).toBeTruthy();

    expect(await screen.findByText(/已加载 100 集待审证据 \/ 共 101 集（还有更多）/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /加载更多待审证据/ }));
    await waitFor(() => expect(getProductionSessionReview).toHaveBeenCalledWith("session-1", expect.objectContaining({ cursor: 100 })));
    expect(await screen.findByText(/已加载 101 集待审证据 \/ 共 101 集（已到末页）/)).toBeTruthy();
  });
});
