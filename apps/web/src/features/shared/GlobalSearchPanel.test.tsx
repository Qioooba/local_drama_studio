import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { searchAll } from "../../generated/api";
import { GlobalSearchPanel } from "./GlobalSearchPanel";

vi.mock("../../generated/api", () => ({ searchAll: vi.fn() }));

const cases = [
  ["EP03 S12", "EP03 · S12 天台对峙", "/projects/p1/episodes/e3/direct/s12", "SHOT"],
  ["阿宁", "阿宁", "/projects/p1/assets?asset=a1", "STORY_ASSET"],
  ["失败任务", "视频生成失败", "/jobs?state=FAILED", "JOB"],
] as const;

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="当前路径">{location.pathname}{location.search}</output>;
}

describe("GlobalSearchPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(searchAll).mockImplementation(async (query) => {
      const found = cases.find(([keyword]) => keyword === query);
      if (!found) return { items: [] };
      const [keyword, label, route, subjectType] = found;
      return { items: [{ project_id: "p1", subject_type: subjectType, subject_id: `hidden-${keyword}`, label, context: "北港项目", route, snippet: "可导航语义结果" }] } as never;
    });
  });

  it.each(cases)("navigates semantic result for %s without exposing its UUID", async (keyword, label, route) => {
    render(<MemoryRouter><GlobalSearchPanel projectId="p1" /><LocationProbe /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText(/搜索项目、分集/), { target: { value: keyword } });
    const result = await screen.findByRole("button", { name: `打开${label}` });
    expect(searchAll).toHaveBeenCalledWith(keyword, "p1", 50, "", expect.any(AbortSignal));
    expect(screen.queryByText(`hidden-${keyword}`)).toBeNull();
    fireEvent.click(result);
    await waitFor(() => expect(screen.getByLabelText("当前路径").textContent).toBe(route));
  });

  it("waits for two characters and exposes empty and error states", async () => {
    vi.mocked(searchAll).mockRejectedValueOnce(new Error("索引暂不可用"));
    render(<MemoryRouter><GlobalSearchPanel projectId="p1" /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText(/搜索项目、分集/), { target: { value: "阿" } });
    expect(searchAll).not.toHaveBeenCalled();
    expect(screen.getByText("再输入一个字符即可搜索。")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/搜索项目、分集/), { target: { value: "阿宁" } });
    expect((await screen.findByRole("alert")).textContent).toContain("索引暂不可用");
    vi.mocked(searchAll).mockResolvedValueOnce({ items: [] });
    fireEvent.change(screen.getByLabelText(/搜索项目、分集/), { target: { value: "无结果" } });
    expect(await screen.findByText("没有匹配结果。")).toBeTruthy();
  });
});
