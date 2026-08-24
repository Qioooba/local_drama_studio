import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getStoryboardWorkspace, planStoryboardBatch } from "../../generated/api";
import { getShotEditContext, planShotEdit } from "../episode-plan-v2/shotEditingApi";
import { getShotGroupWorkspace } from "../episode-plan-v2/shotGroupsApi";
import { getEpisodePlanAssets } from "../episode-plan-v2/episodePlanTableApi";
import { getDirectorRecipeBinding } from "../recipes-v2/api";
import { StoryboardBatchWorkbench, storyboardDraftKey } from "./StoryboardBatchWorkbench";

vi.mock("../../generated/api", () => ({
  getStoryboardWorkspace: vi.fn(),
  planStoryboardBatch: vi.fn(),
  commitStoryboardBatch: vi.fn(),
}));

vi.mock("../episode-plan-v2/shotEditingApi", () => ({
  getShotEditContext: vi.fn(),
  planShotEdit: vi.fn(),
  commitShotEdit: vi.fn(),
}));

vi.mock("../episode-plan-v2/shotGroupsApi", () => ({
  getShotGroupWorkspace: vi.fn(),
}));

vi.mock("../episode-plan-v2/episodePlanTableApi", () => ({
  getEpisodePlanAssets: vi.fn(),
  markEpisodePlanShotReady: vi.fn(),
  setEpisodePlanShotAssetState: vi.fn(),
  runPerShot: vi.fn(),
}));

vi.mock("../recipes-v2/api", () => ({
  getDirectorRecipeBinding: vi.fn(),
}));

const shots = [
  { id: "shot-1", code: "SH-001", target_duration_ms: 3000, shot_type: "MEDIUM", status: "DRAFT", revision: 2, fields: { action: "turn" } },
  { id: "shot-2", code: "SH-002", target_duration_ms: 2000, shot_type: "WIDE", status: "DRAFT", revision: 1, fields: {} },
];

function renderWorkbench(projectId?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <StoryboardBatchWorkbench episodeId="episode-1" projectId={projectId} />
    </QueryClientProvider>,
  );
}

