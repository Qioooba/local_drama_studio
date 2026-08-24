import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { commitReviewBatch, preflightReviewBatch } from "../../generated/api";
import { ReviewInboxPanel } from "./ReviewInboxPanel";

vi.mock("../../generated/api", () => ({
  commitReviewBatch: vi.fn(),
  getReviewContext: vi.fn(),
  listJobs: vi.fn().mockResolvedValue({ items: [] }),
  preflightReviewBatch: vi.fn(),
  submitReview: vi.fn(),
}));

const items = [
  { media_version_id: "old", media_asset_id: "asset-old", project_id: "p1", project_code: "alpha", episode_id: "e1", episode_code: "E01", media_kind: "IMAGE", stage: "KEYFRAME", decision: null, is_stale: 0, age_hours: 240, priority: "HIGH", is_blocked: 1 },
  { media_version_id: "fresh", media_asset_id: "asset-fresh", project_id: "p2", project_code: "beta", episode_id: "e2", episode_code: "E02", shot_id: "shot-12", shot_code: "S12", media_kind: "VIDEO", stage: "PROXY", decision: null, is_stale: 0, age_hours: 2, priority: "NORMAL", is_blocked: 0 },
  { media_version_id: "stale", media_asset_id: "asset-stale", project_id: "p2", project_code: "beta", episode_id: "e2", episode_code: "E02", media_kind: "AUDIO", stage: "IMPORTED", decision: "NEEDS_CHANGES", is_stale: 1, age_hours: 48, priority: "HIGH", is_blocked: 1 },
] as never[];


const videoContext = {
  media_version: { id: "fresh", media_kind: "VIDEO", stage: "PROXY", duration_ms: 1_000, fps_num: 25, fps_den: 1, sha256: "hash" },
  subject_revision: 1,
  template: { id: "template", code: "proxy_video", version_no: 1, items: [] },
  selections: [],
  reviews: [],
  machine_checks: [],
} as never;

function renderPanel() {
  return render(<ReviewInboxPanel items={items} templates={[]} selectedVersionId={null} context={undefined} onSelect={vi.fn()} onPromote={vi.fn()} selecting={false} onMachineCheck={vi.fn()} machineChecking={false} machineCheckError={null} onSubmit={vi.fn()} submitting={false} submitError={null} submitSucceeded={false} />);
}

function countText() {
  return screen.getByText(/读取顺序稳定/).textContent ?? "";
}

