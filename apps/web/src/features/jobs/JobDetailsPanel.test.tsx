import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JobDetailsPanel } from "./JobDetailsPanel";

const job = {
  id: "job-1",
  type: "GENERATION_VARIANT",
  project_id: "project-1",
  subject_type: "GENERATION_VARIANT",
  subject_id: "variant-1",
  state: "SUCCEEDED",
  channel: "GPU_H3",
  idempotency_key: "job-1",
  input_snapshot: {},
  priority: 100,
  max_attempts: 1,
  revision: 3,
  progress: { phase: "RUNNING" },
  attempts: [{
    id: "attempt-1",
    job_id: "job-1",
    attempt_no: 1,
    state: "SUCCEEDED",
    progress: { phase: "RUNNING" },
    artifacts: [{
      id: "artifact-1",
      job_attempt_id: "attempt-1",
      kind: "COMFY_OUTPUT",
      sandbox_rel_path: "jobs/job-1/output.mp4",
      sha256: "a".repeat(64),
      status: "VERIFIED",
      promoted_media_version_id: "media-1",
    }],
  }],
};

describe("JobDetailsPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders terminal truth and disables an already promoted artifact", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ job }), { status: 200 }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><JobDetailsPanel jobId="job-1" /></QueryClientProvider>);

    expect(await screen.findByText("当前进度：已完成 · 100%")).toBeTruthy();
    expect(screen.getByText("进度：已完成 · 100%")).toBeTruthy();
    expect(document.querySelectorAll(".status-pill.state-succeeded")).toHaveLength(2);
    expect(screen.getByText("高级：查看任务输入快照")).toBeTruthy();
    expect(screen.getByRole("button", { name: "已登记为视频" }).hasAttribute("disabled")).toBe(true);
  });

  it("shows a failed error once at its attempt", async () => {
    const failed = {
      ...job,
      state: "FAILED",
      progress: { phase: "FAILED" },
      last_error_code: "PROVIDER_FAILED",
      last_error_detail_redacted: "worker stopped",
      attempts: [{
        ...job.attempts[0],
        state: "FAILED",
        progress: { phase: "FAILED" },
        error_code: "PROVIDER_FAILED",
        error_detail_redacted: "worker stopped",
        artifacts: [],
      }],
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ job: failed }), { status: 200 }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><JobDetailsPanel jobId="job-1" /></QueryClientProvider>);

    expect(await screen.findByText("PROVIDER_FAILED")).toBeTruthy();
    expect(screen.getAllByText(/worker stopped/)).toHaveLength(1);
  });

  it("normalizes provider fractional progress for the job and current step", async () => {
    const running = {
      ...job,
      state: "RUNNING",
      progress: { phase: "SAMPLING", percent: 0.38, step_percent: 0.625 },
      attempts: [{ ...job.attempts[0], state: "RUNNING", progress: { phase: "SAMPLING", percent: 0.38, step_percent: 0.625 }, artifacts: [] }],
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ job: running }), { status: 200 }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><JobDetailsPanel jobId="job-1" /></QueryClientProvider>);

    expect(await screen.findByText("当前进度：SAMPLING · 总体 38% · 当前编码步骤 63%")).toBeTruthy();
    expect(screen.getByText("进度：SAMPLING · 总体 38% · 当前编码步骤 63%")).toBeTruthy();
  });
});
