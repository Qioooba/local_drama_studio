import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DeliveryWorkflowPanel } from "./DeliveryWorkflowPanel";
import { getBackgroundOperation, listEpisodeDeliveryPackages, reviewDeliveryPackage, submitDeliveryPackageBuild, submitEpisodeCompose, verifyDeliveryPackage } from "../../generated/api";

vi.mock("../../generated/api", () => ({
  getBackgroundOperation: vi.fn(),
  listEpisodeDeliveryPackages: vi.fn(),
  submitDeliveryPackageBuild: vi.fn(),
  submitEpisodeCompose: vi.fn(),
  reviewDeliveryPackage: vi.fn(),
  verifyDeliveryPackage: vi.fn(),
  withdrawDeliveryPackage: vi.fn(),
}));

describe("DeliveryWorkflowPanel", () => {
  beforeEach(() => {
    vi.mocked(getBackgroundOperation).mockReset().mockResolvedValue({ job: { id: "job-queued", state: "QUEUED" }, result_type: null, result: null } as never);
    vi.mocked(submitDeliveryPackageBuild).mockReset();
  });
  it("surfaces bounded DOWNLOAD audit counts in delivery history", async () => {
    vi.mocked(listEpisodeDeliveryPackages).mockResolvedValue({
      items: [{
        id: "package-1",
        episode_render_version_id: "render-1",
        target_version_id: "target-1",
        status: "VERIFIED",
        artifact: { scope: "PROJECT", kind: "FILE", display_name: "EP001 · 抖音竖屏 · EP001-package.mp4", server_absolute_path: "F:\\DramaProjects\\p\\06_delivery\\EP001.mp4", rel_path: "06_delivery/EP001.mp4", download_url: "/api/v1/delivery-packages/package-1/download", download_filename: "EP001-package.mp4" },
        manifest_sha256: "a".repeat(64),
        rel_path: "06_delivery/Episode-1/delivery-package-1",
        files: [
          { rel_path: "06_delivery/Episode-1/delivery-package-1/EPISODE_001.mp4", sha256: "b".repeat(64), byte_size: 128 },
          { rel_path: "06_delivery/Episode-1/delivery-package-1/EPISODE_001.srt", sha256: "c".repeat(64), byte_size: 64 },
          { rel_path: "06_delivery/Episode-1/delivery-package-1/manifest.json", sha256: "d".repeat(64), byte_size: 256 },
        ],
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
    await screen.findByRole("link", { name: "下载 MP4 · 已记录 2 次" });
    expect(screen.getByText("下载与操作记录")).toBeTruthy();
    expect(screen.getByRole("link", { name: "下载 MP4 · 已记录 2 次" }).getAttribute("href")).toBe("/api/v1/delivery-packages/package-1/download");
    expect(screen.getByText("独立字幕")).toBeTruthy();
    expect(screen.getByText("06_delivery/Episode-1/delivery-package-1/EPISODE_001.srt")).toBeTruthy();
  });

  it("exposes only the actions owned by the selected delivery step", async () => {
    vi.mocked(listEpisodeDeliveryPackages).mockResolvedValue({ items: [], runtime_contacted: false, network_contacted: false });
    const props = { episodeId: "episode-1", timelineRevisionId: "timeline-1", renderId: "render-1", targetVersionId: "target-1", deliveryId: "delivery-1", renderApprovalReady: true };
    const { rerender } = render(<DeliveryWorkflowPanel {...props} focus="COMPOSE" />);

    expect(screen.getByRole("button", { name: "提交整集渲染任务" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "交付候选已创建" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "记录人工批准" })).toBeNull();

    rerender(<DeliveryWorkflowPanel {...props} focus="REVIEW" />);
    expect(screen.getByRole("button", { name: "验证交付文件" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "记录人工批准" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "提交整集渲染任务" })).toBeNull();

    rerender(<DeliveryWorkflowPanel {...props} focus="PACKAGE" />);
    expect(screen.getByRole("button", { name: "重新验证交付文件" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "记录人工批准" })).toBeNull();
    await waitFor(() => expect(listEpisodeDeliveryPackages).toHaveBeenCalledWith("episode-1"));
  });

  it("explains how to unlock disabled compose and review actions", () => {
    const { rerender } = render(<DeliveryWorkflowPanel episodeId="episode-1" timelineRevisionId={null} renderId={null} targetVersionId={null} deliveryId={null} focus="COMPOSE" />);

    const compose = screen.getByRole("button", { name: "提交整集渲染任务" });
    expect((compose as HTMLButtonElement).disabled).toBe(true);
    expect(compose.getAttribute("aria-describedby")).toBe("delivery-compose-blocker");
    expect(screen.getByText(/请先在时间线创建并冻结一个版本/)).toBeTruthy();
    expect(screen.getByText(/请先完成整集渲染/)).toBeTruthy();

    rerender(<DeliveryWorkflowPanel episodeId="episode-1" timelineRevisionId="timeline-1" renderId="render-1" targetVersionId="target-1" deliveryId={null} focus="REVIEW" />);
    expect(screen.getByText(/请先完成交付候选构建/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "一键验证并批准交付包" }).getAttribute("aria-describedby")).toBe("delivery-review-blocker");
  });

  it("blocks candidate creation until the current episode render is approved", async () => {
    vi.mocked(listEpisodeDeliveryPackages).mockResolvedValue({ items: [], runtime_contacted: false, network_contacted: false });
    render(<DeliveryWorkflowPanel episodeId="episode-1" timelineRevisionId="timeline-1" renderId="render-current" targetVersionId="target-1" deliveryId={null} renderApprovalReady={false} renderReviewHref="/projects/project-1/episodes/episode-1/post/review?targetKind=EPISODE_RENDER_VERSION&targetId=render-current" focus="COMPOSE" />);

    const build = screen.getByRole("button", { name: "等待整集审核" }) as HTMLButtonElement;
    expect(build.disabled).toBe(true);
    expect(build.getAttribute("aria-describedby")).toBe("delivery-build-blocker");
    expect(screen.getByText(/请先在整集审核工作区批准当前最新整集成片/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "打开整集审核工作区" }).getAttribute("href")).toContain("targetId=render-current");
    fireEvent.click(build);
    await waitFor(() => expect(submitDeliveryPackageBuild).not.toHaveBeenCalled());
  });

  it("reuses an existing render without creating a duplicate background job", async () => {
    vi.mocked(submitEpisodeCompose).mockResolvedValue({
      preflight: { status: "READY", blockers: [], warnings: [] },
      render: { id: "render-123456789", episode_id: "episode-1", timeline_revision_id: "timeline-1", status: "VERIFIED", integrity_status: "VERIFIED" },
      idempotent_replay: true,
    } as never);
    const onRenderCreated = vi.fn();
    render(<DeliveryWorkflowPanel episodeId="episode-1" timelineRevisionId="timeline-1" renderId={null} targetVersionId={null} deliveryId={null} focus="COMPOSE" onRenderCreated={onRenderCreated} />);
    fireEvent.click(screen.getByRole("button", { name: "提交整集渲染任务" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("VERIFIED"));
    expect(onRenderCreated).toHaveBeenCalledWith("render-123456789");
    expect(screen.getByRole("status").textContent).not.toContain("undefined");
  });

  it("keeps queued render and delivery work recoverable and hydrates the finished result", async () => {
    vi.mocked(submitEpisodeCompose).mockResolvedValue({ preflight: { status: "READY", blockers: [], warnings: [] }, job: { id: "job-render-new", state: "QUEUED" } } as never);
    vi.mocked(submitDeliveryPackageBuild).mockResolvedValue({ job: { id: "job-delivery-new", state: "QUEUED" } } as never);
    vi.mocked(getBackgroundOperation).mockImplementation(async (jobId) => jobId === "job-delivery-new"
      ? ({ job: { id: jobId, state: "SUCCEEDED" }, result_type: "DELIVERY", result: { id: "delivery-new", status: "VERIFIED" } } as never)
      : ({ job: { id: jobId, state: "QUEUED" }, result_type: null, result: null } as never));
    const onRenderCreated = vi.fn();
    const onDeliveryCreated = vi.fn();
    const { rerender } = render(<DeliveryWorkflowPanel episodeId="episode-1" timelineRevisionId="timeline-1" renderId="render-old" targetVersionId="target-1" deliveryId="delivery-old" renderApprovalReady focus="COMPOSE" onRenderCreated={onRenderCreated} onDeliveryCreated={onDeliveryCreated} />);

    fireEvent.click(screen.getByRole("button", { name: "提交整集渲染任务" }));
    await screen.findByText(/已进入后台队列/);
    expect(onRenderCreated).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: "任务中心" }).getAttribute("href")).toBe("/system/jobs");
    expect((screen.getByRole("button", { name: "整集渲染已排队" }) as HTMLButtonElement).disabled).toBe(true);
    rerender(<DeliveryWorkflowPanel episodeId="episode-1" timelineRevisionId="timeline-1" renderId="render-new" targetVersionId="target-1" deliveryId={null} renderApprovalReady focus="COMPOSE" onRenderCreated={onRenderCreated} onDeliveryCreated={onDeliveryCreated} />);
    fireEvent.click(screen.getByRole("button", { name: "提交交付候选任务" }));
    await waitFor(() => expect(onDeliveryCreated).toHaveBeenCalledWith("delivery-new"));
  });

  it("reports verify success separately when one-click approval fails", async () => {
    vi.mocked(verifyDeliveryPackage).mockResolvedValue({ delivery: { id: "delivery-1", status: "VERIFIED" } } as never);
    vi.mocked(reviewDeliveryPackage).mockRejectedValue(new Error("approval store unavailable"));
    render(<DeliveryWorkflowPanel episodeId="episode-1" timelineRevisionId="timeline-1" renderId="render-1" targetVersionId="target-1" deliveryId="delivery-1" focus="REVIEW" />);

    fireEvent.click(screen.getByRole("button", { name: "一键验证并批准交付包" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("交付包文件已完成验证（已验证）");
    expect(alert.textContent).toContain("记录人工批准");
    expect(screen.getByRole("button", { name: "记录人工批准" })).toBeTruthy();
  });

  it("sends a fresh idempotency key for each explicit delivery build click", async () => {
    vi.mocked(listEpisodeDeliveryPackages).mockResolvedValue({ items: [], runtime_contacted: false, network_contacted: false });
    vi.mocked(submitDeliveryPackageBuild).mockResolvedValue({ job: { id: "job-delivery-key", state: "QUEUED" } } as never);
    const baseProps = { episodeId: "episode-1", timelineRevisionId: "timeline-1", renderId: "render-1", targetVersionId: "target-1", deliveryId: null, renderApprovalReady: true };
    const { rerender } = render(<DeliveryWorkflowPanel {...baseProps} focus="COMPOSE" />);
    fireEvent.click(screen.getByRole("button", { name: "提交交付候选任务" }));
    await waitFor(() => expect(submitDeliveryPackageBuild).toHaveBeenCalledTimes(1));
    const firstKey = vi.mocked(submitDeliveryPackageBuild).mock.calls[0]?.[1];
    expect(firstKey).toEqual(expect.stringMatching(/^delivery-build:/));

    rerender(<DeliveryWorkflowPanel {...baseProps} renderId="render-2" focus="COMPOSE" />);
    const button = screen.getByRole("button", { name: "提交交付候选任务" });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(submitDeliveryPackageBuild).toHaveBeenCalledTimes(2));
    const secondKey = vi.mocked(submitDeliveryPackageBuild).mock.calls[1]?.[1];
    expect(secondKey).toEqual(expect.stringMatching(/^delivery-build:/));
    expect(secondKey).not.toBe(firstKey);
  });
});
