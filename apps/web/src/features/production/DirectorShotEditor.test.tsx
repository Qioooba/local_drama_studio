import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createShotRevision, markShotProductionReady, resolveProfileCameraPlan } from "../../generated/api";
import { DirectorShotEditor } from "./DirectorShotEditor";

vi.mock("../../generated/api", () => ({ createShotRevision: vi.fn(), markShotProductionReady: vi.fn(), resolveProfileCameraPlan: vi.fn() }));

function renderEditor(shot: Record<string, unknown>, profiles: Array<{ id: string; code: string; title: string; version_id: string; capability: string; status: string }> = []) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const onChanged = vi.fn();
  render(<QueryClientProvider client={client}><DirectorShotEditor shot={shot} profiles={profiles} onChanged={onChanged} /></QueryClientProvider>);
  return onChanged;
}

describe("DirectorShotEditor", () => {
  beforeEach(() => {
    vi.mocked(createShotRevision).mockReset().mockResolvedValue({ shot_revision: { id: "revision-2" } });
    vi.mocked(markShotProductionReady).mockReset().mockResolvedValue({ shot: { id: "shot-1", status: "READY" } });
    vi.mocked(resolveProfileCameraPlan).mockReset();
  });

  it("shows exact missing fields and saves a new frozen revision", async () => {
    const onChanged = renderEditor({ id: "shot-1", code: "S001", status: "DRAFT", current_revision_id: "revision-1", current_revision: {} });
    expect(screen.getByRole("status").textContent).toContain("还缺 7 项");
    fireEvent.change(screen.getByLabelText("景别"), { target: { value: "CLOSEUP" } });
    fireEvent.change(screen.getByLabelText("时长"), { target: { value: "4000" } });
    fireEvent.click(screen.getByRole("button", { name: "保存新 revision" }));
    await waitFor(() => expect(createShotRevision).toHaveBeenCalled());
    expect(vi.mocked(createShotRevision).mock.calls[0][0]).toBe("shot-1");
    expect(vi.mocked(createShotRevision).mock.calls[0][2]).toBe(true);
    expect(onChanged).toHaveBeenCalled();
  });

  it("enables Production Ready only for a complete directed revision", async () => {
    const fields = { shot_type: "CLOSEUP", composition: "center", subject_action: "turn", camera_plan: { mode: "NATIVE", shot_type: "CLOSEUP", movement: "PUSH_IN", prompt_text: "", direction: "FORWARD", intensity: 0.5, curve: "LINEAR", profile_version_id: "profile-v1" }, target_duration_ms: 4000, dialogue: "", environment: "", continuity: "same", creative_intent: "focus" };
    renderEditor({ id: "shot-1", code: "S001", status: "DIRECTED", current_revision_id: "revision-2", current_revision: fields, production_readiness: { state: "DIRECTED", blockers: [] } });
    const button = screen.getByRole("button", { name: "标记 Production Ready" }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(markShotProductionReady).toHaveBeenCalledWith("shot-1"));
  });

  it("uses the published Profile to resolve structured camera capability", async () => {
    vi.mocked(resolveProfileCameraPlan).mockResolvedValue({ resolution: {
      camera_plan: { mode: "NATIVE", shot_type: "CLOSEUP", movement: "PUSH_IN", prompt_text: "", direction: "FORWARD", intensity: 0.5, curve: "LINEAR", profile_version_id: "profile-v1" },
      submission_allowed: true, support: "NATIVE", profile: { id: "profile-v1", code: "local-i2v", version_no: 1 }, runtime_contacted: false, network_contacted: false, mutated: false,
    } });
    renderEditor(
      { id: "shot-1", code: "S001", status: "DRAFT", current_revision_id: "revision-1", current_revision: {} },
      [{ id: "profile", code: "local-i2v", title: "本地 I2V", version_id: "profile-v1", capability: "I2V", status: "PUBLISHED" }],
    );
    fireEvent.change(screen.getByLabelText("景别"), { target: { value: "CLOSEUP" } });
    fireEvent.change(screen.getByLabelText("运动"), { target: { value: "PUSH_IN" } });
    fireEvent.click(screen.getByRole("button", { name: "按 Profile 裁决运镜能力" }));
    await waitFor(() => expect(resolveProfileCameraPlan).toHaveBeenCalledWith("profile-v1", expect.objectContaining({ shot_type: "CLOSEUP", movement: "PUSH_IN" })));
    expect(await screen.findByText(/原生参数映射/)).toBeTruthy();
  });

  it("keeps Production Ready blocked when the published Profile does not support camera", async () => {
    vi.mocked(resolveProfileCameraPlan).mockResolvedValue({ resolution: {
      camera_plan: { mode: "UNSUPPORTED", shot_type: "CLOSEUP", movement: "ORBIT", prompt_text: "", direction: "CLOCKWISE", intensity: 0.5, curve: "LINEAR", profile_version_id: "profile-v1" },
      submission_allowed: false, support: "UNSUPPORTED", profile: { id: "profile-v1", code: "local-i2v", version_no: 1 }, runtime_contacted: false, network_contacted: false, mutated: false,
    } });
    renderEditor(
      { id: "shot-1", code: "S001", status: "DIRECTED", current_revision_id: "revision-1", current_revision: {
        shot_type: "CLOSEUP", composition: "center", subject_action: "turn", target_duration_ms: 4000, dialogue: "", environment: "", continuity: "same", creative_intent: "focus",
      } },
      [{ id: "profile", code: "local-i2v", title: "本地 I2V", version_id: "profile-v1", capability: "I2V", status: "PUBLISHED" }],
    );
    fireEvent.change(screen.getByLabelText("运动"), { target: { value: "ORBIT" } });
    fireEvent.click(screen.getByRole("button", { name: "按 Profile 裁决运镜能力" }));
    expect(await screen.findByText(/当前未裁决或 Profile 不支持/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "标记 Production Ready" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("renders the explicit prompt when the Profile only supports prompt fallback", async () => {
    vi.mocked(resolveProfileCameraPlan).mockResolvedValue({ resolution: {
      camera_plan: { mode: "PROMPT_FALLBACK", shot_type: "CLOSEUP", movement: "PUSH_IN", prompt_text: "camera: PUSH_IN", direction: "FORWARD", intensity: 0.5, curve: "LINEAR", profile_version_id: "profile-v1" },
      submission_allowed: true, support: "PROMPT_FALLBACK", profile: { id: "profile-v1", code: "local-i2v", version_no: 1 }, runtime_contacted: false, network_contacted: false, mutated: false,
    } });
    renderEditor(
      { id: "shot-1", code: "S001", status: "DIRECTED", current_revision_id: "revision-1", current_revision: { shot_type: "CLOSEUP" } },
      [{ id: "profile", code: "local-i2v", title: "本地 I2V", version_id: "profile-v1", capability: "I2V", status: "PUBLISHED" }],
    );
    fireEvent.change(screen.getByLabelText("运动"), { target: { value: "PUSH_IN" } });
    fireEvent.click(screen.getByRole("button", { name: "按 Profile 裁决运镜能力" }));
    expect(await screen.findByText(/显式 Prompt 降级/)).toBeTruthy();
    expect((screen.getByLabelText("Prompt 降级文本") as HTMLTextAreaElement).value).toBe("camera: PUSH_IN");
  });
});
