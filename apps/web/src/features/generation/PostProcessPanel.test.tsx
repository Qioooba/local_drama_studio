import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPostProcessRecipe, listPostProcessRecipes, planEnhancementRun, publishPostProcessRecipe, runEnhancement } from "../../generated/api";
import { PostProcessPanel } from "./PostProcessPanel";

vi.mock("../../generated/api", () => ({ createPostProcessRecipe: vi.fn(), listPostProcessRecipes: vi.fn(), planEnhancementRun: vi.fn(), publishPostProcessRecipe: vi.fn(), runEnhancement: vi.fn() }));

const recipe = { id: "recipe-1", code: "enhance", recipe_key: "enhance", title: "Enhance", version_no: 1, parent_recipe_id: null, recipe_hash: "a".repeat(64), status: "ACTIVE" as const, steps: [], capability_contract: {} };
const video = { media_version_id: "video-1", media_asset_id: "asset-1", project_id: "project-1", media_kind: "VIDEO", stage: "PROXY", decision: null, is_stale: null };

describe("PostProcessPanel", () => {
  beforeEach(() => {
    vi.mocked(listPostProcessRecipes).mockReset().mockResolvedValue({ items: [recipe] });
    vi.mocked(planEnhancementRun).mockReset().mockResolvedValue({ plan: { status: "READY", plan_hash: "b".repeat(64), snapshot: {}, command_preview: {}, would_create_run: false, would_overwrite_input: false, runtime_contacted: false, network_contacted: false, mutated: false } });
    vi.mocked(runEnhancement).mockReset().mockResolvedValue({ enhancement: { id: "run-1", input_media_version_id: "video-1", recipe_id: "recipe-1", output_media_version_id: "video-2", status: "SUCCEEDED", plan_hash: "b".repeat(64), input_sha256: "c".repeat(64), output_sha256: "d".repeat(64), execution_snapshot: {}, qc: { passed: true }, bypass_comparison: { input_media_version_id: "video-1", output_media_version_id: "video-2", input_preserved: true } } });
  });

  it("requires read-only planning before running and renders input/output comparison", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><PostProcessPanel videos={[video]} /></QueryClientProvider>);
    const run = await screen.findByRole("button", { name: "确认运行并注册新版本" }) as HTMLButtonElement;
    expect(run.disabled).toBe(true);
    const plan = screen.getByRole("button", { name: "只读预检增强计划" }) as HTMLButtonElement;
    await waitFor(() => expect(plan.disabled).toBe(false));
    fireEvent.click(plan);
    await screen.findByText(/READY，尚未运行/);
    expect(run.disabled).toBe(false);
    fireEvent.click(run);
    await waitFor(() => expect(runEnhancement).toHaveBeenCalledWith({ input_media_version_id: "video-1", recipe_id: "recipe-1", parameters: { requested_from: "POST_PROCESS_PANEL" }, plan_hash: "b".repeat(64) }));
    expect(await screen.findByLabelText("增强前后旁路比较")).toBeTruthy();
    expect(screen.getAllByRole("figure")).toHaveLength(2);
  });

  it("derives a new immutable DRAFT from the selected recipe", async () => {
    vi.mocked(createPostProcessRecipe).mockResolvedValue({ recipe: { ...recipe, id: "recipe-2", code: "enhance@v2", version_no: 2, parent_recipe_id: "recipe-1", status: "DRAFT" } });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><PostProcessPanel videos={[video]} /></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "派生 DRAFT 新版本" }));
    await waitFor(() => expect(createPostProcessRecipe).toHaveBeenCalledWith(expect.objectContaining({ code: "enhance", parent_recipe_id: "recipe-1" })));
    expect(publishPostProcessRecipe).not.toHaveBeenCalled();
  });

  it("adds optional local post-process steps to the immutable recipe draft", async () => {
    vi.mocked(createPostProcessRecipe).mockResolvedValue({ recipe: { ...recipe, id: "recipe-3", code: "enhance@v2", version_no: 2, parent_recipe_id: "recipe-1", status: "DRAFT" } });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><PostProcessPanel videos={[video]} /></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("checkbox", { name: "降噪 DENOISE" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "防抖 STABILIZE（deshake）" }));
    fireEvent.click(screen.getByRole("button", { name: "派生 DRAFT 新版本" }));
    await waitFor(() => expect(createPostProcessRecipe).toHaveBeenCalledWith(expect.objectContaining({ steps: expect.arrayContaining([{ kind: "DENOISE", strength: 1, executor_ref: "builtin:ffmpeg" }, { kind: "STABILIZE", mode: "DESHAKE", executor_ref: "builtin:ffmpeg" }]) })));
  });
});
