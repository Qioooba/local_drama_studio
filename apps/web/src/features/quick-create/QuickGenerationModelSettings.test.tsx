import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getProfileVersion, type GenerationModel } from "../../generated/api";
import { QuickGenerationModelSettings } from "./QuickGenerationModelSettings";
import { listQuickGenerationPresets, type QuickGenerationParameters } from "./quickGenerationClient";

vi.mock("../../generated/api", () => ({ getProfileVersion: vi.fn() }));
vi.mock("../model-config/ProfileExecutionDetailButton", () => ({ ProfileExecutionDetailButton: () => null }));
vi.mock("./quickGenerationClient", () => ({
  createQuickGenerationPreset: vi.fn(), deleteQuickGenerationPreset: vi.fn(), updateQuickGenerationPreset: vi.fn(), listQuickGenerationPresets: vi.fn(),
}));

const models = [{ id: "model", name: "H3 视频模型", category: "VIDEO", capabilities: ["VIDEO_T2V"], actions: ["TEXT_TO_VIDEO"], executable: true, routes: [{ action: "TEXT_TO_VIDEO", capability: "VIDEO_T2V", profile_version_id: "video-1", profile_title: "H3 视频模型", version_no: 1, status: "PUBLISHED", workflow_version_id: "workflow-1", executable: true }] }] as GenerationModel[];

function Harness() {
  const [profileId, setProfileId] = useState("video-1");
  const [parameters, setParameters] = useState<QuickGenerationParameters>({});
  return <QuickGenerationModelSettings title="视频模型" description="调整视频参数" action="TEXT_TO_VIDEO" models={models} profileVersionId={profileId} parameters={parameters} onProfileChange={setProfileId} onParametersChange={setParameters} />;
}

describe("QuickGenerationModelSettings", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getProfileVersion).mockResolvedValue({ profile_version: { execution: { override_schema: { fields: { steps: { type: "integer", label: "采样步数", minimum: 1, maximum: 100, step: 1, scopes: ["RUN"] } } } } } } as never);
    vi.mocked(listQuickGenerationPresets).mockResolvedValue({ items: [{ id: "preset-1", name: "快速预览", capability: "VIDEO_T2V", execution_profile_version_id: "video-1", parameters: { steps: 24 }, favorite: true, model_title: "H3 视频模型", model_version_no: 1, model_status: "PUBLISHED", revision: 1 }] });
  });

  it("applies a reusable preset but keeps its parameters editable", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>);
    const presetSelect = await screen.findByRole("combobox", { name: "视频模型常用参数" });
    fireEvent.change(presetSelect, { target: { value: "preset-1" } });
    fireEvent.click(screen.getByText(/调整本次参数/));
    const steps = await screen.findByRole("spinbutton", { name: /采样步数/ });
    expect((steps as HTMLInputElement).value).toBe("24");
    fireEvent.change(steps, { target: { value: "36" } });
    expect((steps as HTMLInputElement).value).toBe("36");
  });
});
