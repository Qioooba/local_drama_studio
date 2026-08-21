import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { commitStoryboardBatch, getStoryboardWorkspace, planStoryboardBatch, type StoryboardWorkspace } from "../../generated/api";
import { StoryboardBatchWorkbench } from "./StoryboardBatchWorkbench";
import { getShotGroupWorkspace } from "../episode-plan-v2/shotGroupsApi";
import { getEpisodePlanAssets, runPerShot } from "../episode-plan-v2/episodePlanTableApi";
import { getShotEditContext } from "../episode-plan-v2/shotEditingApi";

vi.mock("../../generated/api", () => ({ commitStoryboardBatch: vi.fn(), getStoryboardWorkspace: vi.fn(), planStoryboardBatch: vi.fn() }));
vi.mock("../episode-plan-v2/shotGroupsApi", () => ({ getShotGroupWorkspace: vi.fn() }));
vi.mock("../episode-plan-v2/shotEditingApi", () => ({ commitShotEdit: vi.fn(), getShotEditContext: vi.fn(), planShotEdit: vi.fn() }));
vi.mock("../episode-plan-v2/episodePlanTableApi", () => ({
  getEpisodePlanAssets: vi.fn(), markEpisodePlanShotReady: vi.fn(),
  runPerShot: vi.fn(), setEpisodePlanShotAssetState: vi.fn(),
}));
const shot = (id: string, code: string, duration: number, revision = 1) => ({ id, code, order_key: "1", target_duration_ms: duration, shot_type: "OTHER", status: "DRAFT", revision, current_revision_id: `r-${id}`, current_revision_no: 1, is_frozen: 0, fields: { action: `${code} 动作` }, display_ordinal: 1, timeline_start_ms: 0, timeline_end_ms: duration });
const workspace: StoryboardWorkspace = { episode: { id: "ep-1", title: "第一集" }, items: [shot("a", "SH-001", 1000), shot("b", "SH-002", 2000)], views: ["TABLE", "STORYBOARD", "TIMELINE"], identity_invariant: "stable", total_duration_ms: 3000 };
const renderPanel = () => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><StoryboardBatchWorkbench episodeId="ep-1" /></QueryClientProvider>);

describe("StoryboardBatchWorkbench", () => {
  beforeEach(() => {
    vi.mocked(getStoryboardWorkspace).mockReset().mockResolvedValue({ storyboard: workspace });
    vi.mocked(planStoryboardBatch).mockReset().mockResolvedValue({ plan: { ordered_shot_ids: ["b", "a"], edits: [{ shot_id: "a", expected_revision: 1, target_duration_ms: 4500, shot_type: "OTHER" }], copies: [], plan_hash: "f".repeat(64), valid: true, issues: [], summary: { reordered: 2, edited: 1, copied: 0 }, runtime_contacted: false, network_contacted: false } });
    vi.mocked(commitStoryboardBatch).mockReset().mockResolvedValue({ result: { plan_hash: "f".repeat(64), changed_shot_ids: ["a"], copied_shot_ids: [], storyboard: workspace } });
    vi.mocked(getShotGroupWorkspace).mockReset().mockResolvedValue({ episode: { id: "ep-1", code: "E01", title: "第一集", project_id: "p-1" }, scenes: [], shots: [], groups: [] });
    vi.mocked(getShotEditContext).mockReset().mockResolvedValue({ episode_id: "ep-1", ordering_token: "token", items: [] });
    vi.mocked(getEpisodePlanAssets).mockReset().mockResolvedValue([]);
    vi.mocked(runPerShot).mockReset();
  });
  it("switches all three views and displays content", async () => {
    renderPanel();
    await screen.findByText("SH-001");
    fireEvent.click(screen.getByRole("button", { name: "故事板" }));
    expect(screen.getByText("SH-001 动作")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "时间线" }));
    expect(screen.getByText("1000 ms")).toBeTruthy();
  });

  it("edits while freezing the expected revision into explicit commit via drawer", async () => {
    renderPanel();
    await screen.findByText("SH-001");
    const editBtns = screen.getAllByRole("button", { name: "编辑详情" });
    fireEvent.click(editBtns[0]);
    fireEvent.change(screen.getByLabelText("SH-001 时长"), { target: { value: "4500" } });
    fireEvent.click(screen.getByRole("button", { name: "关闭抽屉" }));

    fireEvent.click(screen.getByRole("button", { name: "重排、拆分与复制" }));
    fireEvent.click(screen.getByRole("button", { name: "校验字段 / 复制计划" }));
    await waitFor(() => expect(planStoryboardBatch).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "确认字段 / 复制" }));
    await waitFor(() => expect(commitStoryboardBatch).toHaveBeenCalled());
    expect(vi.mocked(commitStoryboardBatch).mock.calls[0][1].ordered_shot_ids).toEqual(["a", "b"]);
    expect(vi.mocked(commitStoryboardBatch).mock.calls[0][1].edits[0]).toMatchObject({ shot_id: "a", expected_revision: 1, target_duration_ms: 4500 });
  });

  it("reports per-shot Production Ready partial success without claiming atomicity", async () => {
    vi.mocked(runPerShot).mockResolvedValue([
      { shotId: "a", ok: true, message: "已提交" },
      { shotId: "b", ok: false, message: "缺少导演字段" },
    ]);
    renderPanel();
    await screen.findByText("SH-001");
    fireEvent.click(screen.getByLabelText("选择 SH-001"));
    fireEvent.click(screen.getByLabelText("选择 SH-002"));
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.click(screen.getByRole("button", { name: "批量 Production Ready" }));
    expect(await screen.findByText("批量结果：成功 1 · 失败 1")).toBeTruthy();
    expect(screen.getByText("SH-002：缺少导演字段")).toBeTruthy();
    expect(runPerShot).toHaveBeenCalledWith(["a", "b"], expect.any(Function));
  });

  it("applies a shot type to the selected set and includes every expected revision in the preview", async () => {
    renderPanel();
    await screen.findByText("SH-001");
    fireEvent.click(screen.getByLabelText("选择 SH-001"));
    fireEvent.click(screen.getByLabelText("选择 SH-002"));
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    fireEvent.change(screen.getByLabelText("批量镜头类型"), { target: { value: "MEDIUM" } });
    fireEvent.click(screen.getByRole("button", { name: "应用到所选镜头草稿" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭抽屉" }));

    fireEvent.click(screen.getByRole("button", { name: "重排、拆分与复制" }));
    fireEvent.click(screen.getByRole("button", { name: "校验字段 / 复制计划" }));
    await waitFor(() => expect(planStoryboardBatch).toHaveBeenCalled());
    expect(vi.mocked(planStoryboardBatch).mock.calls[0][1].edits).toEqual([
      { shot_id: "a", expected_revision: 1, target_duration_ms: 1000, shot_type: "MEDIUM" },
      { shot_id: "b", expected_revision: 1, target_duration_ms: 2000, shot_type: "MEDIUM" },
    ]);
  });
});
