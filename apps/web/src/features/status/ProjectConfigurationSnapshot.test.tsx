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
    { target_id: "t1", code: "master", title: "母版", transport: "LOCAL_FILESYSTEM", target_status: "INACTIVE", version_id: "tv1", version_no: 1, version_status: "RETIRED", spec: { width: 1920, height: 1080, fps: 24 }, delivery_package_count: 0 },
    { target_id: "t2", code: "backup", title: "备用竖屏", transport: "LOCAL_FILESYSTEM", target_status: "ACTIVE", version_id: "tv2", version_no: 1, version_status: "ACTIVE", spec: { width: 1080, height: 1920, fps: 30 }, delivery_package_count: 0 },
  ],
  selected_delivery_target_version_id: "tv2",
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
  vi.mocked(api.listDeliveryPresets).mockReset().mockResolvedValue({ items: presets });
  vi.mocked(api.createDeliveryTargetFromPreset).mockReset().mockResolvedValue({ target: { id: "t-new", version_id: "tv-new", project_id: "p1", code: "BILIBILI_HORIZONTAL", title: "B站横屏", transport: "LOCAL_FILESYSTEM", version_no: 1, status: "ACTIVE", preset_code: "BILIBILI_HORIZONTAL", spec: presets[1].spec } });
  vi.mocked(api.createDeliveryTarget).mockReset().mockResolvedValue({ target: { version_id: "tv-custom" } });
  vi.mocked(api.selectDeliveryTargetVersion).mockReset().mockResolvedValue({ target: {} as api.DeliveryTargetVersion });
});

describe("ProjectConfigurationSnapshot creator-first delivery setup", () => {
  test("shows the current target by title and keeps technical identifiers out of the primary controls", async () => {
    render(<ProjectConfigurationSnapshot configuration={configuration} projectId="p1" />);
    await screen.findByLabelText("发布平台");
    expect(screen.getAllByText("备用竖屏").length).toBeGreaterThan(0);
    expect(screen.getAllByText("1080 × 1920 · 30 fps").length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("代码")).toBeNull();
    expect(screen.queryByLabelText("相对目录")).toBeNull();
    expect(screen.queryByLabelText("宽")).toBeNull();
    expect(screen.queryByLabelText("FPS")).toBeNull();
  });

  test("creates and activates a platform target without asking for a title or a second selection", async () => {
    const onChanged = vi.fn();
    render(<ProjectConfigurationSnapshot configuration={configuration} projectId="p1" onChanged={onChanged} />);
    const platform = await screen.findByLabelText("发布平台");
    fireEvent.change(platform, { target: { value: "BILIBILI_HORIZONTAL" } });
    expect(screen.queryByLabelText("预设标题")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "使用此发布规格" }));
    await waitFor(() => expect(api.createDeliveryTargetFromPreset).toHaveBeenCalledWith("p1", { preset_code: "BILIBILI_HORIZONTAL", title: "B站横屏" }));
    expect(await screen.findByText(/已创建并启用“B站横屏”/)).toBeTruthy();
    expect(onChanged).toHaveBeenCalled();
  });

  test("keeps switching an existing historical version explicit", async () => {
    const onChanged = vi.fn();
    render(<ProjectConfigurationSnapshot configuration={configuration} projectId="p1" onChanged={onChanged} />);
    await screen.findByLabelText("发布平台");
    fireEvent.click(screen.getByText("切换到以前使用过的规格"));
    fireEvent.change(screen.getByLabelText("已有发布规格"), { target: { value: "tv1" } });
    fireEvent.click(screen.getByRole("button", { name: "切换规格" }));
    await waitFor(() => expect(api.selectDeliveryTargetVersion).toHaveBeenCalledWith("p1", "tv1"));
    expect(await screen.findByText(/已切换到“母版”/)).toBeTruthy();
  });

  test("derives custom code, path and video facts from semantic controls", async () => {
    render(<ProjectConfigurationSnapshot configuration={configuration} projectId="p1" />);
    await screen.findByLabelText("发布平台");
    fireEvent.click(screen.getByText("高级：创建非平台自定义规格"));
    fireEvent.change(screen.getByLabelText("规格名称"), { target: { value: "导演审片" } });
    fireEvent.change(screen.getByLabelText("画面规格来源"), { target: { value: "horizontal-cinematic" } });
    fireEvent.change(screen.getByLabelText("字幕输出"), { target: { value: "BOTH" } });
    fireEvent.click(screen.getByRole("button", { name: "创建并启用自定义规格" }));
    await waitFor(() => expect(api.createDeliveryTarget).toHaveBeenCalledWith("p1", expect.objectContaining({
      code: expect.stringMatching(/^delivery_/),
      title: "导演审片",
      transport: "LOCAL_FILESYSTEM",
      spec: expect.objectContaining({ width: 1920, height: 1080, fps: 24, subtitles: "BOTH", path_rel: expect.stringMatching(/^06_delivery\//) }),
    })));
  });
});
