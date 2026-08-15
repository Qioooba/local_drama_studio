import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { commitStoryboardBatch, getStoryboardWorkspace, planStoryboardBatch, type StoryboardWorkspace } from "../../generated/api";
import { StoryboardBatchWorkbench } from "./StoryboardBatchWorkbench";

vi.mock("../../generated/api", () => ({ commitStoryboardBatch: vi.fn(), getStoryboardWorkspace: vi.fn(), planStoryboardBatch: vi.fn() }));
const shot = (id: string, code: string, duration: number, revision = 1) => ({ id, code, order_key: "1", target_duration_ms: duration, shot_type: "OTHER", status: "DRAFT", revision, current_revision_id: `r-${id}`, current_revision_no: 1, is_frozen: 0, fields: { action: `${code} 动作` }, display_ordinal: 1, timeline_start_ms: 0, timeline_end_ms: duration });
const workspace: StoryboardWorkspace = { episode: { id: "ep-1", title: "第一集" }, items: [shot("a", "SH-001", 1000), shot("b", "SH-002", 2000)], views: ["TABLE", "STORYBOARD", "TIMELINE"], identity_invariant: "stable", total_duration_ms: 3000 };
const renderPanel = () => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><StoryboardBatchWorkbench episodeId="ep-1" /></QueryClientProvider>);

describe("StoryboardBatchWorkbench", () => {
  beforeEach(() => {
    vi.mocked(getStoryboardWorkspace).mockReset().mockResolvedValue({ storyboard: workspace });
    vi.mocked(planStoryboardBatch).mockReset().mockResolvedValue({ plan: { ordered_shot_ids: ["b", "a"], edits: [{ shot_id: "a", expected_revision: 1, target_duration_ms: 4500, shot_type: "OTHER" }], copies: [], plan_hash: "f".repeat(64), valid: true, issues: [], summary: { reordered: 2, edited: 1, copied: 0 }, runtime_contacted: false, network_contacted: false } });
    vi.mocked(commitStoryboardBatch).mockReset().mockResolvedValue({ result: { plan_hash: "f".repeat(64), changed_shot_ids: ["a"], copied_shot_ids: [], storyboard: workspace } });
  });
  it("switches all three views and requires planning before commit", async () => {
    renderPanel();
    await screen.findByText("SH-001");
    const commit = screen.getByRole("button", { name: "确认提交计划" }) as HTMLButtonElement;
    expect(commit.disabled).toBe(true);
    fireEvent.click(screen.getByRole("tab", { name: "故事板" }));
    expect(screen.getByText("SH-001 动作")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "时间线" }));
    expect(screen.getByText("1000 ms")).toBeTruthy();
  });
  it("reorders and edits while freezing the expected revision into explicit commit", async () => {
    renderPanel();
    await screen.findByRole("table", { name: "分镜批量编辑表格" });
    fireEvent.click(screen.getByRole("button", { name: "SH-001 下移" }));
    fireEvent.change(screen.getByLabelText("SH-001 时长"), { target: { value: "4500" } });
    fireEvent.click(screen.getByRole("button", { name: "校验批量计划" }));
    await waitFor(() => expect(planStoryboardBatch).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "确认提交计划" }));
    await waitFor(() => expect(commitStoryboardBatch).toHaveBeenCalled());
    expect(vi.mocked(commitStoryboardBatch).mock.calls[0][1].ordered_shot_ids).toEqual(["b", "a"]);
    expect(vi.mocked(commitStoryboardBatch).mock.calls[0][1].edits[0]).toMatchObject({ shot_id: "a", expected_revision: 1, target_duration_ms: 4500 });
  });
});
