import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as api from "../../generated/api";
import { LocalModelReferenceForm } from "./LocalModelReferenceForm";

vi.mock("../../generated/api", () => ({
  registerGlobalModelReference: vi.fn(),
  createGlobalModelCompatibilityReport: vi.fn(),
  pickLocalModelFile: vi.fn(),
  scanLocalModelRegistry: vi.fn(),
  getClientCapabilities: vi.fn(),
  listModelLibraryRoots: vi.fn(),
}));

function renderForm(onRegistered = () => undefined) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><LocalModelReferenceForm onRegistered={onRegistered} /></QueryClientProvider>);
}

const capabilities = {
  capabilities: {
    network_mode: "LOCAL_ONLY" as const,
    trusted_lan_unauthenticated: false,
    security_warning: null,
    client_location: "SERVER_LOOPBACK" as const,
    server_file_dialogs: true,
    browser_uploads: true,
    browser_downloads: true,
    model_library_roots: [],
    upload_limits_mb: {},
  },
};

describe("LocalModelReferenceForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getClientCapabilities).mockResolvedValue(capabilities);
    vi.mocked(api.listModelLibraryRoots).mockResolvedValue({ items: [], configured: false, read_only: true });
    vi.mocked(api.registerGlobalModelReference).mockResolvedValue({ artifact: { id: "model-1", code: "MODEL_MODEL_T2V", kind: "T2V", machine_path_ref: "E:\\AI\\model_t2v.safetensors", status: "CANDIDATE", distribution_scope: "REFERENCE_ONLY_NOT_BUNDLED", copied: false, uploaded: false } });
    vi.mocked(api.createGlobalModelCompatibilityReport).mockResolvedValue({ report: { id: "report-1", model_artifact_id: "model-1", path_ref: "E:\\AI\\model_t2v.safetensors", sha256: "a".repeat(64), byte_size: 1, header: {}, quantization: {}, license_status: "UNVERIFIED_NO_LOCAL_LICENSE_EVIDENCE", report_status: "PASS", blockers: [], license_risk: "USER_RESPONSIBILITY_UNKNOWN", distribution_scope: "REFERENCE_ONLY_NOT_BUNDLED", runtime_contacted: false, network_contacted: false } });
  });

  it("adds a picked file to the shared model library and infers its likely use", async () => {
    vi.mocked(api.pickLocalModelFile).mockResolvedValue({ selection: { selected: true, path: "E:\\AI\\model_t2v.safetensors", uploaded: false, copied: false } });
    const onRegistered = vi.fn();
    renderForm(onRegistered);

    fireEvent.click(screen.getByRole("button", { name: "添加本机模型" }));
    fireEvent.click(await screen.findByRole("button", { name: "选择单个模型文件" }));
    expect(await screen.findByText("model_t2v")).toBeTruthy();
    expect((screen.getByLabelText("主要用途") as HTMLSelectElement).value).toBe("T2V");

    fireEvent.click(screen.getByRole("button", { name: "添加到模型库" }));
    await waitFor(() => expect(api.registerGlobalModelReference).toHaveBeenCalledWith(expect.objectContaining({
      code: "MODEL_MODEL_T2V",
      kind: "T2V",
      machine_path_ref: "E:\\AI\\model_t2v.safetensors",
    })));
    expect(api.createGlobalModelCompatibilityReport).toHaveBeenCalledWith("model-1", "T2V");
    expect(await screen.findByText(/离线兼容性检查：通过/)).toBeTruthy();
    expect(onRegistered).toHaveBeenCalled();
  });

  it("reads a configured model folder and asks for confirmation when the filename is ambiguous", async () => {
    vi.mocked(api.listModelLibraryRoots).mockResolvedValue({
      items: [{ id: "root-1", label: "ComfyUI 模型", path: "E:\\ComfyUI\\models" }],
      configured: true,
      read_only: true,
    });
    vi.mocked(api.scanLocalModelRegistry).mockResolvedValue({ scan: {
      root_path: "E:\\ComfyUI\\models",
      items: [{ path: "E:\\ComfyUI\\models\\checkpoints\\cinematic.safetensors", relative_path: "checkpoints/cinematic.safetensors", extension: ".safetensors", byte_size: 1024, sha256: "b".repeat(64), quantization_hint: "UNKNOWN", distribution_scope: "REFERENCE_ONLY_NOT_BUNDLED", copied: false, uploaded: false }],
      scanned_count: 1,
      candidate_count: 1,
      truncated: false,
      max_files: 200,
      read_only: true,
      runtime_contacted: false,
      network_contacted: false,
      mutated: false,
    } });
    renderForm();

    fireEvent.click(screen.getByRole("button", { name: "添加本机模型" }));
    fireEvent.click(await screen.findByRole("button", { name: "读取模型文件夹" }));
    expect(await screen.findByText("找到 1 个模型")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /checkpoints\/cinematic\.safetensors/ }));

    expect((screen.getByLabelText("主要用途") as HTMLSelectElement).value).toBe("");
    expect(screen.getByText("系统无法仅凭文件名判断，请选择最接近的用途。")).toBeTruthy();
    expect((screen.getByRole("button", { name: "添加到模型库" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
