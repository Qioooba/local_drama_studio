import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { queryKeys } from "./queryKeys";

describe("queryKeys", () => {
  it("keeps list, detail and scoped prefixes structurally consistent", () => {
    expect(queryKeys.projects.list({ limit: 100 }).slice(0, 2)).toEqual(queryKeys.projects.lists());
    expect(queryKeys.assetBible.overview("p1").slice(0, 3)).toEqual(queryKeys.assetBible.project("p1"));
    expect(queryKeys.freshness.report("EPISODE", "e1", 50).slice(0, 4)).toEqual(queryKeys.freshness.scope("EPISODE", "e1"));
    expect(queryKeys.sourcePassage.page("v1", 8_000).slice(0, 3)).toEqual(queryKeys.sourcePassage.document("v1"));
    expect(queryKeys.jobs.list(null)).toEqual(["jobs", "scope", "all", "list"]);
  });

  it("invalidates only the intended project asset-bible subtree", async () => {
    const client = new QueryClient();
    client.setQueryData(queryKeys.assetBible.overview("p1"), { overview: true });
    client.setQueryData(queryKeys.assetBible.assets("p1"), { assets: true });
    client.setQueryData(queryKeys.assetBible.overview("p2"), { overview: true });
    await client.invalidateQueries({ queryKey: queryKeys.assetBible.project("p1"), refetchType: "none" });
    expect(client.getQueryState(queryKeys.assetBible.overview("p1"))?.isInvalidated).toBe(true);
    expect(client.getQueryState(queryKeys.assetBible.assets("p1"))?.isInvalidated).toBe(true);
    expect(client.getQueryState(queryKeys.assetBible.overview("p2"))?.isInvalidated).toBe(false);
  });

  it("uses scoped job prefixes without invalidating another project or job details", async () => {
    const client = new QueryClient();
    client.setQueryData(queryKeys.jobs.list("p1"), { items: [] });
    client.setQueryData(queryKeys.jobs.list("p2"), { items: [] });
    client.setQueryData(queryKeys.jobs.detail("j1"), { id: "j1" });
    await client.invalidateQueries({ queryKey: queryKeys.jobs.scope("p1"), refetchType: "none" });
    expect(client.getQueryState(queryKeys.jobs.list("p1"))?.isInvalidated).toBe(true);
    expect(client.getQueryState(queryKeys.jobs.list("p2"))?.isInvalidated).toBe(false);
    expect(client.getQueryState(queryKeys.jobs.detail("j1"))?.isInvalidated).toBe(false);
  });

  it("invalidates only the target project script breakdown drafts", async () => {
    const client = new QueryClient();
    expect(queryKeys.scriptBreakdown.all("p1")).toEqual(["projects", "p1", "script-breakdown-drafts"]);
    expect(queryKeys.scriptBreakdown.jobs("p1")).toEqual(["projects", "p1", "script-breakdown-jobs"]);
    expect(queryKeys.sourcePassage.drafts("p1")).toEqual(["projects", "p1", "script-breakdown-drafts"]);

    client.setQueryData(queryKeys.scriptBreakdown.all("p1"), { items: [], automatic_apply: false, requires_human_action: true });
    client.setQueryData(queryKeys.scriptBreakdown.all("p2"), { items: [], automatic_apply: false, requires_human_action: true });
    await client.invalidateQueries({ queryKey: queryKeys.scriptBreakdown.all("p1"), refetchType: "none" });
    expect(client.getQueryState(queryKeys.scriptBreakdown.all("p1"))?.isInvalidated).toBe(true);
    expect(client.getQueryState(queryKeys.scriptBreakdown.all("p2"))?.isInvalidated).toBe(false);
  });
});
