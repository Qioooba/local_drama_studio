import { beforeEach, describe, expect, it, vi } from "vitest";
import { getJob, promoteJobArtifactToMedia } from "../../generated/api";
import { collectMultiViewOutput } from "./multiviewClient";

vi.mock("../../generated/api", () => ({ getJob: vi.fn(), promoteJobArtifactToMedia: vi.fn(), requestJson: vi.fn() }));

describe("collectMultiViewOutput", () => {
  beforeEach(() => vi.clearAllMocks());
  it("promotes only verified images from successful attempts", async () => {
    vi.mocked(getJob).mockResolvedValue({ job: { state: "SUCCEEDED", attempts: [
      { state: "FAILED", artifacts: [{ id: "old", status: "VERIFIED", sandbox_rel_path: "old.png" }] },
      { state: "SUCCEEDED", artifacts: [
        { id: "image", status: "VERIFIED", sandbox_rel_path: "view.png" },
        { id: "report", status: "VERIFIED", sandbox_rel_path: "report.json" },
        { id: "invalid", status: "INVALID", sandbox_rel_path: "invalid.png" },
      ] },
    ] } } as never);
    vi.mocked(promoteJobArtifactToMedia).mockResolvedValue({ media: {} });
    await collectMultiViewOutput("job");
    expect(promoteJobArtifactToMedia).toHaveBeenCalledTimes(1);
    expect(promoteJobArtifactToMedia).toHaveBeenCalledWith("image", { purpose: "ASSET_REFERENCE", media_kind: "IMAGE", stage: "KEYFRAME" });
  });
  it("does not promote a running job", async () => {
    vi.mocked(getJob).mockResolvedValue({ job: { state: "RUNNING", attempts: [] } } as never);
    await expect(collectMultiViewOutput("job")).rejects.toThrow("尚未成功");
    expect(promoteJobArtifactToMedia).not.toHaveBeenCalled();
  });
});
