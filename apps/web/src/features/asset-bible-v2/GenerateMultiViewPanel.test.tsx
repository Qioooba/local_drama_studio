import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listCapabilityOptions, retryJob } from "../../generated/api";
import { capabilityOptionFixture, capabilityOptionsFixture } from "../model-config/capabilityOptionsTestFixtures";
import { GenerateMultiViewPanel } from "./GenerateMultiViewPanel";
import { bindMultiViewReference, draftAssetMultiViewPrompts, preflightAssetMultiView, submitAssetMultiView } from "./multiviewClient";

vi.mock("../../generated/api", () => ({ listCapabilityOptions: vi.fn(), getProfileVersion: vi.fn(), retryJob: vi.fn() }));
vi.mock("./multiviewClient", () => ({
  bindMultiViewReference: vi.fn(),
  collectMultiViewOutput: vi.fn().mockResolvedValue(undefined),
  draftAssetMultiViewPrompts: vi.fn(),
  getAssetMultiViewHistory: vi.fn().mockResolvedValue([]),
  isMultiViewBatchActive: vi.fn().mockReturnValue(false),
  preflightAssetMultiView: vi.fn(),
  submitAssetMultiView: vi.fn(),
}));

const hero = {
  id: "ref-1", project_id: "project-1", story_asset_id: "asset-1", asset_state_id: null,
  media_version_id: "media-1", reference_kind: "HERO", label: "主参考", priority: 100,
  is_locked: true, yaw_deg: null, pitch_deg: null, status: "ACTIVE", revision: 1,
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<MemoryRouter><QueryClientProvider client={client}><GenerateMultiViewPanel projectId="project-1" assetId="asset-1" assetKind="CHARACTER" assetStatus="ACTIVE" states={[]} baseReferences={[hero]} initialBatches={[]} onReferencesChanged={vi.fn().mockResolvedValue(undefined)} /></QueryClientProvider></MemoryRouter>);
}

