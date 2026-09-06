import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { RuntimeEnvironmentsPanel } from "./RuntimeEnvironmentsPanel";
import * as runtimeClient from "./runtimeClient";

vi.mock("./runtimeClient", () => ({
  listRuntimeEnvironments: vi.fn().mockResolvedValue({ items: [{ id: "env-1", code: "comfy-production", title: "Comfy", status: "ACTIVE", version_count: 1 }] }),
  getRuntimeEnvironment: vi.fn().mockResolvedValue({ environment: { id: "env-1", code: "comfy-production", title: "Comfy", status: "ACTIVE" }, versions: [{ id: "env-v1", runtime_environment_id: "env-1", version_no: 1, status: "PUBLISHED", manifest: { mode: "EXTERNAL" }, environment_fingerprint: "a".repeat(64), validation: { status: "PASS", blockers: [] } }] }),
  runtimeStatus: vi.fn().mockResolvedValue({ observed_state: "RUNNING", reachable: true, instance: { id: "instance-1" }, health: {} }),
  createRuntimeEnvironment: vi.fn(),
  validateRuntimeVersion: vi.fn(),
  publishRuntimeVersion: vi.fn(),
  startRuntime: vi.fn(),
  stopRuntime: vi.fn(),
  createAppContract: vi.fn(),
  publishAppContract: vi.fn(),
  bindWorkflowRuntime: vi.fn(),
  listWorkflowAppContracts: vi.fn().mockResolvedValue({ items: [], binding: null }),
}));

describe("RuntimeEnvironmentsPanel", () => {
  it("loads the published bound contract after selecting a workflow", async () => {
    vi.mocked(runtimeClient.listWorkflowAppContracts).mockResolvedValueOnce({ items: [{ id: "contract-saved", status: "PUBLISHED", capability: "SHOT_KEYFRAME_SINGLE_FRAME", contract: { output_layout: "SINGLE_FRAME" }, bindings: { REFERENCE_IMAGE_3: { node_id: "13", input: "image" } }, semantic_phases: [] }], binding: { contract_version_id: "contract-saved", runtime_environment_version_id: "env-v1" } });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(<QueryClientProvider client={client}><RuntimeEnvironmentsPanel workflows={[{ id: "workflow-three", code: "qwen-three", status: "PUBLISHED" }]} /></QueryClientProvider>);
    fireEvent.change(screen.getByLabelText("Workflow 版本"), { target: { value: "workflow-three" } });
    await screen.findByText(/已绑定/);
    expect(screen.getByLabelText("能力")).toHaveValue("SHOT_KEYFRAME_SINGLE_FRAME");
    expect((screen.getByLabelText("语义输入绑定") as HTMLTextAreaElement).value).toContain("REFERENCE_IMAGE_3");
    expect(screen.getByRole("button", { name: "发布契约" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("能力"), { target: { value: "EDITED" } });
    expect(screen.getByRole("button", { name: "绑定 Workflow + Contract + Runtime" })).toBeDisabled();
    view.unmount();
  });
  it("fills the auditable single-frame contract without submitting it", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><RuntimeEnvironmentsPanel workflows={[{ id: "workflow-qwen", code: "qwen-image-2512-production", status: "PUBLISHED" }]} /></QueryClientProvider>);

    expect(await screen.findByText("Workflow App Contract")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "填充首尾帧单画幅契约模板" }));

    expect(screen.getByLabelText("能力")).toHaveValue("SHOT_KEYFRAME_SINGLE_FRAME");
    expect((screen.getByLabelText("输入 / 输出契约") as HTMLTextAreaElement).value).toContain('"output_layout": "SINGLE_FRAME"');
    expect((screen.getByLabelText("语义输入绑定") as HTMLTextAreaElement).value).toContain('"NEGATIVE_PROMPT"');
    expect((screen.getByLabelText("语义输入绑定") as HTMLTextAreaElement).value).toContain('"node_id": "6"');
    expect(vi.mocked(runtimeClient.createAppContract)).not.toHaveBeenCalled();
  });
});
