import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GenerationEstimateFact } from "./GenerationEstimateFact";
import { getLocalGenerationEstimate } from "./client";

vi.mock("./client", () => ({ getLocalGenerationEstimate: vi.fn() }));

const request = { profileVersionId: "profile-v7", width: 864, height: 480, durationSeconds: 7.292, frameCount: 175, steps: 20 };
const base = {
  dimensions: { profile_version_id: "profile-v7", width: 864, height: 480, duration_seconds: 7.292, frame_count: 175, steps: 20, gpu_class: null, gpu_hardware_model: null, gpu_hardware_model_known: false as const },
  minimum_sample_count: 3,
  evidence: { source: "LOCAL_SUCCEEDED_JOB_ATTEMPTS" as const, most_recent_first: true as const, candidate_limit: 100, candidate_count: 4, gpu_dimension_source: "JOB_RESOURCE_LEASE_CLASS_OR_CHANNEL" as const, gpu_hardware_model_recorded: false as const },
  audit: { read_only: true as const, writes_performed: 0 as const, query_count: 1, query_limit: 100 },
  local_only: true as const,
  network_contacted: false as const,
};

describe("GenerationEstimateFact", () => {
  beforeEach(() => vi.mocked(getLocalGenerationEstimate).mockReset());

  it("shows p50-p90 only from successful local history with explicit dimensions", async () => {
    vi.mocked(getLocalGenerationEstimate).mockResolvedValue({ ...base, status: "AVAILABLE", reason: null, sample_count: 4, p50_seconds: 20, p90_seconds: 40 });
    render(<GenerationEstimateFact profileLabel="H3 正式视频" request={request} />);
    expect(await screen.findByText("预计 20秒–40秒")).toBeTruthy();
    expect(screen.getByText("基于本机最近 4 次同维度成功运行")).toBeTruthy();
    expect(screen.getByText("864×480 · 7.292秒 · 175帧 · 20 次生成迭代")).toBeTruthy();
    expect(getLocalGenerationEstimate).toHaveBeenCalledWith(request);
    expect(screen.getByText(/生成配置中的资源预估只是理论值，不计入历史实测/)).toBeTruthy();
  });

  it("never guesses when fewer than three samples exist", async () => {
    vi.mocked(getLocalGenerationEstimate).mockResolvedValue({ ...base, status: "NO_LOCAL_ESTIMATE", reason: "INSUFFICIENT_SAMPLES", sample_count: 1, p50_seconds: null, p90_seconds: null });
    render(<GenerationEstimateFact profileLabel="H3 正式视频" request={request} />);
    expect(await screen.findByText("暂无本机估算")).toBeTruthy();
    expect(screen.getByText("同维度成功样本 1/3；不猜测耗时。")).toBeTruthy();
    expect(screen.queryByText(/预计/)).toBeNull();
  });

});
