import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { DiagnosticsOverview, isDiagnosticRunStale } from "./DiagnosticsOverview";
import type { DiagnosticRun } from "../../generated/api";

function run(overrides: Partial<DiagnosticRun> = {}): DiagnosticRun {
  return {
    id: "diag-1",
    status: "HEALTHY",
    created_at: "2026-08-29T02:00:00Z",
    checks: [
      { code: "FFMPEG", category: "media", status: "PASS", observed: { version: "ffmpeg version 8.1" } },
      { code: "GPU_MANIFEST", category: "gpu", status: "PASS", observed: { name: "RTX 3090 Ti", total_bytes: 24564 * 1024 ** 2, source: "NVIDIA_SMI" } },
    ],
    ...overrides,
  };
}

describe("DiagnosticsOverview", () => {
  it("leads with production readiness and keeps passed evidence collapsed", () => {
    render(<DiagnosticsOverview run={run()} running={false} onRun={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "生产环境可用" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "需要处理的事项" })).not.toBeInTheDocument();
    expect(screen.getByText("已通过的检查").closest("details")).not.toHaveAttribute("open");
  });

  it("shows only actionable checks in the open issue region", () => {
    render(<DiagnosticsOverview run={run({
      status: "DEGRADED",
      checks: [
        { code: "COMFYUI_LOOPBACK", category: "runtime", status: "BLOCKED", observed: { reason: "ConnectionRefusedError" }, remediation: { action: "restart" } },
        { code: "FFMPEG", category: "media", status: "PASS", observed: { version: "ffmpeg version 8.1" } },
      ],
    })} running={false} onRun={vi.fn()} />);
    const issueRegion = screen.getByRole("heading", { name: "需要处理的事项" }).closest("section");
    expect(issueRegion).toHaveTextContent("图像与视频生成服务");
    expect(issueRegion).toHaveTextContent("启动已配置的 ComfyUI 服务");
    expect(issueRegion).not.toHaveTextContent("视频处理");
  });

  it("does not label production healthy when queued work has no executor", () => {
    render(<DiagnosticsOverview run={run({
      status: "DEGRADED",
      checks: [{ code: "LOCAL_EXECUTOR", category: "runtime", status: "BLOCKED", observed: { queued_count: 1, active_worker_count: 0, active_attempt_count: 0 } }],
    })} running={false} onRun={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "有 1 项影响生产" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "本机任务执行器" })).toBeInTheDocument();
    expect(screen.getByText("有 1 个任务排队，但没有可用执行器")).toBeInTheDocument();
  });

  it("does not turn missing GPU capacity into a false zero", () => {
    render(<DiagnosticsOverview run={run({
      status: "DEGRADED",
      checks: [{ code: "GPU_MANIFEST", category: "gpu", status: "WARN", observed: { name: "RTX 3090 Ti", total_bytes: null } }],
    })} running={false} onRun={vi.fn()} />);
    expect(screen.getByText(/RTX 3090 Ti；显存 未知/)).toBeInTheDocument();
    expect(screen.queryByText(/0\.0 GB/)).not.toBeInTheDocument();
  });

  it("retains the last result while a new check is running", () => {
    const onRun = vi.fn();
    render(<DiagnosticsOverview run={run()} running onRun={onRun} />);
    expect(screen.getByRole("heading", { name: "生产环境可用" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("正在检查");
    expect(screen.getByRole("button", { name: "检查中…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "检查中…" }));
    expect(onRun).not.toHaveBeenCalled();
  });

  it("treats missing or old timestamps as stale", () => {
    expect(isDiagnosticRunStale(undefined, Date.UTC(2026, 7, 29, 3))).toBe(true);
    expect(isDiagnosticRunStale("2026-08-29T02:50:00Z", Date.UTC(2026, 7, 29, 3))).toBe(false);
    expect(isDiagnosticRunStale("2026-08-29T02:30:00Z", Date.UTC(2026, 7, 29, 3))).toBe(true);
  });
});
