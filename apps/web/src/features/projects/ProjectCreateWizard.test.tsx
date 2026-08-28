import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createProject, planProjectCreation } from "../../generated/api";
import { ProjectCreateWizard } from "./ProjectCreateWizard";

vi.mock("../../generated/api", () => ({ planProjectCreation: vi.fn(), createProject: vi.fn() }));

const draftPlan = {
  status: "READY_WITH_CONFIGURATION_BLOCKERS" as const,
  checks: [{ code: "PROJECT_ROOT_AVAILABLE", passed: true }],
  blockers: [],
  configuration_blockers: ["PROFILE_NOT_BOUND", "PRODUCTION_PLAN_NOT_BOUND", "DELIVERY_TARGET_NOT_BOUND"],
  accepted_unconfigured: true,
  target_root_rel: "new_drama",
  estimated_bytes: 1048576,
  structure: { season_count: 1, episode_count_per_season: 10, total_episode_count: 10 },
  presentation: {},
  would_create_project: true as const,
  mutated: false as const,
  runtime_contacted: false as const,
  network_contacted: false as const,
};

function renderWizard(props: Partial<React.ComponentProps<typeof ProjectCreateWizard>> = {}) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const onCreated = props.onCreated ?? vi.fn();
  return {
    onCreated,
    ...render(<QueryClientProvider client={client}><ProjectCreateWizard onCreated={onCreated} /></QueryClientProvider>),
  };
}

function openAndName(title = "新剧") {
  fireEvent.click(screen.getByRole("button", { name: "新建项目" }));
  fireEvent.change(screen.getByLabelText(/作品标题/), { target: { value: title } });
}

beforeEach(() => {
  vi.mocked(planProjectCreation).mockReset().mockResolvedValue({ plan: draftPlan });
  vi.mocked(createProject).mockReset().mockResolvedValue({ project: { id: "new", code: "new_drama", title: "新剧", status: "DRAFT", revision: 1 }, blockers: draftPlan.configuration_blockers });
});

