import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getGenerationEstimate, getRef2VaCapability, listProductionTiers } from "../../generated/api";
import { GenerationControlPanel } from "./GenerationControlPanel";

vi.mock("../../generated/api", () => ({ getGenerationEstimate: vi.fn(), getRef2VaCapability: vi.fn(), listProductionTiers: vi.fn() }));

const tiers = [
  { code: "FAST", label: "极速粗筛", frames: 107, resolution: { "9:16": [480, 832], "16:9": [864, 480] }, denoise: 0.95, steps: 20, cfg: 1.0, default_takes: 2, duration_seconds: 4.458 },
  { code: "PRODUCTION", label: "正式成片", frames: 175, resolution: { "9:16": [480, 832], "16:9": [864, 480] }, denoise: 1.0, steps: 20, cfg: 1.0, default_takes: 8, duration_seconds: 7.292 },
];

function Harness({ initialTier = "", onTierChange }: { initialTier?: string; onTierChange?: (code: string) => void }) {
  const [tier, setTier] = useState(initialTier);
  return <GenerationControlPanel timedDirections="[]" performanceBindings="[]" referenceBindings="[]" motionMasks="[]" onTimedDirectionsChange={vi.fn()} onPerformanceBindingsChange={vi.fn()} onReferenceBindingsChange={vi.fn()} onMotionMasksChange={vi.fn()} tier={tier} onTierChange={onTierChange ?? setTier} />;
}

function renderPanel(tier = "", onTierChange = vi.fn()) {
  return render(<GenerationControlPanel timedDirections="[]" performanceBindings="[]" referenceBindings="[]" motionMasks="[]" onTimedDirectionsChange={vi.fn()} onPerformanceBindingsChange={vi.fn()} onReferenceBindingsChange={vi.fn()} onMotionMasksChange={vi.fn()} tier={tier} onTierChange={onTierChange} />);
}

describe("GenerationControlPanel multimodal contracts", () => {
  beforeEach(() => {
    vi.mocked(listProductionTiers).mockReset().mockResolvedValue({ items: tiers, default_tier: "DRAFT" });
    vi.mocked(getRef2VaCapability).mockReset().mockResolvedValue({ capability: { capability: "H3_REF2VA_CANDIDATE", supported: true, reason: "ok", manifest_hint: { ref2va_unet_name: "minimax_h3_ref2va_int8_convrot.safetensors" } } });
    vi.mocked(getGenerationEstimate).mockReset().mockResolvedValue({ status: "NO_LOCAL_ESTIMATE", reason: "NO_MATCHING_HISTORY", dimensions: { profile_version_id: "profile-v1", width: 864, height: 480, duration_seconds: 7.292, frame_count: 175, steps: 20, gpu_class: null, gpu_hardware_model: null, gpu_hardware_model_known: false }, sample_count: 0, minimum_sample_count: 3, p50_seconds: null, p90_seconds: null, evidence: { source: "LOCAL_SUCCEEDED_JOB_ATTEMPTS", most_recent_first: true, candidate_limit: 100, candidate_count: 0, gpu_dimension_source: "JOB_RESOURCE_LEASE_CLASS_OR_CHANNEL", gpu_hardware_model_recorded: false }, audit: { read_only: true, writes_performed: 0, query_count: 1, query_limit: 100 }, local_only: true, network_contacted: false });
  });

  it("exposes immutable driving/reference bindings with semantic role and weight", () => {
    const onReferenceBindingsChange = vi.fn();
    render(<GenerationControlPanel timedDirections="[]" performanceBindings="[]" referenceBindings="[]" motionMasks="[]" onTimedDirectionsChange={vi.fn()} onPerformanceBindingsChange={vi.fn()} onReferenceBindingsChange={onReferenceBindingsChange} onMotionMasksChange={vi.fn()} />);
    const field = screen.getAllByRole("textbox")[2];
    fireEvent.change(field, { target: { value: '[{"role":"DRIVING_VIDEO","media_version_id":"local-video","ordinal":0,"weight":0.7}]' } });
    expect(onReferenceBindingsChange).toHaveBeenCalledWith('[{"role":"DRIVING_VIDEO","media_version_id":"local-video","ordinal":0,"weight":0.7}]');
    expect(screen.getByText(/数量、顺序、媒体类型和 weight/)).toBeTruthy();
  });

  it("renders the production tier selector from GET /production-tiers and shows the summary", async () => {
    render(<Harness />);
    await waitFor(() => expect(listProductionTiers).toHaveBeenCalled());
    const select = screen.getByLabelText(/生产档位/) as HTMLSelectElement;
    expect([...select.options].map((option) => option.value)).toEqual(["", "FAST", "PRODUCTION"]);
    fireEvent.change(select, { target: { value: "PRODUCTION" } });
    const summary = await screen.findByRole("group", { name: "生产档位参数摘要" });
    expect(summary.textContent).toContain("175");
    expect(summary.textContent).toContain("480×832");
    expect(summary.textContent).toContain("864×480");
    expect(summary.textContent).toContain("8");
  });

  it("reports the selected tier through onTierChange", async () => {
    const onTierChange = vi.fn();
    renderPanel("", onTierChange);
    const select = await screen.findByLabelText(/生产档位/);
    fireEvent.change(select, { target: { value: "FAST" } });
    expect(onTierChange).toHaveBeenCalledWith("FAST");
  });

  it("shows the Ref2V capability bit from GET /capabilities/ref2va", async () => {
    renderPanel();
    await waitFor(() => expect(getRef2VaCapability).toHaveBeenCalled());
    expect(await screen.findByText(/H3_REF2VA_CANDIDATE/)).toBeTruthy();
    expect(screen.getByText(/minimax_h3_ref2va_int8_convrot.safetensors/)).toBeTruthy();
  });

  it("greys the Ref2V slot with the reason when unsupported", async () => {
    vi.mocked(getRef2VaCapability).mockResolvedValue({ capability: { capability: "H3_REF2VA_UNAVAILABLE", supported: false, reason: "manifest 缺少 ref2va 模型", manifest_hint: { ref2va_unet_name: null } } });
    renderPanel();
    expect(await screen.findByText(/H3_REF2VA_UNAVAILABLE/)).toBeTruthy();
    expect(screen.getByText(/manifest 缺少 ref2va 模型/)).toBeTruthy();
  });

  it("queries history with the resolved profile and exact selected tier dimensions", async () => {
    render(<GenerationControlPanel timedDirections="[]" performanceBindings="[]" referenceBindings="[]" motionMasks="[]" onTimedDirectionsChange={vi.fn()} onPerformanceBindingsChange={vi.fn()} onReferenceBindingsChange={vi.fn()} onMotionMasksChange={vi.fn()} tier="PRODUCTION" profileVersionId="profile-v1" profileLabel="正式视频" aspectRatio="16:9" />);
    await waitFor(() => expect(getGenerationEstimate).toHaveBeenCalledWith({ profile_version_id: "profile-v1", width: 864, height: 480, duration_seconds: 7.292, frame_count: 175, steps: 20, gpu_class: undefined }));
    expect(await screen.findByText("864×480 · 7.292秒 · 175帧 · 20 steps")).toBeTruthy();
  });
});