describe("GenerateMultiViewPanel profile selection", () => {
  it("retries the failed view's existing job without submitting a new generation", async () => {
    vi.mocked(retryJob).mockResolvedValue({} as never);
    vi.mocked(listCapabilityOptions).mockResolvedValue(capabilityOptionsFixture("IMAGE_MULTI_VIEW", []));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<MemoryRouter><QueryClientProvider client={client}><GenerateMultiViewPanel
      projectId="project-1" assetId="asset-1" assetKind="CHARACTER" assetStatus="ACTIVE" states={[]} baseReferences={[hero]}
      initialBatches={[{ intent_id: "batch-1", status: "FAILED", created_at: "2026-09-06T00:00:00Z", completed_count: 0, failed_count: 1, total_count: 1,
        items: [{ reference_kind: "LEFT", yaw_deg: -90, variant_id: "variant-1", variant_no: 1, variant_status: "FAILED", job_id: "existing-job", job_state: "FAILED", progress: {}, error: {code: "GPU_RUNTIME_BUSY", detail: "显卡忙"}, outputs: [] }],
      }]} onReferencesChanged={vi.fn()} />
    </QueryClientProvider></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "重试左侧任务" }));
    await waitFor(() => expect(retryJob).toHaveBeenCalledWith("existing-job"));
    await waitFor(() => expect(screen.queryByRole("button", { name: "重试左侧任务" })).toBeNull());
    expect(submitAssetMultiView).not.toHaveBeenCalled();
  });
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(preflightAssetMultiView).mockResolvedValue({ preflight: {
      asset_id: "asset-1", project_id: "project-1", capability: "IMAGE_MULTI_VIEW", status: "READY", ready: true,
      blockers: [], hero: null, profile_resolution: { profile_version_id: null, input_role: null }, views: [], plan_hash: "plan-1",
      would_persist_intent: false, would_create_variants: 3, would_create_jobs: 3,
    } });
    vi.mocked(draftAssetMultiViewPrompts).mockResolvedValue({ prompt_bundle: {
      schema_version: "localdrama.asset-multiview-prompts.v1", source: "LOCAL_LLM", provider: "llama.cpp", model: "local-model", content_hash: "a".repeat(64),
      items: Object.fromEntries(["FRONT", "LEFT", "RIGHT"].map((kind) => [kind, { positive_prompt: `${kind} positive generated prompt`, negative_prompt: `${kind} negative generated prompt` }])),
    } });
  });

  it("lists only published IMAGE_MULTI_VIEW versions with semantic labels and keeps AUTO default", async () => {
    vi.mocked(listCapabilityOptions).mockResolvedValue(capabilityOptionsFixture("IMAGE_MULTI_VIEW", [
      capabilityOptionFixture("IMAGE_MULTI_VIEW", "version-secret-1", "角色三视图", "角色三视图配置", 4),
    ]));
    renderPanel();

    const select = await screen.findByLabelText("多视图生成方式");
    await waitFor(() => expect((select as HTMLSelectElement).disabled).toBe(false));
    expect(screen.getByRole("option", { name: /自动：角色三视图/ })).toBeTruthy();
    expect(screen.getByRole("option", { name: /角色三视图.*角色三视图配置 v4/ })).toBeTruthy();
    expect(screen.queryByRole("option", { name: /草稿|视频/ })).toBeNull();
    expect(document.body.textContent).not.toContain("version-secret");

    fireEvent.change(select, { target: { value: "version-secret-1" } });
    fireEvent.click(screen.getByRole("button", { name: "AI 生成本批 3 组正反提示词" }));
    await screen.findByText("FRONT positive generated prompt");
    fireEvent.click(screen.getByRole("button", { name: "预检 3 个缺失视图" }));
    await waitFor(() => expect(preflightAssetMultiView).toHaveBeenCalledWith("asset-1", expect.objectContaining({ profile_version_id: "version-secret-1", requested_slots: ["FRONT", "LEFT", "RIGHT"], prompt_bundle: expect.objectContaining({ source: "LOCAL_LLM" }) })));
  });

  it("explains catalogue failure without blocking AUTO preflight", async () => {
    vi.mocked(listCapabilityOptions).mockRejectedValue(new Error("offline"));
    renderPanel();
    expect((await screen.findByRole("alert")).textContent).toContain("能力选项读取失败");
    expect((screen.getByRole("button", { name: "预检 3 个缺失视图" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "AI 生成本批 3 组正反提示词" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "预检 3 个缺失视图" }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("requires fresh prompts and preflight after a reviewer changes the correction", async () => {
    vi.mocked(listCapabilityOptions).mockResolvedValue(capabilityOptionsFixture("IMAGE_MULTI_VIEW", []));
    renderPanel();
    const guidance = screen.getByRole("textbox", { name: /本批修订要求/ });
    fireEvent.change(guidance, { target: { value: "保持齐下巴短发，不能变成长发" } });
    fireEvent.click(screen.getByRole("button", { name: "AI 生成本批 3 组正反提示词" }));
    await screen.findByText("FRONT positive generated prompt");
    expect(draftAssetMultiViewPrompts).toHaveBeenCalledWith("asset-1", expect.objectContaining({ revision_guidance: "保持齐下巴短发，不能变成长发" }));
    fireEvent.click(screen.getByRole("button", { name: "预检 3 个缺失视图" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "确认生成 3 个缺失视图" }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.change(guidance, { target: { value: "同时保持原服装" } });
    expect(screen.queryByText("FRONT positive generated prompt")).toBeNull();
    expect((screen.getByRole("button", { name: "确认生成 3 个缺失视图" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "预检 3 个缺失视图" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("bulk-binds every successful unbound output and reports partial failures", async () => {
    vi.mocked(listCapabilityOptions).mockResolvedValue(capabilityOptionsFixture("IMAGE_MULTI_VIEW", []));
    const onChanged = vi.fn().mockResolvedValue(undefined);
    vi.mocked(bindMultiViewReference)
      .mockResolvedValueOnce({ reference: {} as never })
      .mockRejectedValueOnce(new Error("RIGHT 绑定冲突"));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<MemoryRouter><QueryClientProvider client={client}><GenerateMultiViewPanel
      projectId="project-1"
      assetId="asset-1"
      assetKind="CHARACTER"
      assetStatus="ACTIVE"
      states={[]}
      baseReferences={[hero]}
      initialBatches={[{
        intent_id: "batch-1", status: "SUCCEEDED", created_at: "2026-08-24T00:00:00Z", completed_count: 2, failed_count: 0, total_count: 2,
        items: ["LEFT", "RIGHT"].map((kind, index) => ({
          reference_kind: kind, yaw_deg: index ? 90 : -90, variant_id: `variant-${index}`, variant_no: index + 1,
          variant_status: "SUCCEEDED", job_id: `job-${index}`, job_state: "SUCCEEDED", progress: { percent: 100 }, error: null,
          outputs: [{ media_version_id: `media-${kind.toLowerCase()}`, source_artifact_id: `artifact-${index}`, rel_path: `${kind}.png`, mime_type: "image/png", sha256: "a".repeat(64), integrity_status: "VERIFIED" }],
        })),
      }] as never}
      onReferencesChanged={onChanged}
    /></QueryClientProvider></MemoryRouter>);

    fireEvent.click(await screen.findByRole("button", { name: "一键回绑 2 个成功视图" }));
    await waitFor(() => expect(bindMultiViewReference).toHaveBeenCalledTimes(2));
    expect(bindMultiViewReference).toHaveBeenNthCalledWith(1, "asset-1", null, "LEFT", "media-left");
    expect(bindMultiViewReference).toHaveBeenNthCalledWith(2, "asset-1", null, "RIGHT", "media-right");
    expect(await screen.findByText("回绑结果：成功 1 · 失败 1")).toBeTruthy();
    expect(screen.getByText("RIGHT：RIGHT 绑定冲突")).toBeTruthy();
    expect(onChanged).toHaveBeenCalledTimes(1);
  });
});
