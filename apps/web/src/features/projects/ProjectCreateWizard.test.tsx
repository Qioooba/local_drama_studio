import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { createProject, planProjectCreation } from "../../generated/api";
import { ProjectCreateWizard } from "./ProjectCreateWizard";

vi.mock("../../generated/api", () => ({ planProjectCreation: vi.fn(), createProject: vi.fn() }));

describe("ProjectCreateWizard", () => {
  it("uses a modal dialog with Escape close and focus return", async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ProjectCreateWizard onCreated={vi.fn()} /></QueryClientProvider>);
    const trigger = screen.getByRole("button", { name: "新建项目" });
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "新建版本化项目" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(document.activeElement).toBe(screen.getByLabelText("项目标题"));
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "新建项目" })));
  });

  it("keeps every production field empty and performs storage then final plan before creating", async () => {
    const plan = { status: "READY_WITH_CONFIGURATION_BLOCKERS" as const, checks: [{ code: "PROJECT_ROOT_AVAILABLE", passed: true }], blockers: [], configuration_blockers: ["PROFILE_NOT_BOUND", "PRODUCTION_PLAN_NOT_BOUND", "DELIVERY_TARGET_NOT_BOUND"], accepted_unconfigured: true, target_root_rel: "new_drama", estimated_bytes: 1048576, structure: { season_count: 1, episode_count_per_season: 60, total_episode_count: 60 }, presentation: {}, would_create_project: true as const, mutated: false as const, runtime_contacted: false as const, network_contacted: false as const };
    const project = { id: "new", code: "new_drama", title: "新剧", status: "DRAFT", revision: 1 };
    vi.mocked(planProjectCreation).mockResolvedValue({ plan });
    vi.mocked(createProject).mockResolvedValue({ project, blockers: plan.configuration_blockers });
    const onCreated = vi.fn();
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ProjectCreateWizard onCreated={onCreated} /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: "新建项目" }));
    fireEvent.change(screen.getByLabelText("项目标题"), { target: { value: "新剧" } });
    fireEvent.change(screen.getByLabelText("项目 code"), { target: { value: "new_drama" } });
    fireEvent.change(screen.getByLabelText("季数"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("每季集数"), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: /下一步：发布规格/ }));
    expect(screen.getByLabelText("画幅比例")).toHaveProperty("value", "");
    fireEvent.change(screen.getByLabelText("画幅比例"), { target: { value: "9:16" } });
    fireEvent.change(screen.getByLabelText("制作宽度"), { target: { value: "1080" } });
    fireEvent.change(screen.getByLabelText("制作高度"), { target: { value: "1920" } });
    fireEvent.change(screen.getByLabelText("fps 分子"), { target: { value: "24" } });
    fireEvent.change(screen.getByLabelText("fps 分母"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("目标集时长（秒）"), { target: { value: "90" } });
    fireEvent.change(screen.getByLabelText("主语言"), { target: { value: "zh-CN" } });
    fireEvent.change(screen.getByLabelText("字幕策略"), { target: { value: "NONE" } });
    fireEvent.click(screen.getByRole("button", { name: /下一步：存储预检/ }));
    fireEvent.click(screen.getByRole("button", { name: "运行只读存储预检" }));
    await screen.findByText(/选择配置路线/);
    fireEvent.click(screen.getByLabelText(/稍后配置并接受阻塞/));
    fireEvent.click(screen.getByRole("button", { name: /下一步：创建预览/ }));
    fireEvent.click(screen.getByRole("button", { name: "运行最终创建预检" }));
    await screen.findByText("READY_WITH_CONFIGURATION_BLOCKERS");
    expect(createProject).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认创建 DRAFT" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(project));
    expect(planProjectCreation).toHaveBeenCalledTimes(2);
    expect(vi.mocked(planProjectCreation).mock.invocationCallOrder[1]).toBeLessThan(vi.mocked(createProject).mock.invocationCallOrder[0]);
  });

  it("sends an explicitly selected published profile, plan and local target", async () => {
    const plan = { status: "READY" as const, checks: [{ code: "PROJECT_ROOT_AVAILABLE", passed: true }], blockers: [], configuration_blockers: [], accepted_unconfigured: false, target_root_rel: "configured", estimated_bytes: 1048576, structure: { season_count: 1, episode_count_per_season: 1, total_episode_count: 1 }, presentation: {}, would_create_project: true as const, mutated: false as const, runtime_contacted: false as const, network_contacted: false as const };
    vi.mocked(planProjectCreation).mockResolvedValue({ plan });
    vi.mocked(createProject).mockResolvedValue({ project: { id: "configured", code: "configured", title: "配置项目", status: "DRAFT", revision: 1 }, blockers: [] });
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ProjectCreateWizard profiles={[{ id: "profile", code: "p", title: "已发布代理", version_id: "pv", capability: "video.proxy", status: "PUBLISHED" }]} onCreated={vi.fn()} /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: "新建项目" }));
    for (const [label, value] of [["项目标题", "配置项目"], ["项目 code", "configured"], ["季数", "1"], ["每季集数", "1"]]) fireEvent.change(screen.getByLabelText(label), { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: /下一步：发布规格/ }));
    for (const [label, value] of [["画幅比例", "16:9"], ["制作宽度", "1920"], ["制作高度", "1080"], ["fps 分子", "25"], ["fps 分母", "1"], ["目标集时长（秒）", "90"], ["主语言", "zh-CN"]]) fireEvent.change(screen.getByLabelText(label), { target: { value } });
    fireEvent.change(screen.getByLabelText("字幕策略"), { target: { value: "NONE" } });
    fireEvent.click(screen.getByRole("button", { name: /下一步：存储预检/ }));
    fireEvent.click(screen.getByRole("button", { name: "运行只读存储预检" }));
    await screen.findByText(/选择配置路线/);
    fireEvent.click(screen.getByLabelText("现在显式配置"));
    fireEvent.change(screen.getByLabelText("video.proxy"), { target: { value: "pv" } });
    fireEvent.click(screen.getByRole("button", { name: /下一步：创建预览/ }));
    for (const [label, value] of [["方案 code", "configured-plan"], ["方案标题", "配置方案"], ["交付目标 code", "master"], ["交付目标标题", "本地母版"], ["项目内交付路径", "06_delivery/master"]]) fireEvent.change(screen.getByLabelText(label), { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "运行最终创建预检" }));
    await screen.findByText("READY");
    const finalPayload = vi.mocked(planProjectCreation).mock.calls.at(-1)?.[0];
    expect(finalPayload?.allow_unconfigured_capabilities).toBe(false);
    expect(finalPayload?.profile_bindings).toEqual([{ capability: "video.proxy", profile_version_id: "pv" }]);
    expect(finalPayload?.delivery_target?.spec).toMatchObject({ path_rel: "06_delivery/master", width: 1920, height: 1080 });
  });
});
