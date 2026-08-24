import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ModelLicenseEvidenceForm } from "./ModelLicenseEvidenceForm";
import * as api from "../../generated/api";

vi.mock("../../generated/api", async () => {
  const actual = await vi.importActual<typeof import("../../generated/api")>("../../generated/api");
  return { ...actual, importModelLicenseEvidence: vi.fn(), listProjectLocalResources: vi.fn() };
});

const reports = [{ artifact_id: "artifact-1", code: "H3_VIDEO_VAE", kind: "H3 video VAE", machine_path_ref: "E:/model.safetensors", artifact_status: "CANDIDATE", artifact_sha256: "a".repeat(64), report_id: "report-1", report_sha256: "a".repeat(64), byte_size: 10, quantization: { status: "HEADER_MATCHED" }, license_status: "UNVERIFIED", report_status: "BLOCKED" as const, blockers: ["LICENSE_EVIDENCE_MISSING"], report_created_at: "2026-08-15", license_evidence_id: null, license_path_rel: null, has_report: true, has_license_evidence: false }];

describe("ModelLicenseEvidenceForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listProjectLocalResources).mockResolvedValue({ project_id: "project-1", kind: "LICENSE_EVIDENCE", items: [{ path_rel: "00_admin/licenses/h3.json", name: "h3.json", suffix: ".json", byte_size: 128 }], truncated: false, limit: 200, read_only: true, runtime_contacted: false, network_contacted: false, mutated: false });
  });

  const renderForm = (onImported: () => void) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}><ModelLicenseEvidenceForm projectId="project-1" reports={reports} onImported={onImported} /></QueryClientProvider>);
  };

  it("imports only explicit project-local license evidence and reports the resulting gate state", async () => {
    vi.mocked(api.importModelLicenseEvidence).mockResolvedValue({ evidence: { id: "evidence-1" }, report: { id: "report-2", model_artifact_id: "artifact-1", path_ref: "E:/model.safetensors", sha256: "a".repeat(64), byte_size: 10, header: {}, quantization: {}, license_status: "USER_OWNED", report_status: "PASS", blockers: [], runtime_contacted: false, network_contacted: false } });
    const changed = vi.fn();
    renderForm(changed);
    fireEvent.click(screen.getByRole("button", { name: "可选：记录用户授权信息" }));
    fireEvent.change(screen.getByRole("combobox", { name: "模型文件" }), { target: { value: "artifact-1" } });
    await screen.findByRole("option", { name: /h3\.json/ });
    fireEvent.change(screen.getByRole("combobox", { name: "项目内许可证证据" }), { target: { value: "00_admin/licenses/h3.json" } });
    fireEvent.change(screen.getByRole("textbox", { name: "许可证名称" }), { target: { value: "Commercial License" } });
    fireEvent.change(screen.getByRole("combobox", { name: /授权状态/ }), { target: { value: "USER_OWNED" } });
    fireEvent.click(screen.getByRole("button", { name: "校验并冻结证据" }));
    await waitFor(() => expect(api.importModelLicenseEvidence).toHaveBeenCalledWith("project-1", { model_artifact_id: "artifact-1", evidence_path: "00_admin/licenses/h3.json", license_name: "Commercial License", license_status: "USER_OWNED" }));
    expect((await screen.findByRole("status")).textContent).toContain("PASS");
    expect(changed).toHaveBeenCalledTimes(1);
  });

  it("does not import when explicit choices are missing", () => {
    renderForm(() => undefined);
    fireEvent.click(screen.getByRole("button", { name: "可选：记录用户授权信息" }));
    fireEvent.submit(screen.getByRole("button", { name: "校验并冻结证据" }).closest("form")!);
    expect(api.importModelLicenseEvidence).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("显式选择");
  });
});
