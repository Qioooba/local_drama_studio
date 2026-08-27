import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as api from "../../generated/api";
import { LocalModelReferenceForm } from "./LocalModelReferenceForm";

vi.mock("../../generated/api", () => ({ registerLocalModelReference: vi.fn(), createModelCompatibilityReport: vi.fn(), pickLocalModelFile: vi.fn(), scanLocalModelRegistry: vi.fn(), getClientCapabilities: vi.fn(), listModelLibraryRoots: vi.fn() }));

function renderForm(onRegistered = () => undefined) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><LocalModelReferenceForm projectId="project-1" onRegistered={onRegistered} /></QueryClientProvider>);
}

describe("LocalModelReferenceForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getClientCapabilities).mockResolvedValue({ capabilities: { network_mode: "LOCAL_ONLY", client_location: "SERVER_LOOPBACK", server_file_dialogs: true, browser_uploads: true, browser_downloads: true, model_library_roots: [], upload_limits_mb: {} } });
    vi.mocked(api.listModelLibraryRoots).mockResolvedValue({ items: [], configured: false, read_only: true });
  });

  it("references an absolute local path and reports that weights were not copied", async () => {
    vi.mocked(api.registerLocalModelReference).mockResolvedValue({ artifact: { id: "model-1", code: "my-model", kind: "T2V", machine_path_ref: "E:\\AI\\model.safetensors", status: "CANDIDATE", distribution_scope: "REFERENCE_ONLY_NOT_BUNDLED", copied: false, uploaded: false } });
    vi.mocked(api.createModelCompatibilityReport).mockResolvedValue({ report: { id: "report-1", model_artifact_id: "model-1", path_ref: "E:\\AI\\model.safetensors", sha256: "a".repeat(64), byte_size: 1, header: {}, quantization: {}, license_status: "UNVERIFIED_NO_LOCAL_LICENSE_EVIDENCE", report_status: "PASS", blockers: [], license_risk: "USER_RESPONSIBILITY_UNKNOWN", distribution_scope: "REFERENCE_ONLY_NOT_BUNDLED", runtime_contacted: false, network_contacted: false } });
    vi.mocked(api.pickLocalModelFile).mockResolvedValue({ selection: { selected: true, path: "E:\\AI\\model.safetensors", uploaded: false, copied: false } });
    const onRegistered = vi.fn();
    renderForm(onRegistered);
    fireEvent.click(screen.getByRole("button", { name: "添加服务端模型" }));
    expect(screen.queryByLabelText("模型代码")).toBeNull();
    fireEvent.change(screen.getByLabelText("这个模型用来做什么？"), { target: { value: "T2V" } });
    fireEvent.click(await screen.findByRole("button", { name: "从服务器桌面选择" }));
    await screen.findByText("model");
    fireEvent.click(screen.getByRole("button", { name: "添加并检查模型" }));
    await waitFor(() => expect(api.registerLocalModelReference).toHaveBeenCalledWith("project-1", expect.objectContaining({
      code: "MODEL_MODEL",
      kind: "T2V",
      machine_path_ref: "E:\\AI\\model.safetensors",
    })));
    expect(api.createModelCompatibilityReport).toHaveBeenCalledWith("project-1", "model-1", "T2V");
    expect(await screen.findByText(/未复制或上传权重/)).toBeTruthy();
    expect(onRegistered).toHaveBeenCalled();
  });

  it("uses the native local picker without uploading the selected file", async () => {
    vi.mocked(api.pickLocalModelFile).mockResolvedValue({ selection: { selected: true, path: "E:\\AI\\picked.safetensors", uploaded: false, copied: false } });
    renderForm();
    fireEvent.click(screen.getByRole("button", { name: "添加服务端模型" }));
    fireEvent.click(await screen.findByRole("button", { name: "从服务器桌面选择" }));
    expect(await screen.findByText("picked")).toBeTruthy();
    expect(screen.getByTitle("E:\\AI\\picked.safetensors")).toBeTruthy();
  });
});
