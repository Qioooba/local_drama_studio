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
    ...render(<QueryClientProvider client={client}><ProjectCreateWizard onCreated={onCreated} profiles={props.profiles} profilesPending={props.profilesPending} /></QueryClientProvider>),
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
    fireEvent.click(screen.getByRole("button", { name: "继续选择创作方式" }));
    expect(screen.getByLabelText(/竖屏短剧/)).toHaveProperty("checked", true);
    expect(screen.getByLabelText(/先开始创作/)).toHaveProperty("checked", true);
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
    expect(payload.code).toMatch(/^drama_/);
    expect(payload.production_plan).toBeUndefined();
    fireEvent.click(screen.getByRole("button", { name: "创建并进入故事工作区" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(createProject).toHaveBeenCalledWith(payload);
  });

  it("does not block story-first creation while model profiles are still loading", async () => {
    renderWizard({ profilesPending: true });
    openAndName("先写故事");
    fireEvent.click(screen.getByRole("button", { name: "继续选择创作方式" }));
    const continueButton = screen.getByRole("button", { name: "继续并自动检查" });
    expect((continueButton as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(continueButton);
    await waitFor(() => expect(planProjectCreation).toHaveBeenCalledTimes(1));
  });

  it("auto-selects published capabilities and derives plan, target and path", async () => {
    const readyPlan = { ...draftPlan, status: "READY" as const, configuration_blockers: [], accepted_unconfigured: false };
    vi.mocked(planProjectCreation).mockResolvedValue({ plan: readyPlan });
    renderWizard({ profiles: [
      { id: "profile-1", code: "video", title: "视频模型", version_id: "video-v3", version_no: 3, capability: "VIDEO_I2V", status: "PUBLISHED" },
      { id: "profile-2", code: "tts", title: "配音模型", version_id: "tts-v2", version_no: 2, capability: "TTS", status: "PUBLISHED" },
    ] });
    openAndName("配置项目");
    fireEvent.click(screen.getByRole("button", { name: "继续选择创作方式" }));
    fireEvent.click(screen.getByLabelText(/同时配置现有模型/));
    expect(screen.getByLabelText("图片生成视频")).toHaveProperty("value", "video-v3");
    expect(screen.getByLabelText("台词语音合成")).toHaveProperty("value", "tts-v2");
    fireEvent.click(screen.getByRole("button", { name: "继续并自动检查" }));
    await screen.findByText("制作配置就绪");
    const payload = vi.mocked(planProjectCreation).mock.calls[0][0];
    expect(payload.allow_unconfigured_capabilities).toBe(false);
    expect(payload.profile_bindings).toEqual([
      { capability: "TTS", profile_version_id: "tts-v2" },
      { capability: "VIDEO_I2V", profile_version_id: "video-v3" },
    ]);
    expect(payload.production_plan?.code).toMatch(/_local$/);
    expect(payload.delivery_target?.code).toMatch(/_master$/);
    expect(payload.delivery_target?.spec).toMatchObject({ path_rel: "06_delivery/master", width: 1080, height: 1920 });
  });

  it("generates identifiers automatically without asking for machine codes", () => {
    renderWizard();
    openAndName("逆袭神豪");
    expect(screen.getByText("项目技术标识").parentElement?.textContent).toMatch(/drama_/);
    expect(screen.queryByLabelText("项目技术标识")).toBeNull();
  });

  it("switches the complete technical specification through one semantic preset", async () => {
    renderWizard();
    openAndName();
    fireEvent.click(screen.getByRole("button", { name: "继续选择创作方式" }));
    fireEvent.click(screen.getByLabelText(/横屏 4K/));
    fireEvent.click(screen.getByRole("button", { name: "继续并自动检查" }));
    await waitFor(() => expect(planProjectCreation).toHaveBeenCalledWith(expect.objectContaining({
      aspect_ratio: "16:9",
      width: 3840,
      height: 2160,
      fps: { numerator: 25, denominator: 1 },
    })));
  });
});
