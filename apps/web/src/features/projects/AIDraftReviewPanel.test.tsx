import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { applyScriptBreakdownDraft, listEpisodes, listScriptBreakdownDrafts, listSeasons, reviseScriptBreakdownDraftScene, type ScriptBreakdownDraft } from "../../generated/api";
import { AIDraftReviewPanel } from "./AIDraftReviewPanel";
import { requestScriptBreakdown } from "../story-workspace-v2/breakdownClient";

vi.mock("../../generated/api", () => ({ listScriptBreakdownDrafts: vi.fn(), listSeasons: vi.fn(), listEpisodes: vi.fn(), applyScriptBreakdownDraft: vi.fn(), reviseScriptBreakdownDraftScene: vi.fn() }));
vi.mock("../story-workspace-v2/breakdownClient", () => ({ requestScriptBreakdown: vi.fn() }));
function renderPanel() { const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); return render(<QueryClientProvider client={client}><AIDraftReviewPanel projectId="project-1" /></QueryClientProvider>); }

const seasonItems = [{ id: "season-1", code: "SEASON_001", title: "第 1 季" }];
const episodeItems = [{ id: "ep-1", code: "EPISODE_001", title: "第 1 集", production_status: "NOT_STARTED", target_duration_ms: 5_000 }];
const draftItem: ScriptBreakdownDraft = {
  id: "draft", project_id: "project-1", source_document_version_id: "version", import_session_id: "session", status: "DRAFT_READY", revision: 1,
  source_document_code: "script", source_document_title: "剧本",
  draft: { scenes: [{ scene_no: 1, title: "雾港来信", summary: "林默收到旧信", characters: ["林默"], shots: [{ shot_no: 1, visual: "雾港远景", action: "林默拆信", dialogue: "十年前？", duration_seconds: 3 }, { shot_no: 2, visual: "怀表特写", action: "指针倒转", dialogue: "", duration_seconds: 2 }] }] },
  confidence: { profile_version_id: "profile-v1", confidence: { overall: 0.8, notes: ["时间线需确认"] }, questions: ["服装？"], source_passages: [{ scene_no: 1, quote: "原文", source_start: 0, source_end: 2 }] },
  profile_version_id: "profile-v1", evidence_status: "COMPLETE", application_status: "NOT_APPLIED",
  model_draft_sha256: "a".repeat(64), effective_draft_revision_id: null, effective_draft_revision_no: 0, human_edited: false,
  automatic_apply: false, requires_human_action: true, created_at: "now",
};

