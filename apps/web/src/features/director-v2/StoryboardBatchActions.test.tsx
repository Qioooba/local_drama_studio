import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StoryboardBatchActions } from "./StoryboardBatchActions";

const api = vi.hoisted(() => ({
  getStoryboardWorkspace: vi.fn(),
  planStoryboardBatch: vi.fn(),
  commitStoryboardBatch: vi.fn(),
  planGeneration: vi.fn(),
  submitGeneration: vi.fn(),
}));

vi.mock("../../generated/api", () => ({
  getStoryboardWorkspace: api.getStoryboardWorkspace,
  planStoryboardBatch: api.planStoryboardBatch,
  commitStoryboardBatch: api.commitStoryboardBatch,
}));

vi.mock("./storyboardGenerationBatchApi", () => ({
  planStoryboardGenerationBatch: api.planGeneration,
  submitStoryboardGenerationBatch: api.submitGeneration,
}));

const items = [
  { id: "s1", code: "S01", revision: 2, fields: {}, order_key: "1" },
  { id: "s2", code: "S02", revision: 3, fields: {}, order_key: "2" },
  { id: "s3", code: "S03", revision: 4, fields: {}, order_key: "3" },
];

function renderActions(onChanged = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><StoryboardBatchActions episodeId="e1" selectedShotIds={["s1", "s2"]} onChanged={onChanged} /></QueryClientProvider>);
  fireEvent.click(screen.getByText("批量修饰与生成"));
  return onChanged;
}

describe("StoryboardBatchActions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getStoryboardWorkspace.mockResolvedValue({ storyboard: { episode: { id: "e1", title: "第一集" }, items, views: ["STORYBOARD"], identity_invariant: "stable", total_duration_ms: 12_000 } });
  });

  it("plans then commits prompt modifiers as new shot revisions", async () => {
    api.planStoryboardBatch.mockResolvedValue({ plan: { valid: true, plan_hash: "modifier-plan", issues: [] } });
    api.commitStoryboardBatch.mockResolvedValue({ result: {} });
    const onChanged = renderActions();
    fireEvent.change(screen.getByLabelText("统一提示词修饰符"), { target: { value: "雨夜，冷色调, 雨夜" } });
    fireEvent.click(screen.getByRole("button", { name: "检查修饰词" }));
    await screen.findByRole("button", { name: "确认应用到 2 镜" });

    expect(api.planStoryboardBatch).toHaveBeenCalledWith("e1", {
      ordered_shot_ids: ["s1", "s2", "s3"],
      edits: [
        { shot_id: "s1", expected_revision: 2, fields: { prompt_modifiers: ["雨夜", "冷色调"] } },
        { shot_id: "s2", expected_revision: 3, fields: { prompt_modifiers: ["雨夜", "冷色调"] } },
      ],
      copies: [],
    });
    fireEvent.click(screen.getByRole("button", { name: "确认应用到 2 镜" }));
    await waitFor(() => expect(api.commitStoryboardBatch).toHaveBeenCalledWith("e1", expect.any(Object), "modifier-plan"));
    expect(onChanged).toHaveBeenCalled();
    expect((await screen.findByRole("status")).textContent).toContain("新 revision");
  });

  it("requires a ready server plan before dispatching durable generation", async () => {
    api.planGeneration.mockResolvedValue({
      plan: {
        episode_id: "e1", project_id: "p1", targets: [], input_fingerprint: "fingerprint", plan_hash: "a".repeat(64), valid: true, issues: [],
        items: [], summary: { selected: 2, ready: 2, blocked: 0 },
      },
    });
    api.submitGeneration.mockResolvedValue({ batch: { selected_shot_ids: ["s1", "s2"], run_id: "run-1", workflow_id: "workflow-1", status: "RUNNING", task_count: 1, plan_hash: "a".repeat(64), idempotent_replay: false } });
    renderActions();
    fireEvent.click(screen.getByRole("button", { name: "检查批量生成" }));
    await screen.findByRole("button", { name: "确认新增 2 个 Take" });
    expect(api.planGeneration).toHaveBeenCalledWith("e1", [
      { shot_id: "s1", expected_revision: 2 },
      { shot_id: "s2", expected_revision: 3 },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "确认新增 2 个 Take" }));
    await waitFor(() => expect(api.submitGeneration).toHaveBeenCalledWith("e1", expect.any(Array), "a".repeat(64), expect.any(String)));
    expect((await screen.findByRole("status")).textContent).toContain("可恢复生成任务");
  });
});
