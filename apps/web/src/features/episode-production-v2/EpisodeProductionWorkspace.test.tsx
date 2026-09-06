import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError } from "../../generated/api";
import { EpisodeProductionWorkspace } from "./EpisodeProductionWorkspace";

const api = vi.hoisted(() => ({ overview: vi.fn(), shots: vi.fn(), replan: vi.fn(), requestReplan: vi.fn(), apply: vi.fn(), ready: vi.fn(), prepare: vi.fn(), start: vi.fn(), transition: vi.fn(), syncIdentityPacks: vi.fn() }));
vi.mock("../../generated/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../generated/api")>(),
  getEpisodeProductionOverviewV2: api.overview,
  listEpisodeProductionShotsV2: api.shots,
  getEpisodeProductionReplanV2: api.replan,
  requestEpisodeProductionReplanV2: api.requestReplan,
  applyEpisodeProductionReplanV2: api.apply,
  markEpisodeProductionShotsReadyV2: api.ready,
  prepareEpisodeProductionV2: api.prepare,
  startEpisodeProductionRunV2: api.start,
  transitionEpisodeProductionRunV2: api.transition,
}));
vi.mock("../events/useProjectEventInvalidation", () => ({ useProjectEventInvalidation: () => undefined }));
vi.mock("../episode-plan-v2/AssetProposalReviewPanel", () => ({ AssetProposalReviewPanel: () => <section aria-label="资产身份建议处理">资产身份建议审核面板</section> }));
vi.mock("../asset-bible-v2/identityPackClient", () => ({ syncEpisodeCharacterPacks: api.syncIdentityPacks }));

const blockedShot = {
  shot_id: "shot-1", shot_code: "S001", order_key: "1", overall_state: "BLOCKED" as const, next_action: "OPEN_SHOT_STUDIO",
  stages: [
    { stage_code: "SHOT_PLANNING" as const, state: "BLOCKED" as const, reason_code: "SHOT_INTENT_INCOMPLETE", active_job_id: null, allowed_actions: [] },
    { stage_code: "SHOT_IMAGE" as const, state: "EMPTY" as const, reason_code: "NO_CANDIDATES", active_job_id: null, allowed_actions: [] },
    { stage_code: "VIDEO" as const, state: "EMPTY" as const, reason_code: "NO_CANDIDATES", active_job_id: null, allowed_actions: [] },
    { stage_code: "AUDIO_SUBTITLE" as const, state: "EMPTY" as const, reason_code: "NO_DIALOGUE_LINES", active_job_id: null, allowed_actions: [] },
    { stage_code: "COMPOSE_QC" as const, state: "EMPTY" as const, reason_code: "WORKING_VIDEO_REQUIRED", active_job_id: null, allowed_actions: [] },
  ],
  material_slots: [], freshness_edges: [],
  blockers: [{ code: "SHOT_INTENT_INCOMPLETE", message: "镜头意图尚未达到可生产状态。", owner_route: "SHOT_STUDIO" as const, repair_action: "OPEN_DESIGN" }],
};
const overview = (overrides = {}) => ({ overview: {
  episode_id: "e1", project_id: "p1", episode_revision: 7, episode_code: "EP01", episode_title: "第一集",
  episode_summary: "主角在废墟醒来并寻找出口。", key_characters: ["林默"], key_scenes: ["废墟实验室"],
  shot_count: 1, attention_count: 1, active_job_count: 0, next_action: "RESOLVE_ATTENTION",
  state_counts: { BLOCKED: 1 }, active_run: null, allowed_actions: ["START_PRODUCTION_RUN"], ...overrides,
}, read_only: true, request_shape: "episode_production_overview_v2" });

function mount() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><EpisodeProductionWorkspace projectId="p1" episodeId="e1" /></MemoryRouter></QueryClientProvider>);
}

