import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DirectorIntentEditor } from "./DirectorIntentEditor";
import { ApiRequestError, markShotReadyV2, putShotDraftV2 } from "../../generated/api";

vi.mock("../../generated/api", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("../../generated/api")>();
  return { ...original, markShotReadyV2: vi.fn(), putShotDraftV2: vi.fn(), resolveProfileCameraPlan: vi.fn() };
});

const baseFields = {
  schema_version: "director-intent.v3", shot_type: "MEDIUM", subject_action: "服务端动作", creative_intent: "紧张",
  composition: { preset: "CENTER" }, performance: { emotion: "克制" }, camera_plan: { movement: "STATIC" },
};
const revision = (revisionNo: number) => ({ id: `revision-${revisionNo}`, revision_no: revisionNo, is_frozen: false, fields: baseFields });
const key = (revisionNo: number) => `local-drama:director-intent-draft:v1:shot-1:${revisionNo}:director-intent.v3`;
const stored = (revisionNo: number, action: string) => JSON.stringify({
  format_version: 1, shot_id: "shot-1", base_revision_no: revisionNo, schema_version: "director-intent.v3",
  saved_at: "2026-08-21T12:00:00.000Z", fields: { ...baseFields, subject_action: action },
});
const props = (revisionNo: number) => ({ shotId: "shot-1", shotCode: "S01", currentRevision: revision(revisionNo), canEdit: true });
const readyFields = {
  ...baseFields,
  camera_plan: {
    movement: "STATIC", direction: "FORWARD", intensity: 0.5, curve: "LINEAR",
    prompt_text: "", profile_version_id: "profile-v1", mode: "NATIVE",
  },
};

