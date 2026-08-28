import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { EpisodeProgressLibrary, type EpisodeProgressGroup } from "./EpisodeProgressLibrary";

const groups: EpisodeProgressGroup[] = [{
  season: { id: "season-1", code: "S01", title: "第一季", number: 1 },
  episodes: Array.from({ length: 30 }, (_, index) => ({
    id: `episode-${index + 1}`,
    code: `EP${String(index + 1).padStart(2, "0")}`,
    title: `第 ${index + 1} 集`,
    number: index + 1,
    production_status: "NOT_STARTED",
  })),
}];

describe("EpisodeProgressLibrary", () => {
  it("uses a bounded thumbnail grid and supports season, count, and size expansion", () => {
    render(<MemoryRouter><EpisodeProgressLibrary projectId="project-1" groups={groups} loading={false} /></MemoryRouter>);

    expect(screen.getAllByRole("article")).toHaveLength(24);
    fireEvent.click(screen.getByRole("button", { name: "展开全部 30 集" }));
    expect(screen.getAllByRole("article")).toHaveLength(30);
    fireEvent.click(screen.getByRole("button", { name: "收起到前 24 集" }));
    expect(screen.getAllByRole("article")).toHaveLength(24);

    fireEvent.click(screen.getByRole("button", { name: "紧凑" }));
    expect(screen.getByRole("button", { name: "紧凑" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: /S01 · 第一季/ }));
    expect(screen.queryByRole("article")).toBeNull();
  });
});