describe("ProjectCreateWizard", () => {
  it("uses an accessible modal with Escape close and focus return", async () => {
    const { container } = renderWizard();
    const trigger = screen.getByRole("button", { name: "新建项目" });
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "创建新作品" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(document.activeElement).toBe(screen.getByLabelText(/作品标题/));
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "新建项目" })));
    fireEvent.click(screen.getByRole("button", { name: "新建项目" }));
    fireEvent.mouseDown(container.querySelector(".project-create-wizard-overlay") as HTMLElement);
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "创建新作品" })).toBeNull());
  });

  it("creates a writing-ready project with one automatic preflight and no technical input", async () => {
    const { onCreated } = renderWizard();
    openAndName();
    expect(screen.getByRole("spinbutton", { name: /季度数/ })).toHaveProperty("value", "1");
    expect(screen.getByRole("spinbutton", { name: /每季计划集数/ })).toHaveProperty("value", "10");
    expect(screen.getByText("项目技术标识")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "继续设置制作规格" }));
    expect(screen.getByRole("radio", { name: /竖屏 9:16/ })).toHaveProperty("checked", true);
    expect(screen.getByLabelText("常用分辨率", { selector: "#project-format-portrait" })).toHaveProperty("value", "1080x1920");
    expect(screen.queryByText("开始方式")).toBeNull();
    expect(screen.queryByText("同时配置现有模型")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "继续并自动检查" }));
    await screen.findByText("创作就绪");
    expect(planProjectCreation).toHaveBeenCalledTimes(1);
    const payload = vi.mocked(planProjectCreation).mock.calls[0][0];
    expect(payload).toMatchObject({
      title: "新剧",
      season_count: 1,
      episode_count: 10,
      aspect_ratio: "9:16",
      width: 1080,
      height: 1920,
      fps: { numerator: 24, denominator: 1 },
      allow_unconfigured_capabilities: true,
    });
    expect(payload.code).toBe("xin_ju");
    expect(payload.production_plan).toBeUndefined();
    expect(payload.profile_bindings).toBeUndefined();
    expect(payload.delivery_target).toBeUndefined();
    fireEvent.click(screen.getByRole("button", { name: "创建并进入故事工作区" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(createProject).toHaveBeenCalledWith(payload);
  });

  it("generates identifiers automatically without asking for machine codes", () => {
    renderWizard();
    openAndName("逆袭神豪");
    expect(screen.getByText("项目技术标识").parentElement?.textContent).toContain("ni_xi_shen_hao");
    expect(screen.getByText("项目技术标识").parentElement?.textContent).toContain("中文转为无声调拼音");
    expect(screen.queryByLabelText("项目技术标识")).toBeNull();
  });

  it("shows deduplicated landscape and portrait resolution menus", () => {
    renderWizard();
    openAndName();
    fireEvent.click(screen.getByRole("button", { name: "继续设置制作规格" }));
    const landscape = screen.getByLabelText("常用分辨率", { selector: "#project-format-landscape" });
    const portrait = screen.getByLabelText("常用分辨率", { selector: "#project-format-portrait" });
    expect(Array.from((landscape as HTMLSelectElement).options).map((option) => option.textContent)).toEqual([
      "480P · 854 × 480",
      "720P · HD · 1280 × 720",
      "1080P · Full HD · 1920 × 1080",
      "1440P · 2K · 2560 × 1440",
      "2160P · 4K · 3840 × 2160",
    ]);
    expect(Array.from((portrait as HTMLSelectElement).options).map((option) => option.textContent)).toContain("720P · HD · 720 × 1280");
    expect(screen.queryByText("抖音竖屏")).toBeNull();
    expect(screen.queryByText("快手竖屏")).toBeNull();
  });

  it("switches the complete technical specification through one orientation menu", async () => {
    renderWizard();
    openAndName();
    fireEvent.click(screen.getByRole("button", { name: "继续设置制作规格" }));
    fireEvent.click(screen.getByRole("radio", { name: /横屏 16:9/ }));
    fireEvent.change(screen.getByLabelText("常用分辨率", { selector: "#project-format-landscape" }), { target: { value: "3840x2160" } });
    fireEvent.change(screen.getByLabelText("项目帧率"), { target: { value: "25" } });
    fireEvent.click(screen.getByRole("button", { name: "继续并自动检查" }));
    await waitFor(() => expect(planProjectCreation).toHaveBeenCalledWith(expect.objectContaining({
      aspect_ratio: "16:9",
      width: 3840,
      height: 2160,
      fps: { numerator: 25, denominator: 1 },
    })));
  });

  it("supports validated custom dimensions", async () => {
    renderWizard();
    openAndName("方形故事");
    fireEvent.click(screen.getByRole("button", { name: "继续设置制作规格" }));
    fireEvent.click(screen.getByRole("radio", { name: /自定义尺寸/ }));
    fireEvent.change(screen.getByLabelText("宽度（像素）"), { target: { value: "1600" } });
    fireEvent.change(screen.getByLabelText("高度（像素）"), { target: { value: "800" } });
    fireEvent.change(screen.getByLabelText("项目帧率"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "继续并自动检查" }));
    await waitFor(() => expect(planProjectCreation).toHaveBeenCalledWith(expect.objectContaining({
      aspect_ratio: "2:1",
      width: 1600,
      height: 800,
      fps: { numerator: 30, denominator: 1 },
    })));
  });

  it("blocks invalid custom dimensions with an inline explanation", () => {
    renderWizard();
    openAndName();
    fireEvent.click(screen.getByRole("button", { name: "继续设置制作规格" }));
    fireEvent.click(screen.getByRole("radio", { name: /自定义尺寸/ }));
    fireEvent.change(screen.getByLabelText("宽度（像素）"), { target: { value: "999" } });
    expect(screen.getByRole("alert").textContent).toContain("偶数");
    expect(screen.getByRole("button", { name: "继续并自动检查" })).toHaveProperty("disabled", true);
  });
});
