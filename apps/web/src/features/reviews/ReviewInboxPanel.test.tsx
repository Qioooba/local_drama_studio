import { fireEvent, render, screen } from "@testing-library/react";
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

function renderPanel() {
  return render(<ReviewInboxPanel items={items} templates={[]} selectedVersionId={null} context={undefined} onSelect={vi.fn()} onPromote={vi.fn()} selecting={false} onMachineCheck={vi.fn()} machineChecking={false} machineCheckError={null} onSubmit={vi.fn()} submitting={false} submitError={null} submitSucceeded={false} />);
}

function countText() {
  return screen.getByText(/读取顺序稳定/).textContent ?? "";
}

describe("ReviewInboxPanel filters", () => {
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
});
