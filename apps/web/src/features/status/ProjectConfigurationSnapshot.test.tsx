import { describe, expect, test, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProjectConfigurationSnapshot } from "./ReadinessPanels";
import * as api from "../../generated/api";

vi.mock("../../generated/api", async (importOriginal) => {
  const actual = await importOriginal<typeof api>();
  return { ...actual, selectDeliveryTargetVersion: vi.fn(), createDeliveryTarget: vi.fn() };
});

const configuration = {
  project: { id: "p1", code: "p1", title: "P1" },
  production_plan: { id: "plan-1", code: "plan", title: "Plan", version_id: "v1", version_no: 1, status: "ACTIVE", plan: {} },
  profile_bindings: [],
  delivery_targets: [
    { target_id: "t1", code: "master", title: "Master", transport: "LOCAL_FILESYSTEM", target_status: "ACTIVE", version_id: "tv1", version_no: 1, version_status: "ACTIVE", spec: {}, delivery_package_count: 0 },
    { target_id: "t2", code: "backup", title: "Backup", transport: "LOCAL_FILESYSTEM", target_status: "ACTIVE", version_id: "tv2", version_no: 1, version_status: "ACTIVE", spec: {}, delivery_package_count: 0 },
  ],
  selected_delivery_target_version_id: null,
  impact: { profile_switches_preserve_frozen_jobs: true, profile_frozen_job_counts: {}, delivery_package_counts: {}, remote_transport_allowed: false },
  runtime_contacted: false,
  network_contacted: false,
  mutated: false,
} as api.ProjectConfiguration;

describe("ProjectConfigurationSnapshot delivery target selection (FR-DEL-003)", () => {
  test("selects an existing delivery target version explicitly", async () => {
    const select = vi.mocked(api.selectDeliveryTargetVersion).mockResolvedValue({ target: {} as api.DeliveryTargetVersion });
    const onChanged = vi.fn();
    render(<ProjectConfigurationSnapshot configuration={configuration} projectId="p1" onChanged={onChanged} />);
    const selectBox = screen.getByLabelText("交付目标版本");
    fireEvent.change(selectBox, { target: { value: "tv2" } });
    fireEvent.click(screen.getByRole("button", { name: "选择为当前交付目标" }));
    await waitFor(() => expect(select).toHaveBeenCalledWith("p1", "tv2"));
    await waitFor(() => expect(screen.getByText(/已显式选择交付目标版本/)).toBeTruthy());
    expect(onChanged).toHaveBeenCalled();
  });

  test("keeps the select button disabled until a target version is chosen", () => {
    render(<ProjectConfigurationSnapshot configuration={configuration} projectId="p1" />);
    const button = screen.getByRole("button", { name: "选择为当前交付目标" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });
});
