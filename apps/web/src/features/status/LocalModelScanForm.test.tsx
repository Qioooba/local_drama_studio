import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LocalModelScanForm } from "./LocalModelScanForm";
import * as api from "../../generated/api";

vi.mock("../../generated/api", () => ({ scanLocalModelRegistry: vi.fn() }));

describe("LocalModelScanForm", () => {
  beforeEach(() => vi.clearAllMocks());
  it("shows read-only local candidates and never presents an upload action", async () => {
    vi.mocked(api.scanLocalModelRegistry).mockResolvedValue({ scan: { root_path: "E:\\AI\\Models", items: [{ path: "E:\\AI\\Models\\demo_fp16.safetensors", relative_path: "demo_fp16.safetensors", extension: ".safetensors", byte_size: 1024, sha256: "a".repeat(64), quantization_hint: "FP16", distribution_scope: "REFERENCE_ONLY_NOT_BUNDLED", copied: false, uploaded: false }], scanned_count: 1, candidate_count: 1, truncated: false, max_files: 200, read_only: true, runtime_contacted: false, network_contacted: false, mutated: false } });
    render(<LocalModelScanForm />);
    fireEvent.change(screen.getByLabelText("模型目录绝对路径"), { target: { value: "E:\\AI\\Models" } });
    fireEvent.click(screen.getByRole("button", { name: "扫描本机目录" }));
    await waitFor(() => expect(screen.getByText(/只读扫描完成/)).toBeTruthy());
    expect(screen.getByText("demo_fp16.safetensors")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "上传" })).toBeNull();
  });
});
