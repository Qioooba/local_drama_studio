import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { resolveEffectiveConfiguration } from "../../generated/api";
import { EffectiveConfigurationPreview } from "./EffectiveConfigurationPreview";

vi.mock("../../generated/api", () => ({ resolveEffectiveConfiguration: vi.fn() }));

describe("EffectiveConfigurationPreview", () => {
  beforeEach(() => {
    vi.mocked(resolveEffectiveConfiguration).mockReset().mockResolvedValue({
      configuration: {
        capability: "VIDEO_T2V",
        profile_version_id: "profile-1",
        profile: { title: "H3 T2V" },
        effective_settings: { sigma_points: 31, acceleration: "TURBO_LORA", native_audio: true },
        setting_sources: { sigma_points: "RUN_OVERRIDE", acceleration: "PROFILE_DEFAULT", native_audio: "PROFILE_DEFAULT" },
        components: [{ role: "PRIMARY_MODEL", artifact_id: "artifact-1", title: "H3 Model", status: "AVAILABLE", available: true }],
        runtime_status: "READY",
        warnings: [],
        blocking_errors: [],
        estimated_resources: {},
        fingerprint: "sha256:" + "a".repeat(64),
        valid_until: "2026-08-25T00:01:00Z",
        read_only: true,
        local_only: true,
      },
    });
  });

  it("renders final values and their sources", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <EffectiveConfigurationPreview projectId="project-1" capability="VIDEO_T2V" profileVersionId="profile-1" settings={{ sigma_points: 31 }} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("最终参数")).toBeTruthy();
    expect(screen.getByText(/sigma_points/)).toBeTruthy();
    expect(screen.getByText("RUN_OVERRIDE")).toBeTruthy();
  });
});
