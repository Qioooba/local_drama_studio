import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ComfyLabPanel } from "./ComfyLabPanel";
import * as api from "../../generated/api";

vi.mock("../../generated/api", async () => {
  const actual = await vi.importActual<typeof import("../../generated/api")>("../../generated/api");
  return { ...actual, getComfyLabStatus: vi.fn(), discoverComfyLab: vi.fn(), startComfyLab: vi.fn(), stopComfyLab: vi.fn(), restartComfyLab: vi.fn(), captureComfyLabWorkflow: vi.fn(), createComfyLabTestRun: vi.fn() };
});

const stopped = { status: "STOPPED" as const, pid: null, session_id: null, started_at: null, endpoint: "http://127.0.0.1:8188", launch_configured: false, sandbox_root: "work/comfy-lab", formal_project_write: false as const, local_only: true as const, network_contacted: false as const, stale_state: false };

describe("ComfyLabPanel", () => {
  beforeEach(() => {
    vi.mocked(api.getComfyLabStatus).mockResolvedValue({ status: stopped });
    vi.mocked(api.createComfyLabTestRun).mockResolvedValue({ test_run: { status: "BLOCKED" } as never });
  });
  it("shows truthful not-configured state and sandbox safety", async () => {
    render(<ComfyLabPanel />);
    expect(await screen.findByText("ComfyUI 实验室")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("启动配置：未配置")).toBeTruthy());
    expect(screen.getByText(/正式目录写入：否/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "生成测试计划" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByRole("button", { name: "自动检测并配置" })).toBeTruthy();
  });

  it("discovers, persists, and refreshes a local installation on explicit action", async () => {
    const configured = { ...stopped, launch_configured: true };
    vi.mocked(api.getComfyLabStatus).mockResolvedValueOnce({ status: stopped }).mockResolvedValueOnce({ status: configured });
    vi.mocked(api.discoverComfyLab).mockResolvedValue({ discovery: { status: "CONFIGURED", applied: true, candidates: [{ python_path: "C:/ComfyUI/python_embeded/python.exe", root_path: "C:/ComfyUI", port: 8188 }], configuration: { configured: true, python: "C:/ComfyUI/python_embeded/python.exe", root: "C:/ComfyUI", port: 8188, endpoint: "http://127.0.0.1:8188", source: "SAVED" }, searched_roots: ["C:/ComfyUI"], runtime_contacted: false, network_contacted: false } });
    render(<ComfyLabPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "自动检测并配置" }));
    expect(await screen.findByText(/已检测并保存本机 ComfyUI 启动配置/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText("启动配置：已配置")).toBeTruthy());
    expect(api.discoverComfyLab).toHaveBeenCalledWith({ apply: true });
  });
});