describe("DirectorIntentEditor local draft buffer", () => {
  afterEach(() => vi.restoreAllMocks());
  beforeEach(() => {
    window.localStorage.clear();
    vi.clearAllMocks();
    const result = { shot_revision: { id: "revision-2", shot_id: "shot-1", revision_no: 2, is_frozen: false, fields: {} }, shot: { id: "shot-1", status: "DIRECTED", current_revision_id: "revision-2", revision: 2, updated_at: "now" } };
    vi.mocked(putShotDraftV2).mockResolvedValue(result);
    vi.mocked(markShotReadyV2).mockResolvedValue({ ...result, shot: { ...result.shot, status: "READY" } });
  });

  it("offers recovery without silently replacing the authoritative revision", () => {
    window.localStorage.setItem(key(1), stored(1, "本地恢复动作"));
    render(<DirectorIntentEditor {...props(1)} />);
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("服务端动作");
    expect(screen.getByText("发现可恢复的本地草稿")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "恢复草稿" }));
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("本地恢复动作");
    expect(screen.getByText("未保存")).toBeTruthy();
  });

  it("clears the shot draft after a successful authoritative save", async () => {
    render(<DirectorIntentEditor {...props(1)} />);
    fireEvent.change(screen.getByLabelText("主体动作"), { target: { value: "待保存动作" } });
    await waitFor(() => expect(window.localStorage.getItem(key(1))).not.toBeNull(), { timeout: 1_500 });
    fireEvent.click(screen.getByRole("button", { name: /仅保存为第 2 版/ }));
    await waitFor(() => expect(putShotDraftV2).toHaveBeenCalled());
    expect(window.localStorage.getItem(key(1))).toBeNull();
  });

  it("marks an older revision draft stale and migrates it only after explicit confirmation", () => {
    window.localStorage.setItem(key(1), stored(1, "旧 revision 动作"));
    render(<DirectorIntentEditor {...props(2)} />);
    expect(screen.getByText("发现过期的本地草稿")).toBeTruthy();
    expect(screen.getByText(/草稿基于第 1 版.*当前为第 2 版.*不会自动套用/)).toBeTruthy();
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("服务端动作");
    fireEvent.click(screen.getByRole("button", { name: "显式迁移并复核" }));
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("旧 revision 动作");
    expect(screen.getByText(/已迁移基于第 1 版/)).toBeTruthy();
  });

  it("keeps a 409 draft and marks it stale after the authoritative revision reloads", async () => {
    vi.mocked(putShotDraftV2).mockRejectedValueOnce(new ApiRequestError("revision conflict", 409, "REVISION_CONFLICT", null, false, null, { current_revision_no: 2 }));
    const { rerender } = render(<DirectorIntentEditor {...props(1)} />);
    fireEvent.change(screen.getByLabelText("主体动作"), { target: { value: "冲突中的本地动作" } });
    await waitFor(() => expect(window.localStorage.getItem(key(1))).not.toBeNull(), { timeout: 1_500 });
    fireEvent.click(screen.getByRole("button", { name: /仅保存为第 2 版/ }));
    expect(await screen.findByText("保存冲突")).toBeTruthy();
    expect(window.localStorage.getItem(key(1))).not.toBeNull();
    rerender(<DirectorIntentEditor {...props(2)} />);
    expect(await screen.findByText("发现过期的本地草稿")).toBeTruthy();
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("服务端动作");
  });

  it("fails safely when browser storage rejects a debounced write", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("quota", "QuotaExceededError"); });
    render(<DirectorIntentEditor {...props(1)} />);
    fireEvent.change(screen.getByLabelText("主体动作"), { target: { value: "仍可编辑" } });
    expect((await screen.findByRole("alert", {}, { timeout: 1_500 })).textContent).toContain("本地草稿写入失败");
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("仍可编辑");
    expect((screen.getByRole("button", { name: /仅保存为第 2 版/ }) as HTMLButtonElement).disabled).toBe(false);
    setItem.mockRestore();
  });

  it("ignores malformed local JSON and preserves the server revision", () => {
    window.localStorage.setItem(key(1), "{not-json");
    render(<DirectorIntentEditor {...props(1)} />);
    expect(screen.getByRole("alert").textContent).toContain("本地草稿 JSON 已损坏");
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("服务端动作");
    expect(screen.queryByText("发现可恢复的本地草稿")).toBeNull();
  });

  it("atomically saves a dirty complete intent and marks the shot ready in one action", async () => {
    render(<DirectorIntentEditor {...props(1)} shotStatus="DIRECTED" currentRevision={{ ...revision(1), fields: readyFields }} />);
    fireEvent.change(screen.getByLabelText("主体动作"), { target: { value: "完成后的导演动作" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并就绪" }));
    await waitFor(() => expect(markShotReadyV2).toHaveBeenCalledTimes(1));
    expect(putShotDraftV2).not.toHaveBeenCalled();
    expect(await screen.findByText(/镜头已标记为可进入生产/)).toBeTruthy();
  });

  it("keeps the draft unsaved when the atomic ready gate changes", async () => {
    vi.mocked(markShotReadyV2).mockRejectedValueOnce(new Error("门禁刚刚变化"));
    render(<DirectorIntentEditor {...props(1)} shotStatus="DIRECTED" currentRevision={{ ...revision(1), fields: readyFields }} />);
    fireEvent.change(screen.getByLabelText("主体动作"), { target: { value: "已保存但门禁变化" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并就绪" }));
    expect(await screen.findByText(/保存并就绪失败，未写入新版本：门禁刚刚变化/)).toBeTruthy();
    expect(putShotDraftV2).not.toHaveBeenCalled();
    expect(screen.getByText("未保存")).toBeTruthy();
  });

  it("offers grounded environment and continuity suggestions without applying them silently", () => {
    render(<DirectorIntentEditor {...props(1)} intentSuggestions={{
      environment: { value: "灯塔内；夜景", source_label: "LIGHTHOUSE_NIGHT · 海边旧灯塔" },
      continuity: { value: "男主身着风衣，左肩受伤", source_label: "SHOT_001", eligible: true, reason: null },
    }} />);
    expect((screen.getByLabelText("环境差异与补充") as HTMLTextAreaElement).value).toBe("");
    expect((screen.getByLabelText("连续性变化") as HTMLTextAreaElement).value).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "采用建议" }));
    fireEvent.click(screen.getByRole("button", { name: "继承并复核" }));
    expect((screen.getByLabelText("环境差异与补充") as HTMLTextAreaElement).value).toBe("灯塔内；夜景");
    expect((screen.getByLabelText("连续性变化") as HTMLTextAreaElement).value).toBe("男主身着风衣，左肩受伤");
    expect(screen.getByText("未保存")).toBeTruthy();
  });

  it("adopts script-derived intent with source provenance and exposes stale refresh", async () => {
    render(<DirectorIntentEditor {...props(1)} intentSuggestions={{
      environment: null,
      continuity: null,
      script: {
        subject_action: "阿宁拆开旧信",
        creative_intent: "旧信特写；迟疑被打破",
        dialogue: "阿宁：是你吗？",
        source_label: "第一集剧本 · 场 1 镜 2",
        source_revision_id: "draft-revision-3",
        source_fingerprint: "f".repeat(64),
        stale: true,
        stale_reason: "剧本拆解来源已变化，请重新采用并复核镜头意图。",
      },
    }} />);
    expect(screen.getByRole("alert").textContent).toContain("剧本来源已更新");
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("服务端动作");
    fireEvent.click(screen.getByRole("button", { name: "重新采用并复核" }));
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("阿宁拆开旧信");
    expect((screen.getByLabelText("画面创作意图") as HTMLTextAreaElement).value).toBe("旧信特写；迟疑被打破");
    fireEvent.click(screen.getByRole("button", { name: /仅保存为第 2 版/ }));
    await waitFor(() => expect(putShotDraftV2).toHaveBeenCalledWith("shot-1", expect.objectContaining({
      fields: expect.objectContaining({
        suggestion_sources: expect.objectContaining({
          script: expect.objectContaining({ source_fingerprint: "f".repeat(64), source_revision_id: "draft-revision-3" }),
        }),
      }),
    })));
  });
});
