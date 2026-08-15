import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../generated/api";
import { LocalModelReferenceForm } from "./LocalModelReferenceForm";

vi.mock("../../generated/api", () => ({ registerLocalModelReference: vi.fn(), createModelCompatibilityReport: vi.fn(), pickLocalModelFile: vi.fn() }));

describe("LocalModelReferenceForm", () => {
  beforeEach(() => vi.clearAllMocks());

  it("references an absolute local path and reports that weights were not copied", async () => {
    vi.mocked(api.registerLocalModelReference).mockResolvedValue({ artifact: { id: "model-1", code: "my-model", kind: "T2V", machine_path_ref: "E:\\AI\\model.safetensors", status: "CANDIDATE", distribution_scope: "REFERENCE_ONLY_NOT_BUNDLED", copied: false, uploaded: false } });
    vi.mocked(api.createModelCompatibilityReport).mockResolvedValue({ report: { id: "report-1", model_artifact_id: "model-1", path_ref: "E:\\AI\\model.safetensors", sha256: "a".repeat(64), byte_size: 1, header: {}, quantization: {}, license_status: "UNVERIFIED_NO_LOCAL_LICENSE_EVIDENCE", report_status: "PASS", blockers: [], license_risk: "USER_RESPONSIBILITY_UNKNOWN", distribution_scope: "REFERENCE_ONLY_NOT_BUNDLED", runtime_contacted: false, network_contacted: false } });
    const onRegistered = vi.fn();
    render(<LocalModelReferenceForm projectId="project-1" onRegistered={onRegistered} />);
    fireEvent.click(screen.getByRole("button", { name: "添加电脑里的模型" }));
    fireEvent.change(screen.getByLabelText("模型代码"), { target: { value: "my-model" } });
    fireEvent.change(screen.getByLabelText("模型类型"), { target: { value: "T2V" } });
    fireEvent.change(screen.getByLabelText("电脑中的模型绝对路径"), { target: { value: "E:\\AI\\model.safetensors" } });
    fireEvent.click(screen.getByRole("button", { name: "引用并检查兼容性" }));
    await waitFor(() => expect(api.registerLocalModelReference).toHaveBeenCalled());
    expect(await screen.findByText(/未复制或上传权重/)).toBeTruthy();
    expect(onRegistered).toHaveBeenCalled();
  });

  it("uses the native local picker without uploading the selected file", async () => {
    vi.mocked(api.pickLocalModelFile).mockResolvedValue({ selection: { selected: true, path: "E:\\AI\\picked.safetensors", uploaded: false, copied: false } });
    render(<LocalModelReferenceForm projectId="project-1" onRegistered={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "添加电脑里的模型" }));
    fireEvent.click(screen.getByRole("button", { name: "浏览…" }));
    await waitFor(() => expect(screen.getByLabelText("电脑中的模型绝对路径")).toHaveProperty("value", "E:\\AI\\picked.safetensors"));
  });
});
