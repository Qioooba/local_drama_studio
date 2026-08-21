import { act, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { searchAll } from "../../generated/api";
import { normalizeSearchResult, useNavigableSearch } from "./searchNavigation";

vi.mock("../../generated/api", () => ({ searchAll: vi.fn() }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function Harness() {
  const [query, setQuery] = useState("EP03 S12");
  const state = useNavigableSearch(query, "project-1", 0);
  return <><button onClick={() => setQuery("阿宁")}>next</button><output>{state.items[0]?.label ?? state.status}</output></>;
}

describe("navigable search", () => {
  it("ignores a late response from an older query", async () => {
    const first = deferred<{ items: unknown[] }>();
    const second = deferred<{ items: unknown[] }>();
    vi.mocked(searchAll).mockImplementation((query) => (query === "EP03 S12" ? first.promise : second.promise) as never);
    render(<Harness />);
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)); });
    act(() => screen.getByText("next").click());
    await act(async () => { await new Promise((resolve) => window.setTimeout(resolve, 0)); });
    await act(async () => second.resolve({ items: [{ project_id: "project-1", subject_type: "STORY_ASSET", subject_id: "a1", label: "阿宁", context: "角色", route: "/projects/project-1/assets", snippet: "主角" }] }));
    expect(screen.getByText("阿宁")).toBeTruthy();
    await act(async () => first.resolve({ items: [{ project_id: "project-1", subject_type: "SHOT", subject_id: "s12", label: "旧镜头", context: "EP03", route: "/old", snippet: "旧结果" }] }));
    expect(screen.queryByText("旧镜头")).toBeNull();
    expect(screen.getByText("阿宁")).toBeTruthy();
  });

  it("rejects media routes and supplies a safe internal fallback", () => {
    const item = normalizeSearchResult({ project_id: "p1", subject_type: "JOB", subject_id: "j1", label: "失败任务", route: "/api/v1/media-versions/m1/content", snippet: "失败" });
    expect(item?.route).toBe("/jobs?project=p1&job=j1");
  });
});
