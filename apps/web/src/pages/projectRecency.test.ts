import { describe, expect, it } from "vitest";
import { partitionRecentProjects, sortProjectsByUpdatedAt, type ProjectWithUpdatedAt } from "./projectRecency";

const project = (id: string, updatedAt?: string | null): ProjectWithUpdatedAt => ({
  id, code: id.toUpperCase(), title: id, status: "ACTIVE", revision: 1, updated_at: updatedAt,
});

describe("project recency", () => {
  it("sorts valid activity timestamps newest-first and puts incomplete legacy rows last", () => {
    const ordered = sortProjectsByUpdatedAt([
      project("old", "2026-08-01 12:00:00"), project("missing"),
      project("new", "2026-08-20T08:00:00Z"), project("invalid", "not-a-time"),
    ]);
    expect(ordered.map((item) => item.id)).toEqual(["new", "old", "invalid", "missing"]);
  });

  it("creates a bounded recent section without dropping remaining projects", () => {
    const result = partitionRecentProjects([
      project("p1", "2026-08-01T00:00:00Z"), project("p3", "2026-08-03T00:00:00Z"),
      project("p2", "2026-08-02T00:00:00Z"),
    ], 2);
    expect(result.recentProjects.map((item) => item.id)).toEqual(["p3", "p2"]);
    expect(result.otherProjects.map((item) => item.id)).toEqual(["p1"]);
  });
});
