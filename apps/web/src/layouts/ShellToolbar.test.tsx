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

  it("disambiguates only duplicate project titles with project code or a stable id", () => {
    render(<MemoryRouter><ShellToolbar
      baseCommands={[]}
      commandContext={{ projectId: "project-a", episodeId: undefined, navigate: vi.fn() }}
      episodeCatalogPending={false}
      mobileNavOpen={false}
      mobileNavTriggerRef={{ current: null }}
      onEpisodeChange={vi.fn()}
      onProjectChange={vi.fn()}
      onToggleMobileNav={vi.fn()}
      projectId="project-a"
      projects={[
        { id: "project-a", title: "同名项目", code: "NOVEL-A" },
        { id: "project-b", title: "同名项目", code: "NOVEL-B" },
        { id: "project-c", title: "唯一项目", code: "UNIQUE" },
        { id: "project-d", title: "无编码同名" },
        { id: "project-e", title: "无编码同名" },
      ]}
      seasons={[]}
    /></MemoryRouter>);

    const select = screen.getByRole("combobox", { name: "当前项目" });
    expect(within(select).getByRole("option", { name: "同名项目 · NOVEL-A" })).toBeTruthy();
    expect(within(select).getByRole("option", { name: "同名项目 · NOVEL-B" })).toBeTruthy();
    expect(within(select).getByRole("option", { name: "唯一项目" })).toBeTruthy();
    expect(within(select).getByRole("option", { name: "无编码同名 · project-d" })).toBeTruthy();
    expect(within(select).getByRole("option", { name: "无编码同名 · project-e" })).toBeTruthy();
  });

  it("uses a stable id when duplicate projects also share a code", () => {
    render(<MemoryRouter><ShellToolbar
      baseCommands={[]}
      commandContext={{ projectId: "project-a", episodeId: undefined, navigate: vi.fn() }}
      episodeCatalogPending={false}
      mobileNavOpen={false}
      mobileNavTriggerRef={{ current: null }}
      onEpisodeChange={vi.fn()}
      onProjectChange={vi.fn()}
      onToggleMobileNav={vi.fn()}
      projectId="project-a"
      projects={[{ id: "project-a", title: "同名项目", code: "SAME" }, { id: "project-b", title: "同名项目", code: "SAME" }]}
      seasons={[]}
    /></MemoryRouter>);

    const select = screen.getByRole("combobox", { name: "当前项目" });
    expect(within(select).getByRole("option", { name: "同名项目 · project-a" })).toBeTruthy();
    expect(within(select).getByRole("option", { name: "同名项目 · project-b" })).toBeTruthy();
  });
});
