import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listProfiles } from "../../generated/api";
import { GenerateMultiViewPanel } from "./GenerateMultiViewPanel";
import { bindMultiViewReference, preflightAssetMultiView } from "./multiviewClient";

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

    const select = await screen.findByLabelText("已发布的三视图生成模型");
    await waitFor(() => expect((select as HTMLSelectElement).disabled).toBe(false));
    expect(screen.getByRole("option", { name: "自动使用项目偏好" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "角色三视图 · 第 4 版" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: /草稿|视频/ })).toBeNull();
    expect(document.body.textContent).not.toContain("version-secret");

    fireEvent.change(select, { target: { value: "version-secret-1" } });
    fireEvent.click(screen.getByRole("button", { name: "预检 3 个缺失视图" }));
    await waitFor(() => expect(preflightAssetMultiView).toHaveBeenCalledWith("asset-1", expect.objectContaining({ profile_version_id: "version-secret-1", requested_slots: ["FRONT", "LEFT", "RIGHT"] })));
  });

  it("explains catalogue failure without blocking AUTO preflight", async () => {
    vi.mocked(listProfiles).mockRejectedValue(new Error("offline"));
    renderPanel();
    expect((await screen.findByRole("alert")).textContent).toContain("AUTO 仍可运行只读预检");
    expect((screen.getByRole("button", { name: "预检 3 个缺失视图" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("bulk-binds every successful unbound output and reports partial failures", async () => {
    vi.mocked(listProfiles).mockResolvedValue({ items: [] });
    const onChanged = vi.fn().mockResolvedValue(undefined);
    vi.mocked(bindMultiViewReference)
      .mockResolvedValueOnce({ reference: {} as never })
      .mockRejectedValueOnce(new Error("RIGHT 绑定冲突"));
    render(<GenerateMultiViewPanel
      projectId="project-1"
      assetId="asset-1"
      assetKind="CHARACTER"
      assetStatus="ACTIVE"
      states={[]}
      baseReferences={[hero]}
      initialBatches={[{
        intent_id: "batch-1", status: "SUCCEEDED", created_at: "2026-08-24T00:00:00Z", completed_count: 2, failed_count: 0, total_count: 2,
        items: ["LEFT", "RIGHT"].map((kind, index) => ({
          reference_kind: kind, yaw_deg: index ? 90 : -90, variant_id: `variant-${index}`, variant_no: index + 1,
          variant_status: "SUCCEEDED", job_id: `job-${index}`, job_state: "SUCCEEDED", progress: { percent: 100 }, error: null,
          outputs: [{ media_version_id: `media-${kind.toLowerCase()}`, source_artifact_id: `artifact-${index}`, rel_path: `${kind}.png`, mime_type: "image/png", sha256: "a".repeat(64), integrity_status: "VERIFIED" }],
        })),
      }] as never}
      onReferencesChanged={onChanged}
    />);

    fireEvent.click(await screen.findByRole("button", { name: "一键回绑 2 个成功视图" }));
    await waitFor(() => expect(bindMultiViewReference).toHaveBeenCalledTimes(2));
    expect(bindMultiViewReference).toHaveBeenNthCalledWith(1, "asset-1", null, "LEFT", "media-left");
    expect(bindMultiViewReference).toHaveBeenNthCalledWith(2, "asset-1", null, "RIGHT", "media-right");
    expect(await screen.findByText("回绑结果：成功 1 · 失败 1")).toBeTruthy();
    expect(screen.getByText("RIGHT：RIGHT 绑定冲突")).toBeTruthy();
    expect(onChanged).toHaveBeenCalledTimes(1);
  });
});
