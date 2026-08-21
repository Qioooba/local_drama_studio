import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { DeliveryWorkflowPanel } from "./DeliveryWorkflowPanel";
import { listEpisodeDeliveryPackages } from "../../generated/api";

vi.mock("../../generated/api", () => ({
  buildDeliveryPackage: vi.fn(),
  listEpisodeDeliveryPackages: vi.fn(),
  renderEpisode: vi.fn(),
  reviewDeliveryPackage: vi.fn(),
  verifyDeliveryPackage: vi.fn(),
  withdrawDeliveryPackage: vi.fn(),
}));

describe("DeliveryWorkflowPanel", () => {
  it("surfaces bounded DOWNLOAD audit counts in delivery history", async () => {
    vi.mocked(listEpisodeDeliveryPackages).mockResolvedValue({
      items: [{
        id: "package-1",
        episode_render_version_id: "render-1",
        target_version_id: "target-1",
        status: "VERIFIED",
        manifest_sha256: "a".repeat(64),
        rel_path: "06_delivery/Episode-1/delivery-package-1",
        events: [
          { action: "BUILT" },
          { action: "DOWNLOAD", manifest_sha256: "a".repeat(64), note: "{\"transport\":\"LOCAL_FILESYSTEM\"}" },
          { action: "DOWNLOAD", manifest_sha256: "a".repeat(64), note: "{\"transport\":\"LOCAL_FILESYSTEM\"}" },
        ],
      }],
      runtime_contacted: false,
      network_contacted: false,
    });

    render(<DeliveryWorkflowPanel episodeId="episode-1" timelineRevisionId={null} renderId={null} targetVersionId={null} deliveryId={null} />);
    await waitFor(() => expect(screen.getByText("已下载 2 次")).toBeTruthy());
    expect(screen.getByText("下载审计")).toBeTruthy();
  });

  it("exposes only the actions owned by the selected delivery step", async () => {
    vi.mocked(listEpisodeDeliveryPackages).mockResolvedValue({ items: [], runtime_contacted: false, network_contacted: false });
    const props = { episodeId: "episode-1", timelineRevisionId: "timeline-1", renderId: "render-1", targetVersionId: "target-1", deliveryId: "delivery-1" };
    const { rerender } = render(<DeliveryWorkflowPanel {...props} focus="COMPOSE" />);

    expect(screen.getByRole("button", { name: "登记整集渲染" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "创建交付候选" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "记录人工批准" })).toBeNull();

    rerender(<DeliveryWorkflowPanel {...props} focus="REVIEW" />);
    expect(screen.getByRole("button", { name: "验证 manifest / SHA" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "记录人工批准" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "登记整集渲染" })).toBeNull();

    rerender(<DeliveryWorkflowPanel {...props} focus="PACKAGE" />);
    expect(screen.getByRole("button", { name: "复验 manifest / SHA" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "记录人工批准" })).toBeNull();
    await waitFor(() => expect(listEpisodeDeliveryPackages).toHaveBeenCalledWith("episode-1"));
  });
});
