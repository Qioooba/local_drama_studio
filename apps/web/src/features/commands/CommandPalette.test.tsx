import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { searchAll } from "../../generated/api";
import { CommandPalette } from "./CommandPalette";
import type { StudioCommand } from "./commandRegistry";

vi.mock("../../generated/api", () => ({ searchAll: vi.fn() }));

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function close() { this.removeAttribute("open"); };
});

describe("CommandPalette entity search", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(searchAll).mockResolvedValue({ items: [{ project_id: "p1", subject_type: "SHOT", subject_id: "uuid-not-visible", label: "EP03 · S12 天台对峙", context: "北港项目", route: "/projects/p1/episodes/e3/direct/s12", snippet: "阿宁进入画面" }] } as never);
  });

  it("keeps commands and entities in one keyboard listbox and enters the entity route", async () => {
    const navigate = vi.fn();
    const commands: StudioCommand[] = [{ id: "director", label: "打开导演台", group: "创作", run: vi.fn() }];
    render(<CommandPalette context={{ projectId: "p1", navigate }} baseCommands={commands} />);
    fireEvent.click(screen.getByRole("button", { name: "打开命令面板" }));
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "导演" } });
    expect(await screen.findByText("EP03 · S12 天台对峙")).toBeTruthy();
    expect(screen.getByRole("listbox", { name: "命令与搜索结果" })).toBeTruthy();
    expect(screen.getAllByRole("option")).toHaveLength(2);
    expect(screen.queryByText("uuid-not-visible")).toBeNull();
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(navigate).toHaveBeenCalledWith("/projects/p1/episodes/e3/direct/s12");
  });
});
