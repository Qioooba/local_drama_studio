import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { bindProductionPlan, type ProjectConfiguration } from "../../generated/api";
import { ProductionSpecEditor } from "./ProductionSpecEditor";

vi.mock("../../generated/api", () => ({
  bindProductionPlan: vi.fn(),
}));

const configuration = {
  project: { id: "project-1", code: "demo", title: "Demo" },
  production_plan: null,
  production_spec: null,
} as unknown as ProjectConfiguration;

describe("ProductionSpecEditor", () => {
  beforeEach(() => vi.clearAllMocks());

  it("saves a selected portrait 480P presentation and the audited upscale policy", async () => {
    vi.mocked(bindProductionPlan).mockResolvedValue({
      binding: {
        production_plan_id: "plan-1",
        production_plan_version_id: "plan-version-1",
        project_id: "project-1",
        plan: {},
      },
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><ProductionSpecEditor projectId="project-1" configuration={configuration} /></QueryClientProvider>);

    fireEvent.click(screen.getByRole("radio", { name: /竖屏 9:16/ }));
    fireEvent.change(document.getElementById("project-format-portrait") as HTMLSelectElement, { target: { value: "480x854" } });
    expect(screen.getByDisplayValue(/LETTERBOX/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存 480P 生产规格" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存 480P 生产规格" }));

    await waitFor(() => expect(bindProductionPlan).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(bindProductionPlan).mock.calls[0][1];
    expect(payload.code).toBe("project-production-plan");
    expect(payload.plan).toMatchObject({
      schema_version: "localdrama.production-plan.v2",
      presentation: {
        aspect_ratio: "9:16",
        width: 480,
        height: 854,
        fps: { numerator: 24, denominator: 1 },
      },
      generation: {
        strategy: "CAPABILITY_RESOLVED",
        upscale: {
          enabled: true,
          required: true,
          stage: "COMPOSE_QC",
          executor: "builtin:ffmpeg",
          target: "PRESENTATION_SPEC",
          fit: "LETTERBOX",
        },
      },
    });
    expect(screen.getByRole("status")).toHaveTextContent("生产规格已保存：480 × 854 · 24 fps");
  });

  it("saves a selected landscape 480P presentation without a resolution-specific label", async () => {
    vi.mocked(bindProductionPlan).mockResolvedValue({
      binding: {
        production_plan_id: "plan-2",
        production_plan_version_id: "plan-version-2",
        project_id: "project-1",
        plan: {},
      },
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><ProductionSpecEditor projectId="project-1" configuration={configuration} /></QueryClientProvider>);

    fireEvent.click(screen.getByRole("radio", { name: /横屏 16:9/ }));
    fireEvent.change(document.getElementById("project-format-landscape") as HTMLSelectElement, { target: { value: "854x480" } });
    expect(screen.getByRole("button", { name: "保存 480P 生产规格" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存 480P 生产规格" }));

    await waitFor(() => expect(bindProductionPlan).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(bindProductionPlan).mock.calls[0][1];
    expect(payload.plan).toMatchObject({
      presentation: {
        aspect_ratio: "16:9",
        width: 854,
        height: 480,
      },
    });
    expect(screen.getByRole("status")).toHaveTextContent("生产规格已保存：854 × 480 · 24 fps");
  });
});