describe("EpisodeProductionWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.overview.mockResolvedValue(overview());
    api.shots.mockResolvedValue({ items: [blockedShot], cursor: 0, limit: 100, total: 1, next_cursor: null, filters: [], read_only: true, request_shape: "bounded_episode_production_shots_v2" });
    api.start.mockResolvedValue({ run: { id: "run-1", episode_id: "e1", project_id: "p1", status: "RUNNING", revision: 1, updated_at: null, outcome: "STARTED", affected_job_count: 0, idempotent_replay: false } });
    api.prepare.mockResolvedValue({ preparation: { status: "QUEUED", episode_id: "e1", shot_count: 0, job_id: "job-1" } });
    api.transition.mockResolvedValue({ run: { id: "run-1", episode_id: "e1", project_id: "p1", status: "PAUSED_HITL", revision: 4, updated_at: null, outcome: "PAUSED", affected_job_count: 0, idempotent_replay: false } });
    api.replan.mockResolvedValue({ replan: { status: "NOT_READY", episode_id: "e1", job: null } });
    api.requestReplan.mockResolvedValue({ replan: { status: "QUEUED", episode_id: "e1", job_id: "job-replan", idempotent_replay: false, target_duration_ms: 120000 } });
    api.apply.mockResolvedValue({ apply: { status: "APPLIED", episode_id: "e1" } });
    api.ready.mockResolvedValue({ ready: { status: "READY", episode_id: "e1", project_id: "p1", profile_version_id: "profile-1", episode_revision: 8, ready_shot_ids: ["shot-1"], ready_shot_count: 1, failed_shots: [], idempotent_replay: false } });
    api.syncIdentityPacks.mockResolvedValue({ sync: { episode_id: "e1", project_id: "p1", updated_binding_count: 2, character_count: 1 } });
  });

  it("shows one staged episode view and only attention-level shot detail", async () => {
    mount();
    expect(await screen.findByRole("heading", { name: "EP01 · 第一集" })).toBeTruthy();
    expect(screen.getByText("主角在废墟醒来并寻找出口。")).toBeTruthy();
    expect(screen.getByText("林默")).toBeTruthy();
    expect(screen.getByText("方案与设定")).toBeTruthy();
    expect(screen.getByRole("region", { name: "本集待确认项" })).toBeTruthy();
    expect(screen.getByText("一次确认本集 1 个分镜")).toBeTruthy();
    const readyButton = screen.getByRole("button", { name: "确认本集分镜并就绪" });
    expect(readyButton.hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: /我已审核当前本集分镜方案/ }));
    fireEvent.click(readyButton);
    await waitFor(() => expect(api.ready).toHaveBeenCalledWith("e1", {
      expected_episode_revision: 7,
      idempotency_key: expect.any(String),
    }));
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(api.shots).toHaveBeenCalledWith("e1", { cursor: 0, limit: 100 });
  });

  it("starts the automatic path with one low-distraction checkpoint option", async () => {
    const readyForStart = { ...blockedShot, overall_state: "EMPTY" as const, blockers: [], stages: blockedShot.stages.map((stage) => stage.stage_code === "SHOT_PLANNING" ? { ...stage, state: "READY" as const } : stage) };
    api.overview.mockResolvedValue(overview({ attention_count: 0, state_counts: { EMPTY: 1 } }));
    api.shots.mockResolvedValue({ items: [readyForStart], cursor: 0, limit: 100, total: 1, next_cursor: null, filters: [], read_only: true, request_shape: "bounded_episode_production_shots_v2" });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "开始本集" }));
    await waitFor(() => expect(api.start).toHaveBeenCalledWith("e1", expect.objectContaining({ production_mode: "BALANCED", tts_enabled: true, checkpoint_policy: "AUTO_CONTINUE", idempotency_key: expect.any(String) })));
  });

  it("lets the episode run regenerate stale working media without per-shot confirmation", async () => {
    const staleShot = {
      ...blockedShot,
      overall_state: "STALE" as const,
      blockers: [{ code: "WORKING_MEDIA_STALE", message: "当前工作媒体的生成依赖已经变化。", owner_route: "SHOT_STUDIO" as const, repair_action: "REGENERATE_WORKING_MEDIA" }],
      stages: blockedShot.stages.map((stage) => stage.stage_code === "SHOT_PLANNING"
        ? { ...stage, state: "READY" as const, reason_code: "SHOT_REVISION_READY" }
        : stage.stage_code === "SHOT_IMAGE"
          ? { ...stage, state: "STALE" as const, reason_code: "WORKING_MEDIA_DEPENDENCY_CHANGED" }
          : stage),
    };
    api.overview.mockResolvedValue(overview({ state_counts: { STALE: 1 } }));
    api.shots.mockResolvedValue({ items: [staleShot], cursor: 0, limit: 100, total: 1, next_cursor: null, filters: [], read_only: true, request_shape: "bounded_episode_production_shots_v2" });
    mount();
    expect(await screen.findByText("开始后自动更新")).toBeTruthy();
    expect(screen.getByText("随本集重生成")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "开始本集" }));
    await waitFor(() => expect(api.start).toHaveBeenCalledWith("e1", expect.objectContaining({ production_mode: "BALANCED", tts_enabled: true, checkpoint_policy: "AUTO_CONTINUE" })));
  });

  it("allows a new run for unapproved keyframes after the old run ends without approving old candidates", async () => {
    const unapproved = { ...blockedShot, overall_state: "NEEDS_REVIEW", blockers: [], stages: blockedShot.stages.map((stage) =>
      stage.stage_code === "SHOT_PLANNING" ? { ...stage, state: "READY" } : stage.stage_code === "SHOT_IMAGE" ? { ...stage, state: "NEEDS_REVIEW", reason_code: "CANDIDATE_REVIEW_REQUIRED" } : stage) };
    api.overview.mockResolvedValue(overview({ active_run: null, state_counts: { NEEDS_REVIEW: 1 } }));
    api.shots.mockResolvedValue({ items: [unapproved] });
    api.start.mockImplementation(() => new Promise(() => {}));
    mount();
    const button = await screen.findByRole("button", { name: "重新生成本集关键帧" });
    expect(screen.getByText(/旧候选与审核记录保留/)).toBeTruthy();
    fireEvent.click(button);
    await waitFor(() => expect(api.start).toHaveBeenCalledTimes(1));
    expect((screen.getByRole("button", { name: "正在检查…" }) as HTMLButtonElement).disabled).toBe(true);
    expect(api.transition).not.toHaveBeenCalled();
    expect(api.ready).not.toHaveBeenCalled();
  });

  it("prepares a zero-shot episode directly instead of sending the user back to story", async () => {
    api.overview
      .mockResolvedValueOnce(overview({ shot_count: 0, attention_count: 0, active_job_count: 0, state_counts: {} }))
      .mockResolvedValue(overview({ shot_count: 0, attention_count: 0, active_job_count: 1, state_counts: {} }));
    api.shots.mockResolvedValue({ items: [], cursor: 0, limit: 100, total: 0, next_cursor: null, filters: [], read_only: true, request_shape: "bounded_episode_production_shots_v2" });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "生成本集方案" }));
    await waitFor(() => expect(api.prepare).toHaveBeenCalledWith("e1", { idempotency_key: expect.any(String) }));
    expect(await screen.findByText("Agent 正在根据本集原文生成方案，完成后会自动应用到本集。")).toBeTruthy();
    expect(await screen.findByRole("link", { name: "正在生成本集方案" })).toBeTruthy();
    expect(api.prepare).toHaveBeenCalledTimes(1);
  });

  it("retains a planning failure on load and gives each subsequent attempt a fresh key", async () => {
    const failed = (id: string) => overview({ shot_count: 0, planning_job: {
      id, state: "FAILED", error_code: "LOCAL_LLM_OUTPUT_TRUNCATED", error_message: "模型输出达到长度上限",
    } });
    api.overview.mockResolvedValue(failed("old-job"));
    api.shots.mockResolvedValue({ items: [], total: 0 });
    mount();
    expect(await screen.findByText("本集方案生成失败")).toBeTruthy();
    expect(screen.queryByText(/当前无异常/)).toBeNull();
    expect(screen.queryByText("当前没有需要处理的事项")).toBeNull();
    expect(screen.getByRole("link", { name: "查看本次失败详情" }).getAttribute("href")).toContain("job=old-job");
    api.overview.mockResolvedValue(failed("new-job"));
    fireEvent.click(screen.getByRole("button", { name: "重新生成本集方案" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "查看本次失败详情" }).getAttribute("href")).toContain("job=new-job"));
    expect(screen.queryByText(/Agent 正在根据本集原文生成方案/)).toBeNull();
    api.overview.mockResolvedValue(overview({ shot_count: 0, active_job_count: 1,
      planning_job: { id: "third-job", state: "QUEUED" } }));
    fireEvent.click(screen.getByRole("button", { name: "重新生成本集方案" }));
    expect(await screen.findByRole("link", { name: "正在生成本集方案" })).toBeTruthy();
    expect(screen.queryByText("本集方案生成失败")).toBeNull();
    expect(api.prepare.mock.calls[0][1].idempotency_key).not.toBe(api.prepare.mock.calls[1][1].idempotency_key);
    api.overview.mockResolvedValue(overview({ attention_count: 0, planning_job: { id: "third-job", state: "SUCCEEDED" } }));
    fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    expect(await screen.findByRole("button", { name: "开始本集" })).toBeTruthy();
    expect(screen.queryByText("本集方案生成失败")).toBeNull();
  });

  it("uses the active run revision when pausing", async () => {
    api.overview.mockResolvedValue(overview({ active_run: { id: "run-1", status: "RUNNING", revision: 3, updated_at: null }, active_job_count: 1 }));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "暂停" }));
    await waitFor(() => expect(api.transition).toHaveBeenCalledWith("run-1", "pause", expect.objectContaining({ expected_revision: 3, reason: "CREATOR_PAUSE" })));
  });

  it("offers a confirmed cancel action for an obsolete paused run", async () => {
    api.overview.mockResolvedValue(overview({ active_run: { id: "run-1", status: "PAUSED_HITL", revision: 4, updated_at: null }, active_job_count: 1 }));
    mount();

    fireEvent.click(await screen.findByRole("button", { name: "取消本次制作" }));
    expect(screen.getByRole("dialog", { name: "取消本次制作？" })).toBeTruthy();
    expect(screen.getByText(/已生成的历史素材、审核记录和失败证据都会保留/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "确认取消本次制作" }));
    await waitFor(() => expect(api.transition).toHaveBeenCalledWith("run-1", "cancel", expect.objectContaining({ expected_revision: 4, idempotency_key: expect.any(String) })));
  });

  it("explains a machine pause with persisted code, detail, shot count, and project jobs link", async () => {
    api.overview.mockResolvedValue(overview({ active_run: {
      id: "run-machine", status: "PAUSED_HITL", revision: 8, updated_at: null,
      pending_gate: { reason: "DECLARATIVE_CONDITION", next_action: "VIDEO_GENERATION" },
      machine_context: {
        status: "NEEDS_HITL",
        machine_check: {
          status: "NEEDS_HITL",
          code: "APPROVED_KEYFRAME_REQUIRED",
          detail: "当前镜头缺少未过期的人工批准关键帧。",
          missing_shots: [{ shot_id: "shot-1", shot_code: "S001" }, { shot_id: "shot-2", shot_code: "S002" }],
        },
      },
    }, active_job_count: 1 }));
    mount();

    expect(await screen.findByRole("button", { name: "问题处理后继续" })).toBeTruthy();
    expect(screen.getByText("APPROVED_KEYFRAME_REQUIRED")).toBeTruthy();
    expect(screen.getByText(/当前镜头缺少未过期的人工批准关键帧/)).toBeTruthy();
    expect(screen.getByText("受影响镜头")).toBeTruthy();
    expect(screen.getByText("2 镜")).toBeTruthy();
    expect(screen.getByRole("link", { name: "打开项目任务中心" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "检查并审核本集关键帧" }).getAttribute("href")).toContain("/studio");
    fireEvent.click(screen.getByRole("button", { name: "问题处理后继续" }));
    await waitFor(() => expect(api.transition).toHaveBeenCalledWith("run-machine", "resume", expect.objectContaining({ expected_revision: 8, note: "创作者确认后继续" })));
  });

  it("keeps a configured checkpoint distinct from a machine pause", async () => {
    api.overview.mockResolvedValue(overview({ active_run: {
      id: "run-checkpoint", status: "PAUSED_HITL", revision: 9, updated_at: null,
      pending_gate: { reason: "CONFIGURED_CREATOR_CHECKPOINT", checkpoint_policy: "BEFORE_VIDEO", next_action: "VIDEO_GENERATION" },
      machine_context: {},
    } }));
    mount();

    expect(await screen.findByRole("button", { name: "确认并继续" })).toBeTruthy();
    expect(screen.getByText("这是项目配置的人工确认点")).toBeTruthy();
    expect(screen.queryByText("机器检查 code")).toBeNull();
  });

  it("keeps the cancel action visible while a run is still running", async () => {
    api.overview.mockResolvedValue(overview({ active_run: { id: "run-1", status: "RUNNING", revision: 5, updated_at: null }, active_job_count: 1 }));
    mount();
    expect(await screen.findByRole("button", { name: "取消本次制作" })).toBeTruthy();
  });

  it("shows resolved episode values and keeps an invalid replan behind an explicit disabled gate", async () => {
    api.overview.mockResolvedValue(overview({
      target_duration_ms: 120000,
      planned_duration_ms: 60000,
      replan_required: true,
      replan_reasons: [{ code: "TARGET_DURATION_MISMATCH", message: "本集目标与当前分镜时长不一致。" }],
      production_plan_version_no: 3,
      resolved_presentation: { width: 480, height: 854, fps: 24 },
    }));
    api.replan.mockResolvedValue({ replan: {
      status: "DRAFT_READY", episode_id: "e1", expected_episode_revision: 2, plan_hash: "a".repeat(64),
      target_duration_ms: 120000, planned_duration_ms: 120000, summary: { ADD: 1 }, diff: [], valid: false,
      issues: [{ code: "FROZEN_SHOT_WOULD_BE_REMOVED", message: "冻结镜头不能被移除。", shot_id: "shot-1" }],
    } });
    mount();
    expect(await screen.findByText("120 秒")).toBeTruthy();
    expect(screen.getByText("60 秒")).toBeTruthy();
    expect(screen.getByText("480×854")).toBeTruthy();
    expect(await screen.findByText("这份草稿暂不能应用")).toBeTruthy();
    const applyButton = await screen.findByRole("button", { name: "确认应用并继续生成" });
    expect(applyButton.hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: /我已审核本集全部差异/ }));
    expect(applyButton.hasAttribute("disabled")).toBe(true);
    expect(api.apply).not.toHaveBeenCalled();
  });

  it("requires confirmation before applying a valid replan with its revision and hash", async () => {
    api.overview.mockResolvedValue(overview({ target_duration_ms: 120000, planned_duration_ms: 60000, replan_required: true, production_plan_version_no: 3, resolved_presentation: { width: 480, height: 854, fps: 24 } }));
    api.replan.mockResolvedValue({ replan: {
      status: "DRAFT_READY", episode_id: "e1", expected_episode_revision: 2, plan_hash: "b".repeat(64),
      target_duration_ms: 120000, planned_duration_ms: 120000, summary: { MODIFY: 2 }, diff: [{ action: "MODIFY", shot_id: "shot-1", after: { target_duration_ms: 60000 } }], valid: true,
    } });
    mount();
    const applyButton = await screen.findByRole("button", { name: "确认应用并继续生成" });
    expect(applyButton.hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: /我已审核本集全部差异/ }));
    expect(applyButton.hasAttribute("disabled")).toBe(false);
    fireEvent.click(applyButton);
    await waitFor(() => expect(api.apply).toHaveBeenCalledWith("e1", {
      expected_episode_revision: 2,
      expected_plan_hash: "b".repeat(64),
      idempotency_key: expect.any(String),
    }));
  });

  it("rejects a stale-duration draft and lets the user generate a replacement", async () => {
    api.overview.mockResolvedValue(overview({
      target_duration_ms: 120000,
      planned_duration_ms: 114000,
      replan_required: true,
      replan_reasons: [{ code: "TARGET_DURATION_MISMATCH", message: "本集目标与当前分镜时长不一致。" }],
    }));
    api.replan.mockResolvedValue({ replan: {
      status: "DRAFT_READY", episode_id: "e1", draft_id: "draft-old", expected_episode_revision: 2,
      plan_hash: "c".repeat(64), target_duration_ms: 120000, planned_duration_ms: 114000,
      summary: { MODIFY: 1 }, diff: [], valid: true,
    } });

    mount();

    expect(await screen.findByText("这份重规划草稿已不符合当前目标")).toBeTruthy();
    const applyButton = screen.getByRole("button", { name: "确认应用并继续生成" });
    fireEvent.click(screen.getByRole("checkbox", { name: /我已审核本集全部差异/ }));
    expect(applyButton.hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getAllByRole("button", { name: "重新生成重规划草稿" })[0]);
    await waitFor(() => expect(api.requestReplan).toHaveBeenCalledWith("e1", { idempotency_key: expect.any(String) }));
    expect(api.apply).not.toHaveBeenCalled();
  });

  it("shows the real asset codes and ids returned by a blocked production preflight", async () => {
    const readyForStart = { ...blockedShot, overall_state: "EMPTY" as const, blockers: [], stages: blockedShot.stages.map((stage) => stage.stage_code === "SHOT_PLANNING" ? { ...stage, state: "READY" as const } : stage) };
    api.overview.mockResolvedValue(overview({ attention_count: 0, state_counts: { EMPTY: 1 } }));
    api.shots.mockResolvedValue({ items: [readyForStart], cursor: 0, limit: 100, total: 1, next_cursor: null, filters: [], read_only: true, request_shape: "bounded_episode_production_shots_v2" });
    api.start.mockRejectedValue(new ApiRequestError(
      "整集生产 preflight 未通过", 409, "EPISODE_PRODUCTION_PREFLIGHT_BLOCKED", null, false, null,
      {
        blocker_codes: ["ASSET_COMPLETION_REQUIRED"],
        preflight: { blockers: [{ code: "ASSET_COMPLETION_REQUIRED", evidence: {
          code: "ASSET_IDENTITY_DECISION_REQUIRED",
          detail: "仍有资产身份建议需要人工创建、合并或拒绝",
          pending_proposal_ids: ["proposal-12345678"],
          invalid_shot_bindings: [{ asset_code: "CHAR_MOTHER" }],
          missing_asset_ids: ["12345678-abcd-efgh-ijkl-123456789000"],
        } }] },
      },
    ));

    mount();
    fireEvent.click(await screen.findByRole("button", { name: "开始本集" }));

    expect(await screen.findByText(/镜头绑定未更新：CHAR_MOTHER/)).toBeTruthy();
    expect(screen.getByText(/资产前置检查：ASSET_IDENTITY_DECISION_REQUIRED/)).toBeTruthy();
    expect(screen.getByText(/缺少完整身份包的资产：12345678/)).toBeTruthy();
    expect(screen.getByRole("region", { name: "资产身份建议处理" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "同步并重新检查" }));
    await waitFor(() => expect(api.syncIdentityPacks).toHaveBeenCalledWith("e1"));
    expect(await screen.findByText("已将 1 个角色的当前批准造型同步到本集，更新 2 条镜头引用。")).toBeTruthy();
  });

  it("explains that missing project character references must be approved before episode sync", async () => {
    const readyForStart = { ...blockedShot, overall_state: "EMPTY" as const, blockers: [], stages: blockedShot.stages.map((stage) => stage.stage_code === "SHOT_PLANNING" ? { ...stage, state: "READY" as const } : stage) };
    api.overview.mockResolvedValue(overview({ attention_count: 0, state_counts: { EMPTY: 1 } }));
    api.shots.mockResolvedValue({ items: [readyForStart], cursor: 0, limit: 100, total: 1, next_cursor: null, filters: [], read_only: true, request_shape: "bounded_episode_production_shots_v2" });
    api.start.mockRejectedValue(new ApiRequestError(
      "整集生产 preflight 未通过", 409, "EPISODE_PRODUCTION_PREFLIGHT_BLOCKED", null, false, null,
      { blocker_codes: ["ASSET_COMPLETION_REQUIRED"], preflight: { blockers: [{ code: "ASSET_COMPLETION_REQUIRED", evidence: { invalid_shot_bindings: [{ asset_code: "CHAR_MERCHANT" }] } }] } },
    ));
    api.syncIdentityPacks.mockRejectedValue(new ApiRequestError(
      "本集角色身份包尚不能统一应用", 409, "EPISODE_IDENTITY_PACK_SYNC_BLOCKED", "request-1", false, null,
      { missing_assets: [{ asset_id: "asset-1", asset_code: "CHAR_MERCHANT", asset_name: "周掌柜" }], ambiguous_assets: [{ asset_id: "asset-2", asset_code: "CHAR_HERO", asset_name: "沈砚" }] },
    ));

    mount();
    fireEvent.click(await screen.findByRole("button", { name: "开始本集" }));
    fireEvent.click(await screen.findByRole("button", { name: "同步并重新检查" }));

    expect(await screen.findByText(/尚未批准项目角色参考：周掌柜/)).toBeTruthy();
    expect(screen.getByText(/存在多个可用造型，需逐镜选择：沈砚/)).toBeTruthy();
    expect(screen.getByText(/请先在项目资产中完成角色参考，再回到本集同步/)).toBeTruthy();
  });
});
