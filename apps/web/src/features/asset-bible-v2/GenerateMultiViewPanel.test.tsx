import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listProfiles } from "../../generated/api";
import { GenerateMultiViewPanel } from "./GenerateMultiViewPanel";
import { preflightAssetMultiView } from "./multiviewClient";

vi.mock("../../generated/api", () => ({ listProfiles: vi.fn() }));
vi.mock("./multiviewClient", () => ({
  bindMultiViewReference: vi.fn(),
  getAssetMultiViewHistory: vi.fn().mockResolvedValue([]),
  isMultiViewBatchActive: vi.fn().mockReturnValue(false),
  preflightAssetMultiView: vi.fn(),
  submitAssetMultiView: vi.fn(),
}));

const hero = {
  id: "ref-1", project_id: "project-1", story_asset_id: "asset-1", asset_state_id: null,
  media_version_id: "media-1", reference_kind: "HERO", label: "主参考", priority: 100,
  is_locked: true, yaw_deg: null, pitch_deg: null, status: "ACTIVE", revision: 1,
};

function renderPanel() {
  return render(<GenerateMultiViewPanel projectId="project-1" assetId="asset-1" assetKind="CHARACTER" assetStatus="ACTIVE" states={[]} baseReferences={[hero]} initialBatches={[]} onReferencesChanged={vi.fn().mockResolvedValue(undefined)} />);
}

describe("GenerateMultiViewPanel profile selection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(preflightAssetMultiView).mockResolvedValue({ preflight: {
      asset_id: "asset-1", project_id: "project-1", capability: "IMAGE_MULTI_VIEW", status: "READY", ready: true,
      blockers: [], hero: null, profile_resolution: { profile_version_id: null, input_role: null }, views: [], plan_hash: "plan-1",
      would_persist_intent: false, would_create_variants: 3, would_create_jobs: 3,
    } });
  });

  it("lists only published IMAGE_MULTI_VIEW versions with semantic labels and keeps AUTO default", async () => {
    vi.mocked(listProfiles).mockResolvedValue({ items: [
      { id: "profile-1", version_id: "version-secret-1", code: "CHARACTER_3VIEW", title: "角色三视图", version_no: 4, capability: "IMAGE_MULTI_VIEW", status: "PUBLISHED" },
      { id: "profile-2", version_id: "version-secret-2", code: "DRAFT_3VIEW", title: "草稿", version_no: 2, capability: "IMAGE_MULTI_VIEW", status: "DRAFT" },
      { id: "profile-3", version_id: "version-secret-3", code: "VIDEO", title: "视频", version_no: 8, capability: "VIDEO_GENERATION", status: "PUBLISHED" },
    ] });
    renderPanel();

    const select = await screen.findByLabelText("已发布的三视图 Profile");
    await waitFor(() => expect((select as HTMLSelectElement).disabled).toBe(false));
    expect(screen.getByRole("option", { name: "AUTO · 按项目偏好解析" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "角色三视图 · CHARACTER_3VIEW · v4" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: /草稿|视频/ })).toBeNull();
    expect(document.body.textContent).not.toContain("version-secret");

    fireEvent.change(select, { target: { value: "version-secret-1" } });
    fireEvent.click(screen.getByRole("button", { name: "运行只读预检" }));
    await waitFor(() => expect(preflightAssetMultiView).toHaveBeenCalledWith("asset-1", expect.objectContaining({ profile_version_id: "version-secret-1" })));
  });

  it("explains catalogue failure without blocking AUTO preflight", async () => {
    vi.mocked(listProfiles).mockRejectedValue(new Error("offline"));
    renderPanel();
    expect((await screen.findByRole("alert")).textContent).toContain("AUTO 仍可运行只读预检");
    expect((screen.getByRole("button", { name: "运行只读预检" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
