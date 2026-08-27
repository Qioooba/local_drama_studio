import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { bindStoryAssetToShot, createStoryAsset, listShotStoryAssets, listStoryAssets, markShotReadyV2, putShotDraftV2, resolveProfileCameraPlan, unbindStoryAssetFromShot } from "../../generated/api";
import { bindShotCharacterPack, getShotCharacterPacks } from "../asset-bible-v2/identityPackClient";
import { DirectorShotEditor } from "./DirectorShotEditor";

vi.mock("../../generated/api", () => ({ bindStoryAssetToShot: vi.fn(), putShotDraftV2: vi.fn(), createStoryAsset: vi.fn(), listShotStoryAssets: vi.fn(), listStoryAssets: vi.fn(), markShotReadyV2: vi.fn(), resolveProfileCameraPlan: vi.fn(), unbindStoryAssetFromShot: vi.fn() }));
vi.mock("../asset-bible-v2/identityPackClient", () => ({ bindShotCharacterPack: vi.fn(), getShotCharacterPacks: vi.fn() }));

function renderEditor(shot: Record<string, unknown>, profiles: Array<{ id: string; code: string; title: string; version_id: string; capability: string; status: string }> = [], projectId?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const onChanged = vi.fn();
  render(<QueryClientProvider client={client}><DirectorShotEditor shot={shot} profiles={profiles} onChanged={onChanged} projectId={projectId} /></QueryClientProvider>);
  return onChanged;
}

