import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { ReviewInboxPanel } from "./ReviewInboxPanel";

vi.mock("../../generated/api", () => ({
  commitReviewBatch: vi.fn(),
  getReviewContext: vi.fn(),
  preflightReviewBatch: vi.fn(),
  submitReview: vi.fn(),
}));

const items = [
  { media_version_id: "old", media_asset_id: "asset-old", project_id: "p1", project_code: "alpha", episode_id: "e1", episode_code: "E01", media_kind: "IMAGE", stage: "KEYFRAME", decision: null, is_stale: 0, age_hours: 240, priority: "HIGH", is_blocked: 1 },
  { media_version_id: "fresh", media_asset_id: "asset-fresh", project_id: "p2", project_code: "beta", episode_id: "e2", episode_code: "E02", media_kind: "VIDEO", stage: "PROXY", decision: null, is_stale: 0, age_hours: 2, priority: "NORMAL", is_blocked: 0 },
  { media_version_id: "stale", media_asset_id: "asset-stale", project_id: "p2", project_code: "beta", episode_id: "e2", episode_code: "E02", media_kind: "AUDIO", stage: "IMPORTED", decision: "REJECTED", is_stale: 1, age_hours: 48, priority: "HIGH", is_blocked: 1 },
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
    const selectedButton = screen.getByRole("button", { name: /PROXY · VIDEO/ });
    expect(selectedButton.getAttribute("aria-current")).toBe("true");
    fireEvent.keyDown(selectedButton, { key: "ArrowLeft" });
    expect(onSelect).toHaveBeenCalledWith("old");
    expect(screen.getByRole("button", { name: /KEYFRAME · IMAGE/ }).getAttribute("aria-current")).toBe("true");
  });

  it("filters project, episode, age, priority and blocking without changing the source list", () => {
    renderPanel();
    expect(countText()).toContain("显示 3/3");
    fireEvent.change(screen.getByLabelText("项目筛选"), { target: { value: "p2" } });
    expect(countText()).toContain("显示 2/3");
    fireEvent.change(screen.getByLabelText("年龄筛选"), { target: { value: "AGING" } });
    expect(countText()).toContain("显示 1/3");
    expect(screen.getByRole("button", { name: /IMPORTED · AUDIO/ })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("阻塞筛选"), { target: { value: "READY" } });
    expect(countText()).toContain("显示 0/3");
  });

  it("keeps explicit high-priority and stale filters separate", () => {
    renderPanel();
    fireEvent.change(screen.getByLabelText("审核状态"), { target: { value: "STALE" } });
    fireEvent.change(screen.getByLabelText("优先级筛选"), { target: { value: "HIGH" } });
    expect(countText()).toContain("显示 1/3");
    expect(screen.getByRole("button", { name: /IMPORTED · AUDIO/ })).toBeTruthy();
  });

  it("exposes probe-fps frame stepping for the selected video", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ReviewInboxPanel items={items} templates={[]} selectedVersionId="fresh" context={videoContext} onSelect={vi.fn()} onPromote={vi.fn()} selecting={false} onMachineCheck={vi.fn()} machineChecking={false} machineCheckError={null} onSubmit={vi.fn()} submitting={false} submitError={null} submitSucceeded={false} /></QueryClientProvider>);
    expect(screen.getByText(/按 probe fps 25\.000/)).toBeTruthy();
    const video = document.querySelector("video") as HTMLVideoElement;
    Object.defineProperty(video, "duration", { configurable: true, value: 1 });
    fireEvent.click(screen.getByRole("button", { name: "逐帧进" }));
    expect(screen.getByRole("button", { name: "逐帧退" })).toBeTruthy();
  });
});
