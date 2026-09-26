/**
 * 第 2 步 behaviour the design fixes as non-negotiable (§B3, §B9, §F3):
 *
 * * every object shows TWO independent numbers — 出场 N 镜 from
 *   `appearance_beat_count` and the reference state from
 *   `reference_binding_status`; a character in four beats with no reference
 *   reads both “出场 4 镜” and “未设参考图”;
 * * clicking a candidate only previews it; 采用 writes the adoption without a
 *   lock, 采用并锁定 sets the explicit human lock, 解锁 keeps the current image;
 * * the batch size is stated before generating, and an unknown budget reads
 *   待检查 instead of 0;
 * * a failed candidate read keeps the known candidates and says so;
 * * the bottom bar's one primary action is 生成缺失参考 while something is
 *   missing, and 下一步：配音 once nothing is.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  adoptExplainerEntityReference,
  getExplainerReferenceDesign,
  listCapabilityOptions,
  patchExplainerVisualPreferences,
  planExplainerReferenceGeneration,
  submitExplainerReferenceGeneration,
  unlockExplainerEntityReference,
} from "../../generated/api";
import { draftRegistry } from "../drafts/draftRegistry";
import { ExplainerActionBarProvider, ExplainerStepActionBar } from "./ExplainerStepActionBar";
import { explainerStepStatuses } from "./ExplainerSteps";
import {
  useExplainerAssets,
  useExplainerEntityCandidates,
  useExplainerOverview,
  useExplainerReadiness,
} from "./useExplainerQueries";
import { ExplainerAssetsPage } from "./AssetsPage";

vi.mock("../../generated/api", () => ({
  adoptExplainerEntityReference: vi.fn(),
  unlockExplainerEntityReference: vi.fn(),
  planExplainerReferenceGeneration: vi.fn(),
  submitExplainerReferenceGeneration: vi.fn(),
  patchExplainerVisualPreferences: vi.fn(),
  preflightExplainerPlan: vi.fn(),
  startExplainerRun: vi.fn(),
  listCapabilityOptions: vi.fn(),
  getExplainerReferenceDesign: vi.fn(),
}));

vi.mock("./useExplainerQueries", () => ({
  useExplainerAssets: vi.fn(),
  useExplainerOverview: vi.fn(),
  useExplainerReadiness: vi.fn(),
  useExplainerEntityCandidates: vi.fn(),
}));

/** The picker stub proves the page only turns a media choice into a candidate. */
vi.mock("../media-picker/MediaPicker", () => ({
  MediaPicker: ({ onChange }: { onChange: (mediaVersionId: string, item?: { stage: string; version_no: number }) => void }) => (
    <button type="button" onClick={() => onChange("mv-9", { stage: "KEYFRAME", version_no: 2 })}>
      选择示例媒体
    </button>
  ),
}));

function entityFixture(overrides: Record<string, unknown> = {}) {
  return {
    entity_id: "ent-1",
    code: "E001",
    name: "林默",
    entity_type: "REAL_PERSON",
    asset_kind: "CHARACTER",
    fictional: false,
    story_asset_id: "sa-1",
    appearance_beat_count: 4,
    reference_binding_status: "NO_REFERENCE",
    identity_input_status: "HERO_REQUIRED",
    reference: null,
    state_revisions: [{ id: "state-1", revision_no: 1 }],
    missing_reason: null,
    candidate_counts: { REFERENCE: 2, KEYFRAME: 0, VISUAL: 0 },
    revision: 5,
    ...overrides,
  };
}

function sceneFixture(overrides: Record<string, unknown> = {}) {
  return {
    entity_id: "ent-2",
    code: "E002",
    name: "灯塔控制室",
    entity_type: "LOCATION",
    asset_kind: "SCENE",
    fictional: false,
    story_asset_id: "sa-2",
    appearance_beat_count: 2,
    reference_binding_status: "ADOPTED_REFERENCE",
    identity_input_status: null,
    reference: { id: "ref-2", media_version_id: "mv-20" },
    state_revisions: [],
    missing_reason: null,
    candidate_counts: { REFERENCE: 1, KEYFRAME: 0, VISUAL: 0 },
    revision: 2,
    ...overrides,
  };
}