describe("DirectorShotEditor", () => {
  beforeEach(() => {
    vi.mocked(putShotDraftV2).mockReset().mockResolvedValue({ shot_revision: { id: "revision-2", shot_id: "shot-1", revision_no: 2, fields: {}, is_frozen: true }, shot: { id: "shot-1", status: "DIRECTED", current_revision_id: "revision-2", revision: 2, updated_at: "now" } });
    vi.mocked(createStoryAsset).mockReset().mockResolvedValue({ asset: { id: "asset-new", project_id: "project-1", kind: "CHARACTER", code: "CHAR_YOUNG", name: "少年", description: "", canonical_media_version_id: null, extra: {}, status: "ACTIVE", revision: 1, created_at: "now", updated_at: "now", created_by: "local-user", schema_version: "v2" } });
    vi.mocked(markShotReadyV2).mockReset().mockResolvedValue({ shot_revision: { id: "revision-2", shot_id: "shot-1", revision_no: 2, fields: {}, is_frozen: true }, shot: { id: "shot-1", status: "READY", current_revision_id: "revision-2", revision: 3, updated_at: "now" } });
    vi.mocked(resolveProfileCameraPlan).mockReset();
    vi.mocked(listStoryAssets).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(listShotStoryAssets).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(bindStoryAssetToShot).mockReset().mockResolvedValue({ binding: { binding_id: "binding-1", shot_id: "shot-1", asset_id: "asset-1", name: "母亲", code: "CHAR_MOTHER", kind: "CHARACTER", status: "ACTIVE", canonical_media_version_id: null, role_in_shot: "main", created_at: "now", created_by: "local-user" } });
    vi.mocked(unbindStoryAssetFromShot).mockReset().mockResolvedValue({ unbound: true, binding_id: "binding-1" });
    vi.mocked(getShotCharacterPacks).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(bindShotCharacterPack).mockReset().mockResolvedValue({ binding: { shot_id: "shot-1", story_asset_id: "asset-1", identity_pack_version_id: "pack-version-1" } });
  });

  it("shows exact missing fields and saves a new frozen revision", async () => {
    const onChanged = renderEditor({ id: "shot-1", code: "S001", status: "DRAFT", current_revision_id: "revision-1", current_revision: {} });
    expect(screen.getByRole("status").textContent).toContain("还缺 7 项");
    fireEvent.change(screen.getByLabelText("景别"), { target: { value: "CLOSEUP" } });
    fireEvent.change(screen.getByLabelText("时长（秒）"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "保存新版本" }));
    await waitFor(() => expect(putShotDraftV2).toHaveBeenCalled());
    expect(vi.mocked(putShotDraftV2).mock.calls[0][0]).toBe("shot-1");
    expect(vi.mocked(putShotDraftV2).mock.calls[0][1].freeze).toBe(true);
    expect(onChanged).toHaveBeenCalled();
  });

  it("enables Production Ready only for a complete directed revision", async () => {
    const fields = { shot_type: "CLOSEUP", composition: "center", subject_action: "turn", camera_plan: { mode: "NATIVE", shot_type: "CLOSEUP", movement: "PUSH_IN", prompt_text: "", direction: "FORWARD", intensity: 0.5, curve: "LINEAR", profile_version_id: "profile-v1" }, target_duration_ms: 4000, dialogue: "", environment: "", continuity: "same", creative_intent: "focus" };
    renderEditor({ id: "shot-1", code: "S001", status: "DIRECTED", current_revision_id: "revision-2", current_revision: fields, production_readiness: { state: "DIRECTED", blockers: [] } });
    const button = screen.getByRole("button", { name: "标记为可进入生产" }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(markShotReadyV2).toHaveBeenCalledWith("shot-1", { expected_revision_no: undefined }));
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
    fireEvent.click(screen.getByRole("button", { name: "按生成配置检查运镜能力" }));
    await waitFor(() => expect(resolveProfileCameraPlan).toHaveBeenCalledWith("profile-v1", expect.objectContaining({ shot_type: "CLOSEUP", movement: "PUSH_IN" })));
    expect(await screen.findByText(/模型原生支持/)).toBeTruthy();
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
    fireEvent.click(screen.getByRole("button", { name: "按生成配置检查运镜能力" }));
    expect(await screen.findByText(/当前生成配置不支持/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "标记为可进入生产" }) as HTMLButtonElement).disabled).toBe(true);
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
    fireEvent.click(screen.getByRole("button", { name: "按生成配置检查运镜能力" }));
    expect(await screen.findByText(/提示词兼容/)).toBeTruthy();
    expect((screen.getByLabelText("兼容运镜补充描述") as HTMLTextAreaElement).value).toBe("camera: PUSH_IN");
  });

  it("offers the blueprint camera vocabulary including zoom", () => {
    renderEditor({ id: "shot-1", code: "S001", status: "DRAFT", current_revision_id: "revision-1", current_revision: {} });
    expect(screen.getByRole("option", { name: "变焦" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "环绕" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "滚转" })).toBeTruthy();
  });

  it("binds and unbinds story assets when a project is provided", async () => {
    vi.mocked(listStoryAssets).mockResolvedValue({ items: [
      { id: "asset-1", project_id: "project-1", kind: "CHARACTER", code: "CHAR_MOTHER", name: "母亲", description: "", canonical_media_version_id: null, extra: {}, status: "ACTIVE", revision: 1, created_at: "now", updated_at: "now", created_by: "local-user", schema_version: "v2" },
      { id: "asset-2", project_id: "project-1", kind: "SCENE", code: "SCENE_KITCHEN", name: "厨房", description: "", canonical_media_version_id: null, extra: {}, status: "ACTIVE", revision: 1, created_at: "now", updated_at: "now", created_by: "local-user", schema_version: "v2" },
    ] });
    vi.mocked(listShotStoryAssets).mockResolvedValue({ items: [
      { binding_id: "binding-1", shot_id: "shot-1", asset_id: "asset-1", name: "母亲", code: "CHAR_MOTHER", kind: "CHARACTER", status: "ACTIVE", canonical_media_version_id: null, role_in_shot: "main", created_at: "now", created_by: "local-user" },
    ] });
    renderEditor({ id: "shot-1", code: "S001", status: "DIRECTED", current_revision_id: "revision-1", current_revision: {} }, [], "project-1");
    expect(await screen.findByText("故事资产")).toBeTruthy();
    expect(await screen.findByText("母亲")).toBeTruthy();
    expect(screen.getByText(/角色：main/)).toBeTruthy();
    const select = screen.getByLabelText("绑定资产") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "asset-2" } });
    fireEvent.change(screen.getByLabelText("镜头内角色"), { target: { value: "location" } });
    fireEvent.click(screen.getByRole("button", { name: "绑定到本镜头" }));
    expect(bindStoryAssetToShot).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog").textContent).toContain("SCENE_KITCHEN");
    fireEvent.click(screen.getByRole("button", { name: "确认绑定" }));
    await waitFor(() => expect(bindStoryAssetToShot).toHaveBeenCalledWith("shot-1", { asset_id: "asset-2", role_in_shot: "location" }));
    fireEvent.click(screen.getByRole("button", { name: "解绑" }));
    expect(unbindStoryAssetFromShot).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog").textContent).toContain("确认解绑 母亲");
    fireEvent.click(screen.getByRole("button", { name: "确认解绑" }));
    await waitFor(() => expect(unbindStoryAssetFromShot).toHaveBeenCalledWith("binding-1"));
  });

  it("allows the first approved identity-pack version to be bound to a character", async () => {
    vi.mocked(listStoryAssets).mockResolvedValue({ items: [
      { id: "asset-1", project_id: "project-1", kind: "CHARACTER", code: "CHAR_MOTHER", name: "母亲", description: "", canonical_media_version_id: null, extra: {}, status: "ACTIVE", revision: 1, created_at: "now", updated_at: "now", created_by: "local-user", schema_version: "v2" },
    ] });
    vi.mocked(listShotStoryAssets).mockResolvedValue({ items: [
      { binding_id: "binding-1", shot_id: "shot-1", asset_id: "asset-1", name: "母亲", code: "CHAR_MOTHER", kind: "CHARACTER", status: "ACTIVE", canonical_media_version_id: null, role_in_shot: "main", created_at: "now", created_by: "local-user" },
    ] });
    vi.mocked(getShotCharacterPacks).mockResolvedValue({ items: [{
      shot_id: "shot-1",
      story_asset_id: "asset-1",
      asset_state_id: null,
      identity_pack_version_id: null,
      character_name: "母亲",
      character_code: "CHAR_MOTHER",
      approved_versions: [{ pack_id: "pack-1", pack_name: "基础造型", pack_code: "BASE", version_id: "pack-version-1", version_no: 1, status: "APPROVED" }],
      is_stale: false,
    }] });
    renderEditor({ id: "shot-1", code: "S001", status: "DIRECTED", current_revision_id: "revision-1", current_revision: {} }, [], "project-1");

    const select = await screen.findByLabelText("身份包版本 CHAR_MOTHER");
    expect((select as HTMLSelectElement).value).toBe("");
    fireEvent.change(select, { target: { value: "pack-version-1" } });

    await waitFor(() => expect(bindShotCharacterPack).toHaveBeenCalledWith("shot-1", {
      story_asset_id: "asset-1",
      pack_version_id: "pack-version-1",
    }));
  });

  it("creates a missing story asset in place and immediately binds it to the shot", async () => {
    renderEditor({ id: "shot-1", code: "S001", status: "DIRECTED", current_revision_id: "revision-1", current_revision: {} }, [], "project-1");
    await screen.findByText("本镜头尚未绑定故事资产。");
    fireEvent.click(screen.getByText("资产库里没有？就地创建并绑定"));
    fireEvent.change(screen.getByLabelText("新资产名称"), { target: { value: "少年" } });
    expect(screen.getByText(/^CHAR_/)).toBeTruthy();
    expect(screen.queryByLabelText("新资产编号")).toBeNull();
    fireEvent.change(screen.getByLabelText("镜头内角色"), { target: { value: "secondary" } });
    fireEvent.click(screen.getByRole("button", { name: "创建并绑定到本镜头" }));

    await waitFor(() => expect(createStoryAsset).toHaveBeenCalledWith("project-1", expect.objectContaining({ kind: "CHARACTER", name: "少年" })));
    expect(bindStoryAssetToShot).toHaveBeenCalledWith("shot-1", { asset_id: "asset-new", role_in_shot: "secondary" });
  });

  it("uses the same confirmation command for semantic card drag and button paths", async () => {
    const scene = { id: "asset-scene", project_id: "project-1", kind: "SCENE" as const, code: "SCENE_ROOF", name: "天台", description: "", canonical_media_version_id: "image-version", extra: {}, status: "ACTIVE" as const, revision: 1, created_at: "now", updated_at: "now", created_by: "local-user", schema_version: "v2" };
    vi.mocked(listStoryAssets).mockResolvedValue({ items: [scene] });
    renderEditor({ id: "shot-1", code: "S001", status: "PRODUCTION_READY", current_revision_id: "revision-1", current_revision: {} }, [], "project-1");
    const cardButton = await screen.findByRole("button", { name: "选择 SCENE_ROOF 天台" });
    const dataTransfer = { setData: vi.fn(), getData: vi.fn(), effectAllowed: "none", dropEffect: "none" };
    fireEvent.dragStart(cardButton.closest("article")!, { dataTransfer });
    const serialized = vi.mocked(dataTransfer.setData).mock.calls.find(([kind]) => kind === "application/x-localdrama-story-asset")?.[1];
    dataTransfer.getData.mockReturnValue(String(serialized));
    fireEvent.drop(screen.getByLabelText("镜头资产拖放槽"), { dataTransfer });
    expect(bindStoryAssetToShot).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认绑定" }));
    await waitFor(() => expect(bindStoryAssetToShot).toHaveBeenCalledWith("shot-1", { asset_id: "asset-scene", role_in_shot: "main" }));
    const thumbnail = document.querySelector(".shot-asset-catalogue img") as HTMLImageElement;
    expect(thumbnail.getAttribute("src")).toContain("/thumbnail?size=small&frame=poster");
    expect(thumbnail.getAttribute("src")).not.toContain("/content");
  });

  it("rejects cross-project drops before opening confirmation", async () => {
    renderEditor({ id: "shot-1", code: "S001", status: "DIRECTED", current_revision_id: "revision-1", current_revision: {} }, [], "project-1");
    await screen.findByText("本镜头尚未绑定故事资产。");
    const foreign = { id: "asset-foreign", project_id: "project-2", kind: "CHARACTER", code: "CHAR_OTHER", name: "外部角色", status: "ACTIVE" };
    fireEvent.drop(screen.getByLabelText("镜头资产拖放槽"), { dataTransfer: { getData: () => JSON.stringify(foreign), dropEffect: "copy" } });
    expect(screen.getByRole("alert").textContent).toContain("其他项目");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(bindStoryAssetToShot).not.toHaveBeenCalled();
  });

  it("keeps the story asset section hidden without a project", async () => {
    renderEditor({ id: "shot-1", code: "S001", status: "DRAFT", current_revision_id: "revision-1", current_revision: {} });
    expect(screen.queryByText("故事资产")).toBeNull();
    expect(listStoryAssets).not.toHaveBeenCalled();
  });
});
