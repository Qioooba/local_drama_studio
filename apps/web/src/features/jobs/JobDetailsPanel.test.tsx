import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JobDetailsPanel } from "./JobDetailsPanel";
import { apiJsonResponse } from "../../test/apiResponse";

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
    vi.spyOn(globalThis, "fetch").mockResolvedValue(apiJsonResponse({ job }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><JobDetailsPanel jobId="job-1" /></QueryClientProvider>);

    expect(await screen.findByText("当前进度：已完成 · 100%")).toBeTruthy();
    expect(screen.getByText("进度：已完成 · 100%")).toBeTruthy();
    expect(document.querySelectorAll(".status-pill.state-succeeded")).toHaveLength(2);
    expect(screen.getByText("高级：查看任务输入快照")).toBeTruthy();
    expect(screen.getByRole("button", { name: "已登记为视频" }).hasAttribute("disabled")).toBe(true);
    const download = screen.getByRole("link", { name: "下载视频到当前电脑" });
    expect(download.getAttribute("href")).toBe("/api/v1/artifacts/artifact-1/download");
    expect(download.hasAttribute("download")).toBe(true);
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
    vi.spyOn(globalThis, "fetch").mockResolvedValue(apiJsonResponse({ job: failed }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><JobDetailsPanel jobId="job-1" /></QueryClientProvider>);

    expect(await screen.findByText("PROVIDER_FAILED")).toBeTruthy();
    expect(screen.getAllByText(/worker stopped/)).toHaveLength(1);
  });

  it("previews a verified image artifact directly in the review page", async () => {
    const imageJob = {
      ...job,
      attempts: [{
        ...job.attempts[0],
        artifacts: [{
          ...job.attempts[0].artifacts[0],
          sandbox_rel_path: "jobs/job-1/evidence.png",
          promoted_media_version_id: null,
        }],
      }],
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(apiJsonResponse({ job: imageJob }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><JobDetailsPanel jobId="job-1" /></QueryClientProvider>);

    const preview = await screen.findByRole("img", { name: "已验证产物预览：evidence.png" });
    expect(preview.getAttribute("src")).toBe("/api/v1/artifacts/artifact-1/download");
    expect(screen.getByText("页面直读已验证产物 · evidence.png")).toBeTruthy();
  });

  it("normalizes provider fractional progress for the job and current step", async () => {
    const running = {
      ...job,
      state: "RUNNING",
      progress: { phase: "SAMPLING", percent: 0.38, step_percent: 0.625 },
      attempts: [{ ...job.attempts[0], state: "RUNNING", progress: { phase: "SAMPLING", percent: 0.38, step_percent: 0.625 }, artifacts: [] }],
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(apiJsonResponse({ job: running }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><JobDetailsPanel jobId="job-1" /></QueryClientProvider>);

    expect(await screen.findByText("当前进度：SAMPLING · 总体 38% · 当前编码步骤 63%")).toBeTruthy();
    expect(screen.getByText("进度：SAMPLING · 总体 38% · 当前编码步骤 63%")).toBeTruthy();
  });

  it("finalizes a succeeded TTS artifact into a dialogue candidate and links back to its shot", async () => {
    const ttsJob = {
      ...job,
      id: "tts-job-1",
      type: "TTS_GENERATION",
      subject_type: "DIALOGUE_TEXT_REVISION",
      subject_id: "text-revision-1",
      scope_episode_id: "episode-1",
      scope_shot_id: "shot-1",
      attempts: [{
        ...job.attempts[0],
        artifacts: [{
          ...job.attempts[0].artifacts[0],
          id: "tts-artifact-1",
          kind: "TTS_AUDIO",
          sandbox_rel_path: "jobs/tts-job-1/speech.wav",
          promoted_media_version_id: null,
        }],
      }],
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      if (init?.method === "POST") {
        return apiJsonResponse({ result: { job_id: "tts-job-1", artifact_id: "tts-artifact-1", media: { id: "media-1" }, candidate: { id: "candidate-1" }, idempotent_replay: false } });
      }
      return apiJsonResponse({ job: ttsJob });
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><JobDetailsPanel jobId="tts-job-1" /></QueryClientProvider>);

    const finalize = await screen.findByRole("button", { name: "完成 TTS 登记" });
    fireEvent.click(finalize);

    expect(await screen.findByText(/TTS 已登记为对白候选/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "回到对应镜头试听与采用" }).getAttribute("href")).toBe("/projects/project-1/episodes/episode-1/studio/shot-1?focus=sound");
    expect(fetchMock.mock.calls.some(([, request]) => request?.method === "POST")).toBe(true);
  });
});