function assetsView(entities: unknown[]) {
  return {
    video_id: "video-1",
    project_id: "project-1",
    entities,
    entity_counts: { CHARACTER: 1, SCENE: 1 },
    missing_reference_count: 1,
    channel_profile_version: { id: "cpv-1", title: "项目默认风格", version_no: 2, status: "PUBLISHED" },
    channel_profile_is_frozen_snapshot: true,
    three_view_is_display_only: true,
    visual_preferences: null,
    resolved_style: null,
    unresolved_constraints: [],
  };
}

function candidate(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    candidate_kind: "CREATIVE",
    purpose: "REFERENCE",
    owner_kind: "ENTITY",
    owner_id: "ent-1",
    beat_id: null,
    entity_id: "ent-1",
    edition_id: null,
    variant_no: Number(id.replace(/\D/g, "")) || 1,
    media_kind: "IMAGE",
    media_version_id: `mv-${id}`,
    media_sha256: null,
    thumbnail_url: `/api/v1/media-versions/mv-${id}/thumbnail?size=small`,
    preview_url: null,
    playback_url: null,
    duration_ms: null,
    width: 1024,
    height: 1024,
    status: "READY",
    render_type_planned: "I2V",
    render_type_actual: null,
    fallback_reason: null,
    selected: false,
    locked: false,
    stale: false,
    adopted: false,
    job_id: null,
    job_state: null,
    seed: 42,
    parent_candidate_id: null,
    prompt: "prompt",
    negative_prompt: null,
    reference_media_version_ids: [],
    short_label: `候选 ${id}`,
    error_code: null,
    error_message: null,
    retryable: false,
    created_at: null,
    ...overrides,
  };
}

function candidatesPage(candidates: unknown[]) {
  return {
    project_id: "project-1",
    owner_kind: "ENTITY",
    owner_id: "ent-1",
    purpose: "REFERENCE",
    edition_id: null,
    candidates,
    counts: { REFERENCE: candidates.length },
    active_selection: null,
    empty_state: candidates.length ? null : "NO_CANDIDATES_YET",
    candidates_newest_first: true,
    read_error_keeps_known_selection: true,
  };
}

function overviewFact() {
  return {
    project_id: "project-1",
    video: { id: "video-1", title: "测试作品", revision: 7, automation_mode: "AUTO_WITH_EXCEPTIONS", editions: [] },
    editions: [],
    beat_count: 4,
    render_type_counts: {},
    latest_run: null,
    open_issues: [],
    open_issue_count: 0,
    blocking_issue_count: 0,
    active_decisions: [],
    authority_labels: { machine: "", human: "", publication: "" },
    capability_snapshot: { probed: true },
  };
}

function readinessFact() {
  return {
    video_id: "video-1",
    project_id: "project-1",
    revision: 7,
    steps: [{ step_code: "IDENTITY_ASSETS", page: "assets", label: "人物与风格", status: "NEEDS_SELECTION", produced: 0, required: 2, blocked_reason: null, next_action: null, step_binding_id: null }],
    first_actionable_page: "assets",
    blocking_step_code: null,
    blocking_reason: null,
    // The generated client still declares a `visual_strategy` field; it is server
    // data this page neither shows nor writes any more.
    one_click_route: { mode: "ONE_CLICK", visual_strategy: "ALL_I2V", script_policy: "PRESERVE_ORIGINAL", summary: "s" },
  };
}

