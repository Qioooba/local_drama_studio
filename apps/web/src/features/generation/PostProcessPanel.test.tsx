import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPostProcessRecipe, getBackgroundOperation, listPostProcessRecipes, listProjectLocalResources, planEnhancementRun, publishPostProcessRecipe, submitEnhancementRun } from "../../generated/api";
import { PostProcessPanel } from "./PostProcessPanel";

vi.mock("../../generated/api", () => ({ createPostProcessRecipe: vi.fn(), getBackgroundOperation: vi.fn(), listPostProcessRecipes: vi.fn(), listProjectLocalResources: vi.fn(), planEnhancementRun: vi.fn(), publishPostProcessRecipe: vi.fn(), submitEnhancementRun: vi.fn() }));

const recipe = { id: "recipe-1", code: "enhance", recipe_key: "enhance", title: "Enhance", version_no: 1, parent_recipe_id: null, recipe_hash: "a".repeat(64), status: "ACTIVE" as const, steps: [{ kind: "SCALE", width: 1920, height: 1080, fit: "CONTAIN", executor_ref: "builtin:ffmpeg" }, { kind: "TECHNICAL_QC", executor_ref: "builtin:ffprobe" }, { kind: "ENCODE", codec: "H264", preset: "veryfast", crf: 18, executor_ref: "builtin:ffmpeg" }], capability_contract: {} };
const video = { media_version_id: "video-1", media_asset_id: "asset-1", project_id: "project-1", episode_code: "EP03", shot_code: "S12", media_kind: "VIDEO", stage: "PROXY", decision: null, is_stale: null };

