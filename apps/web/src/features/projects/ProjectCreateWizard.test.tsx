import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { createProject, planProjectCreation } from "../../generated/api";
import { ProjectCreateWizard } from "./ProjectCreateWizard";

vi.mock("../../generated/api", () => ({ planProjectCreation: vi.fn(), createProject: vi.fn() }));

describe("ProjectCreateWizard", () => {
  it("keeps specs empty, requires blocker acceptance, plans before creating", async () => {
    const plan = { status: "READY_WITH_CONFIGURATION_BLOCKERS" as const, checks: [{ code: "PROJECT_ROOT_AVAILABLE", passed: true }], blockers: [], configuration_blockers: ["PROFILE_NOT_BOUND", "PRODUCTION_PLAN_NOT_BOUND", "DELIVERY_TARGET_NOT_BOUND"], accepted_unconfigured: true, target_root_rel: "new_drama", estimated_bytes: 1048576, would_create_project: true as const, mutated: false as const, runtime_contacted: false as const, network_contacted: false as const };
    const project = { id: "new", code: "new_drama", title: "新剧", status: "DRAFT", revision: 1 };
    vi.mocked(planProjectCreation).mockResolvedValue({ plan });
    vi.mocked(createProject).mockResolvedValue({ project, blockers: plan.configuration_blockers });
    const onCreated = vi.fn();
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ProjectCreateWizard onCreated={onCreated} /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: "新建项目" }));
    fireEvent.change(screen.getByLabelText("项目标题"), { target: { value: "新剧" } });
    fireEvent.change(screen.getByLabelText("项目 code"), { target: { value: "new_drama" } });
    fireEvent.change(screen.getByLabelText("集数"), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: /下一步：制作规格/ }));
    expect(screen.getByLabelText("画幅比例")).toHaveProperty("value", "");
    fireEvent.change(screen.getByLabelText("画幅比例"), { target: { value: "9:16" } });
    fireEvent.change(screen.getByLabelText("fps 分子"), { target: { value: "24" } });
    fireEvent.change(screen.getByLabelText("fps 分母"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("目标集时长（秒）"), { target: { value: "90" } });
    fireEvent.click(screen.getByRole("button", { name: /下一步：本地能力/ }));
    expect((screen.getByRole("button", { name: "运行存储与配置预检" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText(/稍后逐项配置/));
    fireEvent.click(screen.getByRole("button", { name: "运行存储与配置预检" }));
    await screen.findByText("READY_WITH_CONFIGURATION_BLOCKERS");
    expect(createProject).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认创建 DRAFT" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(project));
    expect(vi.mocked(planProjectCreation).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(createProject).mock.invocationCallOrder[0]);
  });
});