describe("ReviewInboxPanel filters", () => {
  it("exposes selected review state and supports arrow-key candidate navigation", () => {
    let selected = "fresh";
    const onSelect = vi.fn((id: string) => { selected = id; rerenderPanel(); });
    let rerenderPanel: () => void = () => undefined;
    const renderResult = render(<ReviewInboxPanel items={items} templates={[]} selectedVersionId={selected} context={undefined} onSelect={onSelect} onPromote={vi.fn()} selecting={false} onMachineCheck={vi.fn()} machineChecking={false} machineCheckError={null} onSubmit={vi.fn()} submitting={false} submitError={null} submitSucceeded={false} />);
    rerenderPanel = () => renderResult.rerender(<ReviewInboxPanel items={items} templates={[]} selectedVersionId={selected} context={undefined} onSelect={onSelect} onPromote={vi.fn()} selecting={false} machineChecking={false} machineCheckError={null} onMachineCheck={vi.fn()} onSubmit={vi.fn()} submitting={false} submitError={null} submitSucceeded={false} />);
    const selectedButton = screen.getByRole("button", { name: /预览候选 · 视频/ });
    expect(selectedButton.textContent).toContain("S12 · 预览候选 · 视频");
    expect(selectedButton.textContent).not.toContain("fresh");
    expect(selectedButton.getAttribute("aria-current")).toBe("true");
    fireEvent.keyDown(selectedButton, { key: "ArrowLeft" });
    expect(onSelect).toHaveBeenCalledWith("old");
    expect(screen.getByRole("button", { name: /关键帧 · 图片/ }).getAttribute("aria-current")).toBe("true");
  });

  it("filters project, episode, age, priority and blocking without changing the source list", () => {
    renderPanel();
    expect(countText()).toContain("显示 3/3");
    fireEvent.change(screen.getByLabelText("项目筛选"), { target: { value: "p2" } });
    expect(countText()).toContain("显示 2/3");
    fireEvent.change(screen.getByLabelText("年龄筛选"), { target: { value: "AGING" } });
    expect(countText()).toContain("显示 1/3");
    expect(screen.getByRole("button", { name: /导入素材 · 音频/ })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("阻塞筛选"), { target: { value: "READY" } });
    expect(countText()).toContain("显示 0/3");
  });

  it("keeps explicit high-priority and stale filters separate", () => {
    renderPanel();
    fireEvent.change(screen.getByLabelText("审核状态"), { target: { value: "STALE" } });
    fireEvent.change(screen.getByLabelText("优先级筛选"), { target: { value: "HIGH" } });
    expect(countText()).toContain("显示 1/3");
    expect(screen.getByRole("button", { name: /导入素材 · 音频/ })).toBeTruthy();
  });

  it("moves the selection to the first visible item instead of keeping a filtered-out detail", async () => {
    const onSelect = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ReviewInboxPanel items={items} templates={[]} selectedVersionId="fresh" context={videoContext} onSelect={onSelect} onPromote={vi.fn()} selecting={false} onMachineCheck={vi.fn()} machineChecking={false} machineCheckError={null} onSubmit={vi.fn()} submitting={false} submitError={null} submitSucceeded={false} /></QueryClientProvider>);
    fireEvent.change(screen.getByLabelText("媒体类型"), { target: { value: "AUDIO" } });
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("stale"));
    expect(screen.queryByText(/按 probe fps/)).toBeNull();
    expect(screen.getByText("正在切换到当前筛选项。")).toBeTruthy();
  });

  it("exposes probe-fps frame stepping for the selected video", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ReviewInboxPanel items={items} templates={[]} selectedVersionId="fresh" context={videoContext} onSelect={vi.fn()} onPromote={vi.fn()} selecting={false} onMachineCheck={vi.fn()} machineChecking={false} machineCheckError={null} onSubmit={vi.fn()} submitting={false} submitError={null} submitSucceeded={false} /></QueryClientProvider>);
    expect(screen.getByText(/按视频帧率逐帧移动/)).toBeTruthy();
    const video = document.querySelector("video") as HTMLVideoElement;
    Object.defineProperty(video, "duration", { configurable: true, value: 1 });
    fireEvent.click(screen.getByRole("button", { name: "逐帧进" }));
    expect(screen.getByRole("button", { name: "逐帧退" })).toBeTruthy();
  });

  it("exposes formal video machine QC and keeps approval disabled until it passes", () => {
    const formalItem = { media_version_id: "formal", media_asset_id: "asset-formal", project_id: "p2", project_code: "beta", episode_id: "e2", episode_code: "E02", shot_id: "shot-12", shot_code: "S12", media_kind: "VIDEO", stage: "FORMAL", decision: null, is_stale: 0, age_hours: 2, priority: "NORMAL", is_blocked: 1 } as never;
    const onMachineCheck = vi.fn();
    const formalContext = {
      media_version: { id: "formal", media_kind: "VIDEO", stage: "FORMAL", duration_ms: 1_000, fps_num: 25, fps_den: 1, sha256: "hash" },
      subject_revision: 1,
      template: { id: "formal-template", code: "formal_video", version_no: 1, items: [{ id: "decode", label: "可解码", required: true }] },
      selections: [],
      reviews: [],
      machine_checks: [],
    } as never;
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const { rerender } = render(<QueryClientProvider client={client}><ReviewInboxPanel items={[formalItem]} templates={[]} selectedVersionId="formal" context={formalContext} onSelect={vi.fn()} onPromote={vi.fn()} selecting={false} onMachineCheck={onMachineCheck} machineChecking={false} machineCheckError={null} onSubmit={vi.fn()} submitting={false} submitError={null} submitSucceeded={false} /></QueryClientProvider>);

    fireEvent.click(screen.getByRole("radio", { name: "通过" }));
    expect((screen.getByRole("button", { name: "提交审核" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "检查视频" }));
    expect(onMachineCheck).toHaveBeenCalledWith("formal");

    const passedContext = {
      media_version: { id: "formal", media_kind: "VIDEO", stage: "FORMAL", duration_ms: 1_000, fps_num: 25, fps_den: 1, sha256: "hash" },
      subject_revision: 1,
      template: { id: "formal-template", code: "formal_video", version_no: 1, items: [{ id: "decode", label: "可解码", required: true }] },
      selections: [],
      reviews: [],
      machine_checks: [{ status: "PASS", results: [{ item_id: "decode", result: "PASS", details: {} }] }],
    } as never;
    rerender(<QueryClientProvider client={client}><ReviewInboxPanel items={[formalItem]} templates={[]} selectedVersionId="formal" context={passedContext} onSelect={vi.fn()} onPromote={vi.fn()} selecting={false} onMachineCheck={onMachineCheck} machineChecking={false} machineCheckError={null} onSubmit={vi.fn()} submitting={false} submitError={null} submitSucceeded={false} /></QueryClientProvider>);
    expect(screen.getByText(/可解码：通过/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "提交审核" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("does not auto-pass human checks after batch preflight", async () => {
    vi.mocked(preflightReviewBatch).mockResolvedValue({ plan: { plan_id: "plan-1", plan_token: "token-1", expires_at: "2099-01-01T00:00:00Z", status: "READY", items: [{ media_version_id: "old", template_version_id: "image-template", expected_subject_revision: 1 }] } } as never);
    vi.mocked(commitReviewBatch).mockResolvedValue({ result: { plan_id: "plan-1", items: [{ media_version_id: "old" }] } } as never);
    const onBatchChanged = vi.fn();
    const imageTemplate = { id: "image-template", code: "image_asset", version_no: 1, items: [{ id: "identity", label: "人物身份", required: true }] } as never;
    render(<ReviewInboxPanel items={items} templates={[imageTemplate]} selectedVersionId={null} context={undefined} onSelect={vi.fn()} onPromote={vi.fn()} selecting={false} onMachineCheck={vi.fn()} machineChecking={false} machineCheckError={null} onSubmit={vi.fn()} submitting={false} submitError={null} submitSucceeded={false} onBatchChanged={onBatchChanged} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "E01 · 关键帧 · 图片" }));
    fireEvent.click(screen.getByRole("button", { name: "检查批量审核" }));
    const pass = await screen.findByRole("radio", { name: "通过" });
    expect((pass as HTMLInputElement).checked).toBe(false);
    expect((screen.getByRole("button", { name: "提交批量审核" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(pass);
    fireEvent.change(screen.getByLabelText("批量决定"), { target: { value: "REJECTED" } });
    expect((screen.getByRole("button", { name: "提交批量审核" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "人物身份或造型不一致" }));
    expect(screen.getByLabelText("批量拒绝原因")).toHaveProperty("value", "人物身份或造型不一致");
    expect((screen.getByRole("button", { name: "提交批量审核" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "提交批量审核" }));
    await waitFor(() => expect(onBatchChanged).toHaveBeenCalledTimes(1));
  });

  it("renders disabled '音轨绑定无需采用' button for IMPORTED AUDIO instead of KEYFRAME selection", () => {
    const audioItem = { media_version_id: "audio-imp", media_asset_id: "asset-audio", project_id: "p2", project_code: "beta", episode_id: "e2", episode_code: "E02", media_kind: "AUDIO", stage: "IMPORTED", decision: null, is_stale: 0, age_hours: 1, priority: "NORMAL", is_blocked: 0 } as never;
    const audioContext = {
      media_version: { id: "audio-imp", media_kind: "AUDIO", stage: "IMPORTED", duration_ms: 5_000, sha256: "hash" },
      subject_revision: 1,
      template: { id: "audio-template", code: "audio_mix", version_no: 1, items: [] },
      selections: [],
      reviews: [],
      machine_checks: [],
    } as never;
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ReviewInboxPanel items={[audioItem]} templates={[]} selectedVersionId="audio-imp" context={audioContext} onSelect={vi.fn()} onPromote={vi.fn()} selecting={false} onMachineCheck={vi.fn()} machineChecking={false} machineCheckError={null} onSubmit={vi.fn()} submitting={false} submitError={null} submitSucceeded={false} />
      </QueryClientProvider>
    );
    const noPromoteBtn = screen.getByRole("button", { name: "音轨绑定无需采用" }) as HTMLButtonElement;
    expect(noPromoteBtn).toBeTruthy();
    expect(noPromoteBtn.disabled).toBe(true);
    expect(screen.queryByRole("button", { name: /选择为 KEYFRAME/ })).toBeNull();
  });

  it("offers the next review item and a context-preserving return to Director after saving", () => {
    const onSelect = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ReviewInboxPanel items={items} templates={[]} selectedVersionId="fresh" context={{ media_version: { id: "fresh", media_kind: "VIDEO", stage: "PROXY", duration_ms: 1_000, fps_num: 25, fps_den: 1, sha256: "hash" }, subject_revision: 1, template: { id: "template", code: "proxy_video", version_no: 1, items: [] }, selections: [], reviews: [{ id: "review-1", decision: "NEEDS_CHANGES", is_stale: false }], machine_checks: [] } as never} onSelect={onSelect} onPromote={vi.fn()} selecting={false} onMachineCheck={vi.fn()} machineChecking={false} machineCheckError={null} onSubmit={vi.fn()} submitting={false} submitError={null} submitSucceeded />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "审核下一项" }));
    expect(onSelect).toHaveBeenCalledWith("stale");
    expect(screen.getByRole("link", { name: "返回导演台" }).getAttribute("href")).toBe("/projects/p2/episodes/e2/direct/shot-12");
    expect(screen.getByRole("link", { name: "进入时间线" }).getAttribute("href")).toBe("/projects/p2/episodes/e2/timeline");
  });
});