describe("StoryboardBatchWorkbench creator-safe structure editing", () => {
  beforeEach(() => {
    window.localStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
    vi.mocked(getStoryboardWorkspace).mockResolvedValue({
      storyboard: { items: shots, views: ["TABLE", "STORYBOARD", "TIMELINE"], identity_invariant: "stable", total_duration_ms: 5000 },
    } as never);
    vi.mocked(getShotEditContext).mockResolvedValue({
      episode_id: "episode-1",
      ordering_token: "order-token",
      items: shots.map(({ id, code, target_duration_ms, revision }, index) => ({ id, code, target_duration_ms, revision, order_key: String(index + 1) })),
    });
    vi.mocked(getShotGroupWorkspace).mockResolvedValue({
      episode: { id: "episode-1", code: "EP01", title: "Episode 1", project_id: "project-1" },
      groups: [], scenes: [], shots: [],
    });
    vi.mocked(getEpisodePlanAssets).mockResolvedValue([]);
    vi.mocked(planShotEdit).mockResolvedValue({
      plan_hash: "plan-hash",
      valid: true,
      issues: [],
      summary: { reordered: false, split: 1 },
      effects: { timeline: "STALE", selected_results: "UNCHANGED", asset_bindings: "COPIED" },
      ordered_shot_ids: ["shot-1", "shot-2"],
    });
    vi.mocked(planStoryboardBatch).mockResolvedValue({
      plan: {
        ordered_shot_ids: ["shot-1", "shot-2"], edits: [], copies: [], plan_hash: "e".repeat(64), valid: true, issues: [],
        summary: { reordered: 0, edited: 0, copied: 0 }, runtime_contacted: false, network_contacted: false,
      },
    } as never);
  });

  it("uses an inline ratio preview, automatic child codes, autosave, and undo", async () => {
    renderWorkbench();
    await screen.findByText("SH-001");
    fireEvent.click(screen.getByRole("button", { name: "全宽结构编排" }));
    expect(screen.getByRole("heading", { name: "重排、拆分与复制" })).toBeTruthy();

    fireEvent.change(screen.getByRole("combobox", { name: "拆分镜头" }), { target: { value: "shot-1" } });
    const ratio = screen.getByLabelText("拆分比例") as HTMLInputElement;
    expect(ratio.value).toBe("50");
    expect(screen.getByText("第一段编号").parentElement?.textContent).toContain("SH-001-A");
    expect(screen.getByText("第二段编号").parentElement?.textContent).toContain("SH-001-B");
    expect(screen.queryByLabelText("第一段编号")).toBeNull();

    fireEvent.change(ratio, { target: { value: "70" } });
    expect(screen.getByText("2.10 秒")).toBeTruthy();
    expect(screen.getByText("0.90 秒")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "加入拆分计划" }));
    await waitFor(() => expect(window.localStorage.getItem(storyboardDraftKey("episode-1"))).toContain('"first_duration_ms":2100'));

    fireEvent.click(screen.getByRole("button", { name: "预览重排 / 拆分" }));
    await waitFor(() => expect(planShotEdit).toHaveBeenCalledWith("episode-1", expect.objectContaining({
      ordering_token: "order-token",
      splits: [expect.objectContaining({ shot_id: "shot-1", first_code: "SH-001-A", second_code: "SH-001-B", first_duration_ms: 2100 })],
    })));

    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect(screen.getByRole("button", { name: "预览重排 / 拆分" })).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "重做" })).toHaveProperty("disabled", false);
  });

  it("restores a revision-matched episode draft without leaking it into server state", async () => {
    window.localStorage.setItem(storyboardDraftKey("episode-1"), JSON.stringify({
      version: 1,
      episodeId: "episode-1",
      sourceRevisions: { "shot-1": 2, "shot-2": 1 },
      orderedIds: ["shot-1", "shot-2"],
      edits: { "shot-1": { target_duration_ms: "4200", shot_type: "CLOSEUP" } },
      copies: [],
      pendingReorder: null,
      splits: [],
      copySource: "",
      copyCode: "",
      splitSource: "",
      splitAt: "",
      splitCodes: { first: "", second: "" },
      savedAt: "2026-08-24T00:00:00Z",
    }));
    renderWorkbench();

    expect(await screen.findByText("已恢复本集尚未提交的分镜编辑计划。")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "编辑详情" })[0]);
    expect((screen.getByLabelText("SH-001 时长") as HTMLInputElement).value).toBe("4200");
    expect((screen.getByLabelText("SH-001 类型") as HTMLSelectElement).value).toBe("CLOSEUP");
  });

  it("applies the bound Director Recipe to selected draft edits with immutable provenance", async () => {
    vi.mocked(getDirectorRecipeBinding).mockResolvedValue({
      id: "recipe-version-4",
      recipe_id: "recipe-1",
      recipe_version_id: "recipe-version-4",
      project_id: "project-1",
      code: "DRAMA_FAST",
      title: "快节奏",
      version_no: 4,
      recipe_hash: "f".repeat(64),
      reason: "approved",
      is_frozen: true,
      created_at: "2026-08-24T00:00:00Z",
      created_by: "local-user",
      revision: 1,
      recipe: {
        aspect_ratio: "9:16",
        shot_planning: { avg_duration_ms: 3600, dialogue_coverage: "CLOSEUP_INTIMATE" },
        asset_policy: { character_required_refs: [] },
        generation: { image: { capability: "IMAGE_CHARACTER" }, video: { capability: "VIDEO_I2V" } },
        qc_policy_ref: { policy_version_id: "qc-1" },
      },
    });
    renderWorkbench("project-1");
    await screen.findByText("SH-001");
    fireEvent.click(screen.getByLabelText("选择 SH-001"));
    fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
    expect(await screen.findByText(/DRAMA_FAST v4/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "套用到所选镜头草稿" }));
    fireEvent.click(screen.getByRole("button", { name: "全宽结构编排" }));
    fireEvent.click(screen.getByRole("button", { name: "校验字段 / 复制计划" }));

    await waitFor(() => expect(planStoryboardBatch).toHaveBeenCalledWith("episode-1", expect.objectContaining({
      edits: [expect.objectContaining({
        shot_id: "shot-1",
        target_duration_ms: 3600,
        shot_type: "CLOSEUP",
        fields: { director_recipe_application: expect.objectContaining({ recipe_version_id: "recipe-version-4", recipe_hash: "f".repeat(64), version_no: 4 }) },
      })],
    })));
  });
});
