import { beforeEach, describe, expect, test, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProjectConfigurationSnapshot } from "./ReadinessPanels";
import * as api from "../../generated/api";

vi.mock("../../generated/api", async (importOriginal) => {
  const actual = await importOriginal<typeof api>();
  return { ...actual, selectDeliveryTargetVersion: vi.fn(), createDeliveryTarget: vi.fn(), listDeliveryPresets: vi.fn(), createDeliveryTargetFromPreset: vi.fn() };
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

const presets = [
  { code: "DOUYIN_VERTICAL", title: "抖音竖屏", description: "抖音竖屏 1080×1920 @30fps", spec: { path_rel: "06_delivery/douyin_vertical", width: 1080, height: 1920, fps: 30, bitrate_kbps: 6000, max_duration_seconds: 180, cover_aspect: "1080x1440", audio: "AAC", subtitles: "SIDECAR" } },
  { code: "BILIBILI_HORIZONTAL", title: "B站横屏", description: "B站横屏 1920×1080 @30fps", spec: { path_rel: "06_delivery/bilibili_horizontal", width: 1920, height: 1080, fps: 30, bitrate_kbps: 8000, max_duration_seconds: 600, cover_aspect: "1920x1080", audio: "AAC", subtitles: "SIDECAR" } },
];

beforeEach(() => {
  vi.mocked(api.listDeliveryPresets).mockResolvedValue({ items: presets });
  vi.mocked(api.createDeliveryTargetFromPreset).mockResolvedValue({ target: { id: "t-new", version_id: "tv-new", project_id: "p1", code: "DOUYIN_VERTICAL", title: "抖音交付", transport: "LOCAL_FILESYSTEM", version_no: 1, status: "ACTIVE", preset_code: "DOUYIN_VERTICAL", spec: presets[0].spec } });
});

describe("ProjectConfigurationSnapshot delivery target selection (FR-DEL-003)", () => {
  test("selects an existing delivery target version explicitly", async () => {
    const select = vi.mocked(api.selectDeliveryTargetVersion).mockResolvedValue({ target: {} as api.DeliveryTargetVersion });
    const onChanged = vi.fn();
    render(<ProjectConfigurationSnapshot configuration={configuration} projectId="p1" onChanged={onChanged} />);
    await screen.findByLabelText("交付规格预设");
    const selectBox = screen.getByLabelText("交付目标版本");
    fireEvent.change(selectBox, { target: { value: "tv2" } });
    fireEvent.click(screen.getByRole("button", { name: "选择为当前交付目标" }));
    await waitFor(() => expect(select).toHaveBeenCalledWith("p1", "tv2"));
    await waitFor(() => expect(screen.getByText(/已显式选择交付目标版本/)).toBeTruthy());
    expect(onChanged).toHaveBeenCalled();
  });

  test("keeps the select button disabled until a target version is chosen", async () => {
    render(<ProjectConfigurationSnapshot configuration={configuration} projectId="p1" />);
    await screen.findByLabelText("交付规格预设");
    const button = screen.getByRole("button", { name: "选择为当前交付目标" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });
});

describe("ProjectConfigurationSnapshot preset creation (G11 P1-6)", () => {
  test("loads built-in presets into the dropdown", async () => {
    render(<ProjectConfigurationSnapshot configuration={configuration} projectId="p1" />);
    await waitFor(() => expect(vi.mocked(api.listDeliveryPresets)).toHaveBeenCalled());
    const selectBox = screen.getByLabelText("交付规格预设");
    const options = Array.from(selectBox.querySelectorAll("option")).map((option) => option.textContent);
    expect(options).toContain("抖音竖屏 · DOUYIN_VERTICAL");
    expect(options).toContain("B站横屏 · BILIBILI_HORIZONTAL");
    await waitFor(() => expect(screen.getByText(/抖音竖屏 1080×1920/)).toBeTruthy());
  });

  test("creates a delivery target from the selected preset and refreshes", async () => {
    const onChanged = vi.fn();
    render(<ProjectConfigurationSnapshot configuration={configuration} projectId="p1" onChanged={onChanged} />);
    await waitFor(() => expect(vi.mocked(api.listDeliveryPresets)).toHaveBeenCalled());
    const selectBox = screen.getByLabelText("交付规格预设");
    fireEvent.change(selectBox, { target: { value: "BILIBILI_HORIZONTAL" } });
    fireEvent.change(screen.getByLabelText("预设标题"), { target: { value: "B站交付" } });
    fireEvent.click(screen.getByRole("button", { name: "按预设创建目标" }));
    await waitFor(() => expect(vi.mocked(api.createDeliveryTargetFromPreset)).toHaveBeenCalledWith("p1", { preset_code: "BILIBILI_HORIZONTAL", title: "B站交付" }));
    await waitFor(() => expect(screen.getByText(/已按预设创建交付目标/)).toBeTruthy());
    expect(onChanged).toHaveBeenCalled();
  });
});
