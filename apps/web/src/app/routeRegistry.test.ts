import { describe, expect, it } from "vitest";
import { buildBreadcrumbs, parseRouteContext, routes, validateRouteOwnership } from "./routeRegistry";

describe("canonical route registry", () => {
  it("builds the reduced product routes", () => {
    expect(routes.home()).toBe("/");
    expect(routes.quickCreate()).toBe("/quick-create");
    expect(routes.projects()).toBe("/projects");
    expect(routes.adaptationPlans("p 1")).toBe("/projects/p%201/story/plans");
    expect(routes.adaptationPlan("p1", "plan 1")).toBe("/projects/p1/story/plans/plan%201");
    expect(routes.settings("p 1", "quality")).toBe("/projects/p%201/settings/quality");
    expect(routes.shotStudio("p1", "e1", "s1")).toBe("/projects/p1/episodes/e1/studio/s1");
    expect(routes.episodeProduction("p1", "e1")).toBe("/projects/p1/episodes/e1/production");
    expect(routes.postReview("p1", "e1")).toBe("/projects/p1/episodes/e1/post/review");
    expect(routes.postAudio("p1", "e1")).toBe("/projects/p1/episodes/e1/post/audio");
    expect(routes.postEdit("p1", "e1")).toBe("/projects/p1/episodes/e1/post/edit");
    expect(routes.systemCapabilities("p1")).toBe("/system/capabilities");
    expect(routes.systemJobs("p1")).toBe("/system/jobs?project=p1");
  });

  it("parses canonical contexts", () => {
    expect(parseRouteContext("/").routeId).toBe("home");
    expect(parseRouteContext("/quick-create").routeId).toBe("quickCreate");
    expect(parseRouteContext("/projects/p1/story/plans")).toMatchObject({ routeId: "adaptationPlans", scope: "PROJECT", projectId: "p1" });
    expect(parseRouteContext("/projects/p1/story/plans/plan1")).toMatchObject({ routeId: "adaptationPlan", scope: "PROJECT", projectId: "p1" });
    expect(parseRouteContext("/projects/p1/settings/quality").routeId).toBe("settings");
    expect(parseRouteContext("/system/capabilities").routeId).toBe("systemCapabilities");
    expect(parseRouteContext("/projects/p1/episodes/e1/studio/s1")).toEqual({ routeId: "shotStudioShot", scope: "EPISODE", projectId: "p1", episodeId: "e1", shotId: "s1" });
    expect(parseRouteContext("/projects/p1/episodes/e1/production").routeId).toBe("episodeProduction");
    expect(parseRouteContext("/projects/p1/episodes/e1/post/audio").routeId).toBe("postAudio");
  });

  it("fails closed for malformed identifiers", () => {
    expect(parseRouteContext("/projects/%E0%A4%A/story").routeId).toBeNull();
    expect(parseRouteContext("/projects/p1/episodes/e1/studio/%E0%A4%A").routeId).toBeNull();
  });

  it("validates entity ownership", () => {
    const context = parseRouteContext("/projects/p1/episodes/e1/plan");
    expect(validateRouteOwnership(context, "p1", "e1")).toEqual({ valid: true });
    expect(validateRouteOwnership(context, "p2", "e1").valid).toBe(false);
  });

  it("builds precise shot breadcrumbs", () => {
    expect(buildBreadcrumbs({ pathname: "/projects/p1/episodes/e1/studio/s1", projectTitle: "项目", seasonTitle: "S1", episodeTitle: "E1", shotCode: "S001" })).toEqual([
      { label: "工作台", to: "/" },
      { label: "项目", to: "/projects" },
      { label: "项目", to: "/projects/p1" },
      { label: "S1 / E1", to: "/projects/p1/episodes/e1/plan" },
      { label: "镜头", to: "/projects/p1/episodes/e1/studio" },
      { label: "S001", isCurrent: true },
    ]);
  });
});