describe("PostProcessPanel", () => {
  beforeEach(() => {
    vi.mocked(listPostProcessRecipes).mockReset().mockResolvedValue({ items: [recipe] });
    vi.mocked(listProjectLocalResources).mockReset().mockResolvedValue({ project_id: "project-1", kind: "LUT", items: [{ path_rel: "00_admin/color/cinema.cube", name: "cinema.cube", suffix: ".cube", byte_size: 1024 }], truncated: false, limit: 200, read_only: true, runtime_contacted: false, network_contacted: false, mutated: false });
    vi.mocked(planEnhancementRun).mockReset().mockResolvedValue({ plan: { status: "READY", plan_hash: "b".repeat(64), snapshot: {}, command_preview: {}, would_create_run: false, would_overwrite_input: false, runtime_contacted: false, network_contacted: false, mutated: false } });
    vi.mocked(submitEnhancementRun).mockReset().mockResolvedValue({ job: { id: "enhancement-job-1", state: "QUEUED" } } as never);
    vi.mocked(getBackgroundOperation).mockReset().mockResolvedValue({ job: { id: "enhancement-job-1", state: "SUCCEEDED" }, result_type: "ENHANCEMENT", result: { id: "run-1", input_media_version_id: "video-1", recipe_id: "recipe-1", output_media_version_id: "video-2", status: "SUCCEEDED", plan_hash: "b".repeat(64), input_sha256: "c".repeat(64), output_sha256: "d".repeat(64), execution_snapshot: {}, qc: { passed: true }, bypass_comparison: { input_media_version_id: "video-1", output_media_version_id: "video-2", input_preserved: true } } } as never);
  });

  it("requires read-only planning before running and renders input/output comparison", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><PostProcessPanel videos={[video]} /></QueryClientProvider>);
    const run = await screen.findByRole("button", { name: "提交后台增强任务" }) as HTMLButtonElement;
    expect(screen.getByRole("option", { name: "EP03 · S12 · 预览代理 · 未审核" })).toBeTruthy();
    expect(document.body.textContent).not.toContain("VIDEO · video-1");
    expect(run.disabled).toBe(true);
    const plan = screen.getByRole("button", { name: "只读预检增强计划" }) as HTMLButtonElement;
    await waitFor(() => expect(plan.disabled).toBe(false));
    fireEvent.click(plan);
    await screen.findByText(/预检通过，尚未运行/);
    expect(run.disabled).toBe(false);
    fireEvent.click(run);
    await waitFor(() => expect(submitEnhancementRun).toHaveBeenCalledWith({ input_media_version_id: "video-1", recipe_id: "recipe-1", parameters: { requested_from: "POST_PROCESS_PANEL" }, plan_hash: "b".repeat(64) }));
    expect(await screen.findByLabelText("增强前后旁路比较")).toBeTruthy();
    expect(screen.getAllByRole("figure")).toHaveLength(2);
  });

  it("does not mark a server-canonicalized recipe dirty solely because object keys are reordered", async () => {
    vi.mocked(listPostProcessRecipes).mockResolvedValue({ items: [{ ...recipe, steps: [{ executor_ref: "builtin:ffmpeg", fit: "CONTAIN", height: 1080, kind: "SCALE", width: 1920 }, { executor_ref: "builtin:ffprobe", kind: "TECHNICAL_QC" }, { codec: "H264", crf: 18, executor_ref: "builtin:ffmpeg", kind: "ENCODE", preset: "veryfast" }] }] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><PostProcessPanel videos={[video]} /></QueryClientProvider>);
    const plan = await screen.findByRole("button", { name: "只读预检增强计划" }) as HTMLButtonElement;
    await waitFor(() => expect(plan.disabled).toBe(false));
    expect(screen.queryByText(/当前编辑尚未形成不可变版本/)).toBeNull();
  });

  it("derives a new immutable DRAFT from the selected recipe", async () => {
    vi.mocked(createPostProcessRecipe).mockResolvedValue({ recipe: { ...recipe, id: "recipe-2", code: "enhance@v2", version_no: 2, parent_recipe_id: "recipe-1", status: "DRAFT" } });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><PostProcessPanel videos={[video]} /></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "派生新的草稿版本" }));
    await waitFor(() => expect(createPostProcessRecipe).toHaveBeenCalledWith(expect.objectContaining({ code: "enhance", parent_recipe_id: "recipe-1" })));
    expect(publishPostProcessRecipe).not.toHaveBeenCalled();
  });

  it("adds optional local post-process steps to the immutable recipe draft", async () => {
    vi.mocked(createPostProcessRecipe).mockResolvedValue({ recipe: { ...recipe, id: "recipe-3", code: "enhance@v2", version_no: 2, parent_recipe_id: "recipe-1", status: "DRAFT" } });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><PostProcessPanel videos={[video]} /></QueryClientProvider>);
    const recipes = await screen.findByRole("combobox", { name: "已有版本" });
    await waitFor(() => expect((recipes as HTMLSelectElement).value).toBe("recipe-1"));
    await waitFor(() => expect((screen.getByRole("textbox", { name: "标题" }) as HTMLInputElement).value).toBe("Enhance"));
    fireEvent.click(await screen.findByRole("checkbox", { name: "降低画面噪点" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "稳定抖动画面" }));
    fireEvent.change(await screen.findByRole("combobox", { name: "项目调色文件（可选）" }), { target: { value: "00_admin/color/cinema.cube" } });
    expect(screen.getByText(/当前编辑尚未形成不可变版本/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "只读预检增强计划" })).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByRole("button", { name: "派生新的草稿版本" }));
    await waitFor(() => expect(createPostProcessRecipe).toHaveBeenCalledWith(expect.objectContaining({ steps: expect.arrayContaining([{ kind: "DENOISE", strength: 1, executor_ref: "builtin:ffmpeg" }, { kind: "STABILIZE", mode: "DESHAKE", executor_ref: "builtin:ffmpeg" }, { kind: "LUT_3D", path_rel: "00_admin/color/cinema.cube", executor_ref: "builtin:ffmpeg" }]) })));
  });

  it("keeps the explicit new-root selection instead of snapping back to the active recipe", async () => {
    vi.mocked(createPostProcessRecipe).mockResolvedValue({ recipe: { ...recipe, id: "recipe-new", code: "uat-new-root", recipe_key: "uat-new-root", version_no: 1, parent_recipe_id: null, status: "DRAFT" } });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><PostProcessPanel videos={[video]} /></QueryClientProvider>);
    const recipes = await screen.findByRole("combobox", { name: "已有版本" });
    await waitFor(() => expect((recipes as HTMLSelectElement).value).toBe("recipe-1"));
    fireEvent.change(recipes, { target: { value: "" } });
    await waitFor(() => expect((recipes as HTMLSelectElement).value).toBe(""));
    fireEvent.change(screen.getByRole("textbox", { name: "标题" }), { target: { value: "UAT New Root" } });
    expect(screen.getByText("配方技术标识").parentElement?.textContent).toContain("recipe-uat-new-root");
    fireEvent.click(screen.getByRole("button", { name: "创建第 1 版草稿" }));
    await waitFor(() => expect(createPostProcessRecipe).toHaveBeenCalledWith(expect.objectContaining({ code: "recipe-uat-new-root", parent_recipe_id: undefined })));
  });
});