function Harness() {
  return (
    <ExplainerActionBarProvider>
      <div className="explainer-workspace">
        <ExplainerAssetsPage />
      </div>
      <ExplainerStepActionBar projectId="project-1" activePage="assets" statuses={explainerStepStatuses(null)} />
    </ExplainerActionBarProvider>
  );
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/explainers/project-1/assets"]}>
        <Routes>
          <Route path="/explainers/:projectId/assets" element={<Harness />} />
          <Route path="/explainers/:projectId/audio" element={<p>配音页面</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockAssets(entities: unknown[], extra: Record<string, unknown> = {}) {
  vi.mocked(useExplainerAssets).mockReturnValue({
    data: assetsView(entities),
    isPending: false,
    isError: false,
    refetch: vi.fn(),
    ...extra,
  } as never);
}

function mockCandidates(page: unknown, extra: Record<string, unknown> = {}) {
  vi.mocked(useExplainerEntityCandidates).mockReturnValue({
    data: page,
    isPending: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
    ...extra,
  } as never);
}

describe("ExplainerAssetsPage step 2 (§B3, §B9)", () => {
  beforeEach(() => {
    draftRegistry.clear();
    vi.clearAllMocks();
    vi.mocked(listCapabilityOptions).mockResolvedValue({
      capability: "IMAGE_CONCEPT",
      scope: { project_id: "project-1", episode_id: null, shot_id: null },
      selection: { mode: "AUTO", source: "NONE", profile_version_id: null, ready: false, option: null, blockers: [] },
      options: [],
      configured_runtime: null,
      summary: { total_count: 0, selectable_count: 0, blocked_count: 0 },
      repair_href: "/system/capabilities",
      read_only: true,
      runtime_contacted: false,
      network_contacted: false,
      mutated: false,
    } as never);
    vi.mocked(useExplainerOverview).mockReturnValue({ data: overviewFact(), isPending: false, isError: false } as never);
    vi.mocked(patchExplainerVisualPreferences).mockResolvedValue({
      video_id: "video-1",
      revision: 8,
      visual_preferences: {},
    } as never);
    vi.mocked(useExplainerReadiness).mockReturnValue({ data: readinessFact(), isPending: false, isError: false } as never);
    vi.mocked(adoptExplainerEntityReference).mockResolvedValue({ reference_id: "ref-9", locked: false, impact: { affected_beat_count: 4 } } as never);
    vi.mocked(unlockExplainerEntityReference).mockResolvedValue({ locked: false, current_media_kept: true } as never);
    vi.mocked(planExplainerReferenceGeneration).mockImplementation(async (_projectId: string, _entityId: string, payload: { candidate_count?: number }) => ({
      status: "EXECUTABLE",
      owner_kind: "ENTITY",
      owner_id: "ent-1",
      purpose: "REFERENCE",
      mode: "TEXT_TO_IMAGE",
      candidate_count: payload.candidate_count ?? 1,
      plan_hash: "h".repeat(64),
      candidate_seeds: [7],
      execution_profile_version_id: "profile-1",
      profile_title: "本机图像",
      expected_resolution_hash: null,
      media_kind: "IMAGE",
      render_type_actual: null,
      render_type_planned: "I2V",
      prompt: "p",
      negative_prompt: null,
      reference_capacity: { max_image_references: 3, resolved_image_references: 0 },
      resolved_references: [],
      frozen_inputs: {},
      blockers: [],
      // A null budget is exactly the "unknown balance" case that must read 待检查.
      budget: null,
      planned_duration_ms: null,
      allowed_durations_ms: null,
    }) as never);
    vi.mocked(submitExplainerReferenceGeneration).mockResolvedValue({
      operation_id: "op-1",
      status: "ACCEPTED",
      requested_count: 1,
      accepted_count: 1,
      items: [],
      idempotent_replay: false,
    } as never);
  });

  afterEach(() => {
    cleanup();
    draftRegistry.clear();
  });

  it("shows 出场 N 镜 and the reference state as two separate numbers", async () => {
    mockAssets([entityFixture(), sceneFixture()]);
    mockCandidates(candidatesPage([candidate("cand-1")]));
    mount();

    expect(await screen.findByText("出场 4 镜")).toBeTruthy();
    expect(screen.getAllByText("未设参考图").length).toBeGreaterThan(0);
    // The scene's own numbers are independent of the character's.
    fireEvent.click(screen.getByRole("button", { name: "场景 1" }));
    expect(await screen.findByText("出场 2 镜")).toBeTruthy();
    expect(screen.getAllByText("已采用参考图").length).toBeGreaterThan(0);
  });

  it("keeps a character with no reference from looking unused", async () => {
    mockAssets([entityFixture({ appearance_beat_count: 4, reference_binding_status: "NO_REFERENCE" })]);
    mockCandidates(candidatesPage([]));
    mount();

    const card = (await screen.findAllByText("林默"))[0].closest("button");
    expect(card).toBeTruthy();
    expect(card?.textContent).toContain("出场 4 镜");
    expect(card?.textContent).toContain("未设参考图");
  });

  it("previews on click and only adopts on an explicit 采用 / 采用并锁定", async () => {
    mockAssets([entityFixture()]);
    mockCandidates(candidatesPage([candidate("cand-1"), candidate("cand-2")]));
    mount();

    const stage = await screen.findByRole("group", { name: "当前参考图操作" });
    const adoptButton = within(stage).getByRole("button", { name: "采用" });
    expect(adoptButton).toBeDisabled();

    // Clicking a candidate only previews it — nothing is written.
    fireEvent.click(await screen.findByRole("button", { name: "预览候选 cand-1" }));
    expect(adoptExplainerEntityReference).not.toHaveBeenCalled();
    expect(within(stage).getByText(/正在预览候选（尚未采用）/)).toBeTruthy();

    fireEvent.click(within(stage).getByRole("button", { name: "采用" }));
    await waitFor(() => expect(adoptExplainerEntityReference).toHaveBeenCalledTimes(1));
    expect(vi.mocked(adoptExplainerEntityReference).mock.calls[0][2]).toMatchObject({
      candidate_id: "cand-1",
      lock: false,
      expected_entity_revision: 5,
    });

    fireEvent.click(await screen.findByRole("button", { name: "预览候选 cand-1" }));
    fireEvent.click(within(stage).getByRole("button", { name: "采用并锁定" }));
    await waitFor(() => expect(adoptExplainerEntityReference).toHaveBeenCalledTimes(2));
    expect(vi.mocked(adoptExplainerEntityReference).mock.calls[1][2]).toMatchObject({ lock: true });
  });

  it("unlocks the replacement policy without removing the current image", async () => {
    mockAssets([entityFixture({ reference: { id: "ref-1", media_version_id: "mv-7" }, reference_binding_status: "ADOPTED_REFERENCE" })]);
    mockCandidates(candidatesPage([candidate("cand-1")]));
    mount();

    const stage = await screen.findByRole("group", { name: "当前参考图操作" });
    expect(within(stage).getByRole("img", { name: /当前采用的参考图/ })).toBeTruthy();
    fireEvent.click(within(stage).getByRole("button", { name: "解锁" }));
    await waitFor(() => expect(unlockExplainerEntityReference).toHaveBeenCalledTimes(1));
    expect(vi.mocked(unlockExplainerEntityReference).mock.calls[0][2]).toMatchObject({ reference_id: "ref-1" });
    // The current image is still the adopted one.
    expect(within(stage).getByRole("img", { name: /当前采用的参考图/ })).toBeTruthy();
    expect(await screen.findByText(/只改变将来的替换策略/)).toBeTruthy();
  });

  it("states the batch size before generating and reads 待检查 when the budget is unknown", async () => {
    mockAssets([entityFixture()]);
    mockCandidates(candidatesPage([candidate("cand-1")]));
    mount();

    // §B9: the key character's first batch defaults to 2 images.
    expect(await screen.findByRole("button", { name: "再生成 2 张" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "检查生成条件" }));
    await waitFor(() => expect(planExplainerReferenceGeneration).toHaveBeenCalled());
    expect(await screen.findByText(/本次将提交 2 张图片/)).toBeTruthy();
    expect(screen.getByText(/预算余量：待检查/)).toBeTruthy();
    expect(screen.queryByText(/预算余量：0/)).toBeNull();

    // A regenerate drops back to 1 image for this batch.
    fireEvent.click(screen.getByRole("button", { name: "再生成 2 张" }));
    await waitFor(() => expect(submitExplainerReferenceGeneration).toHaveBeenCalled());
    expect(vi.mocked(submitExplainerReferenceGeneration).mock.calls[0][2].candidate_count).toBe(1);
    expect(await screen.findByRole("button", { name: "再生成 1 张" })).toBeTruthy();
  });

  it("submits a verified plan without replacing the adopted image", async () => {
    mockAssets([entityFixture()]);
    mockCandidates(candidatesPage([candidate("cand-1")]));
    mount();

    fireEvent.click(await screen.findByRole("button", { name: "生成 2 张" }));
    await waitFor(() => expect(submitExplainerReferenceGeneration).toHaveBeenCalled());
    const payload = vi.mocked(submitExplainerReferenceGeneration).mock.calls[0][2];
    expect(payload.expected_plan_hash).toBe("h".repeat(64));
    expect(payload.operation_id).toBeTruthy();
    expect(await screen.findByText(/新批次会保留旧候选/)).toBeTruthy();
  });

  it("keeps the known candidates when the candidate list read fails", async () => {
    mockAssets([entityFixture()]);
    mockCandidates(candidatesPage([candidate("cand-1")]), { isError: true, error: new Error("网络中断") });
    mount();

    expect(await screen.findByText(/候选读取失败：网络中断/)).toBeTruthy();
    // Not the "还没有候选" empty state: the known candidates are still listed.
    expect(screen.getByRole("button", { name: "预览候选 cand-1" })).toBeTruthy();
    expect(screen.queryByText("还没有候选")).toBeNull();
  });

  it("keeps the last known assets and says 更新失败 when the refresh fails", async () => {
    mockAssets([entityFixture()], { isError: true, error: new Error("服务不可用") });
    mockCandidates(candidatesPage([candidate("cand-1")]));
    mount();

    expect(await screen.findByText(/更新失败/)).toBeTruthy();
    expect(screen.getAllByText("林默").length).toBeGreaterThan(0);
    expect(screen.queryByText("还没有人物与场景资产")).toBeNull();
  });

  it("filters by category without becoming a second navigation", async () => {
    mockAssets([entityFixture(), sceneFixture()]);
    mockCandidates(candidatesPage([candidate("cand-1")]));
    mount();

    expect(await screen.findByRole("button", { name: "人物 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "场景 1" })).toBeTruthy();
    expect(screen.getByText(/这是内容筛选，不是第二套制作步骤/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "场景 1" }));
    expect((await screen.findAllByText("灯塔控制室")).length).toBeGreaterThan(0);
  });

  it("turns a media-library choice into a candidate, never an adoption", async () => {
    mockAssets([entityFixture()]);
    mockCandidates(candidatesPage([]));
    mount();

    fireEvent.click(await screen.findByRole("button", { name: "上传 / 从媒体库选择" }));
    fireEvent.click(await screen.findByRole("button", { name: "选择示例媒体" }));
    expect(adoptExplainerEntityReference).not.toHaveBeenCalled();
    expect(await screen.findByText(/点击“采用”后才进入生成依赖/)).toBeTruthy();
  });

  it("offers 更多视角 as an on-demand slot list, not a forced three-view", async () => {
    mockAssets([entityFixture({ reference: { id: "ref-1", media_version_id: "mv-7" }, reference_binding_status: "ADOPTED_REFERENCE" })]);
    mockCandidates(candidatesPage([candidate("cand-1")]));
    mount();

    const stage = await screen.findByRole("group", { name: "当前参考图操作" });
    // Folded by default: no multi-view obligation is imposed on the character.
    expect(screen.queryByText(/正面（未生成）/)).toBeNull();
    fireEvent.click(within(stage).getByRole("button", { name: "更多视角" }));
    expect(await screen.findByText("主参考（已采用）")).toBeTruthy();
    expect(screen.getByText("正面（未生成）")).toBeTruthy();
  });

  it("makes 生成缺失参考 the single bottom-bar primary while something is missing", async () => {
    mockAssets([entityFixture(), sceneFixture()]);
    mockCandidates(candidatesPage([candidate("cand-1")]));
    mount();

    const primary = await screen.findByRole("button", { name: /生成缺失参考（1 项 · 1 张）/ });
    expect(primary).toBeTruthy();
    fireEvent.click(primary);
    await waitFor(() => expect(submitExplainerReferenceGeneration).toHaveBeenCalled());
    // Only the missing, unlocked object is submitted.
    expect(vi.mocked(submitExplainerReferenceGeneration).mock.calls[0][1]).toBe("ent-1");
  });

  it("offers 下一步：配音 once nothing is missing", async () => {
    mockAssets([sceneFixture()]);
    mockCandidates(candidatesPage([]));
    mount();

    const primary = await screen.findByRole("button", { name: "下一步：配音" });
    fireEvent.click(primary);
    expect(await screen.findByText("配音页面")).toBeTruthy();
  });

  it("has no 全片片段策略 selector and never sends a removed visual_strategy", async () => {
    mockAssets([entityFixture()]);
    mockCandidates(candidatesPage([candidate("cand-1")]));
    mount();

    // The product removed 静图推拉 and the whole 片段策略 choice: every picture is
    // produced by real AI 图生视频, so nothing here picks a strategy.
    expect(await screen.findByText("出场 4 镜")).toBeTruthy();
    expect(screen.queryByLabelText("全片片段策略")).toBeNull();
    expect(screen.queryByText("静图推拉")).toBeNull();
    expect(screen.queryByText(/关键镜头 AI 动态/)).toBeNull();
    expect(screen.queryByText(/全部 AI 动态/)).toBeNull();
    // The style / negative-prompt / candidate-count controls stay.
    expect(screen.getByRole("button", { name: "更换风格" })).toBeTruthy();
    expect(screen.getByLabelText(/每次候选数/)).toBeTruthy();

    // Saving the settings writes only the surviving fields.
    const draft = draftRegistry.get("explainer-asset-settings:project-1:ent-1");
    expect(draft?.entityKey).toContain("生成设置");
    await draft?.save?.(draft.version);
    await waitFor(() => expect(patchExplainerVisualPreferences).toHaveBeenCalled());
    const [, patch] = vi.mocked(patchExplainerVisualPreferences).mock.calls[0];
    const preferences = (patch as { visual_preferences: Record<string, unknown> }).visual_preferences;
    expect("visual_strategy" in preferences).toBe(false);
    expect(preferences.image_candidate_count).toBe(2);
  });

  it("shows the program-compiled setting prompt without calling a model", async () => {
    mockAssets([entityFixture()]);
    mockCandidates(candidatesPage([candidate("cand-1")]));
    vi.mocked(getExplainerReferenceDesign).mockResolvedValue({
      entity_id: "ent-1",
      reference_kind: "HERO",
      description_prompt: "周工 的人物参考图：只有一个人物，正面站立，服务身份识别，不做剧情动作。",
      negative_prompt: "额外人物、文字、水印",
      adopted_reference_count: 1,
      unresolved_constraints: [],
      model_calls_on_this_read: 0,
    } as never);
    mount();

    fireEvent.click(await screen.findByText("设定提示词（程序编译，可核对）"));
    expect(await screen.findByText(/人物参考图：只有一个人物/)).toBeTruthy();
    expect(screen.getByText("额外人物、文字、水印")).toBeTruthy();
    // The read is deterministic: it must never trigger generation or adoption.
    expect(submitExplainerReferenceGeneration).not.toHaveBeenCalled();
    expect(adoptExplainerEntityReference).not.toHaveBeenCalled();
  });
});
