import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ShellToolbar } from "./ShellToolbar";

vi.mock("../features/commands/CommandPalette", () => ({
  CommandPalette: () => <button type="button">搜索命令与实体</button>,
}));
vi.mock("../features/status-v2/LocalRuntimeIndicator", () => ({
  LocalRuntimeIndicator: () => <button type="button">本机环境正常</button>,
}));

describe("ShellToolbar", () => {
  it("keeps search, production context and system state in explicit aligned groups", () => {
    const onProjectChange = vi.fn();
    const onEpisodeChange = vi.fn();

    render(<MemoryRouter><ShellToolbar
      baseCommands={[]}
      commandContext={{ projectId: "project-1", episodeId: "episode-1", navigate: vi.fn() }}
      episodeCatalogPending={false}
      episodeId="episode-1"
      mobileNavOpen={false}
      mobileNavTriggerRef={{ current: null }}
      onEpisodeChange={onEpisodeChange}
      onProjectChange={onProjectChange}
      onToggleMobileNav={vi.fn()}
      projectId="project-1"
      projects={[{ id: "project-1", title: "北方小院" }, { id: "project-2", title: "旧城新梦" }]}
      seasons={[{ id: "season-1", title: "第一季", episodes: [{ id: "episode-1", title: "第一集" }, { id: "episode-2", title: "第二集" }] }]}
    /></MemoryRouter>);

    const toolbar = screen.getByRole("group", { name: "全局工具" });
    expect(within(toolbar).getByRole("group", { name: "制作上下文" })).toBeTruthy();
    expect(within(toolbar).getByRole("group", { name: "系统状态与任务" })).toBeTruthy();
    expect(within(toolbar).getByRole("link", { name: "打开任务中心" }).getAttribute("href")).toBe("/system/jobs?project=project-1");

    fireEvent.change(within(toolbar).getByRole("combobox", { name: "当前项目" }), { target: { value: "project-2" } });
    fireEvent.change(within(toolbar).getByRole("combobox", { name: "当前分集" }), { target: { value: "episode-2" } });
    expect(onProjectChange).toHaveBeenCalledWith("project-2");
    expect(onEpisodeChange).toHaveBeenCalledWith("episode-2");
  });
});