describe("AIDraftReviewPanel", () => {
  beforeEach(() => {
    vi.mocked(listScriptBreakdownDrafts).mockReset();
    vi.mocked(listSeasons).mockReset();
    vi.mocked(listEpisodes).mockReset();
    vi.mocked(applyScriptBreakdownDraft).mockReset();
    vi.mocked(reviseScriptBreakdownDraftScene).mockReset();
    vi.mocked(requestScriptBreakdown).mockReset();
    vi.mocked(listSeasons).mockResolvedValue({ items: seasonItems });
    vi.mocked(listEpisodes).mockResolvedValue({ items: episodeItems });
  });
  it("labels persisted suggestions as not applied and exposes an explicit apply action", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({ automatic_apply: false, requires_human_action: true, items: [{ ...draftItem }] });
    renderPanel();
    expect(await screen.findByText(/DRAFT_READY · NOT_APPLIED/)).toBeTruthy();
    expect(screen.getByText("1 个建议场次 · 2 个建议镜头")).toBeTruthy();
    expect(screen.getByText(/证据完整 · 置信度 80%/)).toBeTruthy();
    fireEvent.click(screen.getByText(/展开完整草稿/));
    expect(screen.getByText(/场 1 · 雾港来信/)).toBeTruthy();
    expect(screen.getByText("画面：雾港远景")).toBeTruthy();
    expect(screen.getByText("服装？")).toBeTruthy();
    expect(screen.getByText("原文")).toBeTruthy();
    expect(screen.getByLabelText("选择目标集")).toBeTruthy();
    expect((screen.getByRole("button", { name: "应用到成片" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText(/已应用/)).toBeNull();
  });
  it("applies a draft to the target episode and shows the result summary", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({ automatic_apply: false, requires_human_action: true, items: [{ ...draftItem }] });
    vi.mocked(applyScriptBreakdownDraft).mockResolvedValue({
      apply: { draft_id: "draft", episode_id: "ep-1", created: { scenes: 2, shots: 4, lines: 4 }, extracted_characters: [{ name: "母亲", scene_count: 2 }, { name: "孩子", scene_count: 1 }], selected_scene_nos: [1], applied_scene_nos: [1], remaining_scene_nos: [], applied: true },
    });
    renderPanel();
    const button = await screen.findByRole("button", { name: "应用到成片" });
    fireEvent.click(screen.getByRole("checkbox", { name: /我已展开并审阅/ }));
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    expect(await screen.findByText(/应用完成：创建 2 场 · 4 镜 · 4 条对白/)).toBeTruthy();
    expect(screen.getByText(/母亲×2、孩子×1/)).toBeTruthy();
    expect(applyScriptBreakdownDraft).toHaveBeenCalledWith("draft", { episode_id: "ep-1", scene_nos: [1] });
  });
  it("creates an auditable human scene revision while applied scenes remain locked", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({ automatic_apply: false, requires_human_action: true, items: [{ ...draftItem }] });
    vi.mocked(reviseScriptBreakdownDraftScene).mockResolvedValue({
      revision: { draft_id: "draft", scene_no: 1, draft_revision: 2, effective_draft_revision_id: "revision-1", effective_draft_revision_no: 1, content_sha256: "b".repeat(64), draft: draftItem.draft, human_edited: true },
    });
    renderPanel();
    fireEvent.click(await screen.findByText(/展开完整草稿/));
    fireEvent.click(screen.getByRole("button", { name: "编辑场 1" }));
    fireEvent.change(screen.getByLabelText("场 1 标题"), { target: { value: "人工校订标题" } });
    fireEvent.change(screen.getByLabelText("场 1 镜 1 时长"), { target: { value: "4.5" } });
    fireEvent.change(screen.getByLabelText("场 1 修改说明"), { target: { value: "校正标题和镜头节奏" } });
    fireEvent.click(screen.getByRole("button", { name: "保存人工修订" }));
    await waitFor(() => expect(reviseScriptBreakdownDraftScene).toHaveBeenCalledWith("draft", 1, expect.objectContaining({
      expected_revision: 1,
      change_note: "校正标题和镜头节奏",
      title: "人工校订标题",
      characters: ["林默"],
    })));
    expect(vi.mocked(reviseScriptBreakdownDraftScene).mock.calls[0][2].shots[0].duration_seconds).toBe(4.5);
  });
  it("applies only selected scenes and keeps completed scenes locked", async () => {
    const twoSceneDraft: ScriptBreakdownDraft = {
      ...draftItem,
      application_status: "PARTIALLY_APPLIED",
      applied_scene_nos: [1],
      remaining_scene_nos: [2],
      confidence: { ...draftItem.confidence, duration_contract_status: "PASS" },
      draft: { scenes: [
        ...(draftItem.draft.scenes ?? []),
        { scene_no: 2, title: "清晨重逢", summary: "苏晚抵达", characters: ["苏晚"], shots: [{ shot_no: 1, visual: "门外", action: "苏晚敲门", dialogue: "", duration_seconds: 1 }] },
      ] },
    };
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({ automatic_apply: false, requires_human_action: true, items: [twoSceneDraft] });
    vi.mocked(applyScriptBreakdownDraft).mockResolvedValue({
      apply: { draft_id: "draft", episode_id: "ep-1", created: { scenes: 1, shots: 1, lines: 0 }, extracted_characters: [{ name: "苏晚", scene_count: 1 }], selected_scene_nos: [2], applied_scene_nos: [1, 2], remaining_scene_nos: [], applied: true },
    });
    renderPanel();
    fireEvent.click(await screen.findByText(/展开完整草稿/));
    expect(screen.getByRole("checkbox", { name: "选择场 1" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "选择场 1" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "选择场 2" })).toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: /我已展开并审阅所选场次/ }));
    fireEvent.click(screen.getByRole("button", { name: "应用到成片" }));
    await waitFor(() => expect(applyScriptBreakdownDraft).toHaveBeenCalledWith("draft", { episode_id: "ep-1", scene_nos: [2] }));
  });
  it("blocks application when the draft duration exceeds the selected episode contract", async () => {
    vi.mocked(listEpisodes).mockResolvedValue({ items: [{ ...episodeItems[0], target_duration_ms: 60_000 }] });
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({ automatic_apply: false, requires_human_action: true, items: [{ ...draftItem }] });
    renderPanel();
    expect(await screen.findByRole("alert")).toHaveTextContent("草稿总时长 5 秒 · 目标 60 秒");
    expect(screen.getByRole("alert")).toHaveTextContent("禁止应用");
    fireEvent.click(screen.getByRole("checkbox", { name: /我已展开并审阅/ }));
    expect(screen.getByRole("button", { name: "应用到成片" })).toBeDisabled();
    expect(applyScriptBreakdownDraft).not.toHaveBeenCalled();
  });
  it("discloses deterministic timing normalization before human application", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({
      automatic_apply: false,
      requires_human_action: true,
      items: [{
        ...draftItem,
        confidence: {
          ...draftItem.confidence,
          target_episode_id: "ep-1",
          target_duration_seconds: 5,
          total_duration_seconds: 5,
          model_total_duration_seconds: 20,
          normalized_total_duration_seconds: 5,
          duration_adjustment_ratio: 0.25,
          duration_adjustment_status: "NORMALIZED_TO_TARGET",
          duration_contract_status: "PASS",
        },
      }],
    });
    renderPanel();
    expect(await screen.findByText(/模型原始合计 20 秒/)).toHaveTextContent("场景与动作未改");
    expect(screen.getByRole("button", { name: "应用到成片" })).toBeDisabled();
  });
  it("discloses stripped ungrounded dialogue without blocking a sanitized draft", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({
      automatic_apply: false,
      requires_human_action: true,
      items: [{
        ...draftItem,
        confidence: {
          ...draftItem.confidence,
          stripped_ungrounded_dialogue_count: 2,
          dialogue_adjustment_status: "STRIPPED_UNGROUNDED",
          dialogue_grounding_status: "PASS",
        },
      }],
    });
    renderPanel();
    expect(await screen.findByText(/检测到 2 镜对白无法逐字对齐本场原文/)).toHaveTextContent("已确定性清空并记录原输出哈希");
  });
  it("discloses extractive scene fallback without blocking the auditable sanitized draft", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({
      automatic_apply: false,
      requires_human_action: true,
      items: [{
        ...draftItem,
        confidence: {
          ...draftItem.confidence,
          scene_grounding_status: "PASS",
          scene_adjustment_status: "EXTRACTIVE_FALLBACK",
          extractive_fallback_scene_count: 1,
          scene_adjustments: [{ scene_no: 1, model_scene_sha256: "a".repeat(64), model_source_similarity: 0.01 }],
        },
      }],
    });
    renderPanel();
    expect(await screen.findByText(/检测到 1 场与引用原文匹配不足/)).toHaveTextContent("已替换为逐句原文安全镜头并记录原场景哈希");
    expect(screen.getByRole("button", { name: "应用到成片" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /我已展开并审阅/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: "应用到成片" })).toBeEnabled());
  });
  it("surfaces server grounding blockers and keeps unsafe persisted drafts disabled", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({
      automatic_apply: false,
      requires_human_action: true,
      items: [{
        ...draftItem,
        confidence: { ...draftItem.confidence, target_episode_id: "ep-1" },
        application_blockers: [{
          code: "LOCAL_LLM_DIALOGUE_GROUNDING_INVALID",
          message: "模型对白未逐字落在本场已验证原文引用中，禁止保存或应用草稿",
        }],
      }],
    });
    renderPanel();
    expect(await screen.findByText(/模型对白未逐字落在本场已验证原文引用中/)).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /我已展开并审阅/ }));
    expect(screen.getByRole("button", { name: "应用到成片" })).toBeDisabled();
    expect(applyScriptBreakdownDraft).not.toHaveBeenCalled();
  });
  it("queues a new Job from the blocked draft without reimporting the source", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({
      automatic_apply: false,
      requires_human_action: true,
      items: [{
        ...draftItem,
        confidence: { ...draftItem.confidence, target_episode_id: "ep-1" },
        application_blockers: [{ code: "LOCAL_LLM_DIALOGUE_GROUNDING_INVALID", message: "对白未通过来源校验" }],
      }, {
        ...draftItem,
        id: "draft-older",
        confidence: { ...draftItem.confidence, target_episode_id: "ep-1" },
        application_blockers: [{ code: "LOCAL_LLM_SCENE_GROUNDING_INVALID", message: "场景未通过来源校验" }],
      }],
    });
    vi.mocked(requestScriptBreakdown).mockResolvedValue({
      job: { id: "job-safe-retry", type: "SCRIPT_BREAKDOWN_LOCAL_LLM", project_id: "project-1", state: "QUEUED", channel: "CPU", priority: 60, max_attempts: 1, revision: 1 },
      automatic_apply: false,
      requires_human_action: true,
    });
    renderPanel();
    fireEvent.click((await screen.findAllByRole("button", { name: "按当前原文与目标重新生成" }))[0]);
    expect(await screen.findByText(/新的安全复检任务已排队/)).toHaveTextContent("job-safe-ret");
    expect(screen.getAllByText(/新的安全复检任务已排队/)).toHaveLength(1);
    expect(requestScriptBreakdown).toHaveBeenCalledWith("session", "profile-v1", "ep-1", expect.any(String), {
      sourceParagraphStart: undefined,
      sourceParagraphEnd: undefined,
    });
  });
  it("shows applied state and disables further application for APPLIED drafts", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({
      automatic_apply: false, requires_human_action: true,
      items: [{ ...draftItem, status: "APPLIED", application_status: "APPLIED", requires_human_action: false }],
    });
    renderPanel();
    expect(await screen.findByText(/APPLIED · APPLIED/)).toBeTruthy();
    expect(screen.getByText(/已应用：本草稿的场次\/镜头\/对白已落地为生产实体/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("does not invent suggestions for an empty production project", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({ automatic_apply: false, requires_human_action: true, items: [] });
    renderPanel();
    expect(await screen.findByText(/不会显示模拟建议/)).toBeTruthy();
  });
});
