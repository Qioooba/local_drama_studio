import { describe, expect, it } from "vitest";
import {
  buildBreadcrumbs,
  parseRouteContext,
  routes,
  validateRouteOwnership,
} from "./routeRegistry";

describe("routeRegistry", () => {
  it("builds correct typed route URLs", () => {
    expect(routes.projects()).toBe("/projects");
    expect(routes.models()).toBe("/models");
    expect(routes.jobs()).toBe("/jobs");
    expect(routes.jobs("p-123")).toBe("/jobs?project=p-123");
    expect(routes.diagnostics()).toBe("/diagnostics");
    expect(routes.diagnostics("p-123")).toBe("/diagnostics?project=p-123");

    expect(routes.projectHome("proj-001")).toBe("/projects/proj-001");
    expect(routes.story("proj-001")).toBe("/projects/proj-001/story");
    expect(routes.assets("proj-001")).toBe("/projects/proj-001/assets");
    expect(routes.qcPolicies("proj-001")).toBe("/projects/proj-001/qc-policies");
    expect(routes.directorRecipes("proj-001")).toBe("/projects/proj-001/director-recipes");
    expect(routes.productionSettings("proj-001")).toBe("/projects/proj-001/production-settings");
    expect(routes.projectModels("proj-001")).toBe("/projects/proj-001/models");
    expect(routes.projectJobs("proj-001")).toBe("/projects/proj-001/jobs");
    expect(routes.projectDiagnostics("proj-001")).toBe("/projects/proj-001/diagnostics");
    expect(routes.mediaLab("proj-001")).toBe("/projects/proj-001/lab");
    expect(routes.canvas("proj-001")).toBe("/projects/proj-001/canvas");
    expect(routes.canvas("proj-001", "ep-1")).toBe("/projects/proj-001/canvas?episode=ep-1");
    expect(routes.operations("proj-001")).toBe("/projects/proj-001/operations");

    expect(routes.episodePlan("proj-001", "ep-01")).toBe("/projects/proj-001/episodes/ep-01/plan");
    expect(routes.directorDesk("proj-001", "ep-01")).toBe("/projects/proj-001/episodes/ep-01/direct");
    expect(routes.directorDesk("proj-001", "ep-01", "shot-02")).toBe(
      "/projects/proj-001/episodes/ep-01/direct/shot-02"
    );
    expect(routes.generation("proj-001", "ep-01")).toBe("/projects/proj-001/episodes/ep-01/generation");
    expect(routes.generation("proj-001", "ep-01", "shot-02")).toBe(
      "/projects/proj-001/episodes/ep-01/generation/shot-02"
    );
    expect(routes.episodeReview("proj-001", "ep-01")).toBe("/projects/proj-001/episodes/ep-01/review");
    expect(routes.audio("proj-001", "ep-01")).toBe("/projects/proj-001/episodes/ep-01/audio");
    expect(routes.timeline("proj-001", "ep-01")).toBe("/projects/proj-001/episodes/ep-01/timeline");
    expect(routes.delivery("proj-001", "ep-01")).toBe("/projects/proj-001/episodes/ep-01/delivery");
    expect(routes.episodeRun("proj-001", "ep-01")).toBe("/projects/proj-001/episodes/ep-01/run");
  });

  it("parses route context accurately across global, project, and episode scopes", () => {
    expect(parseRouteContext("/projects")).toEqual({
      routeId: "projects",
      scope: "GLOBAL",
      projectId: null,
      episodeId: null,
      shotId: null,
    });

    expect(parseRouteContext("/projects/proj-999")).toEqual({
      routeId: "projectHome",
      scope: "PROJECT",
      projectId: "proj-999",
      episodeId: null,
      shotId: null,
    });

    expect(parseRouteContext("/projects/proj-999/story")).toEqual({
      routeId: "story",
      scope: "PROJECT",
      projectId: "proj-999",
      episodeId: null,
      shotId: null,
    });

    expect(parseRouteContext("/projects/proj-999/models").routeId).toBe("projectModels");
    expect(parseRouteContext("/projects/proj-999/jobs").routeId).toBe("projectJobs");
    expect(parseRouteContext("/projects/proj-999/diagnostics").routeId).toBe("projectDiagnostics");
    expect(parseRouteContext("/projects/proj-999/settings").routeId).toBe("productionSettings");

    expect(parseRouteContext("/projects/proj-999/episodes/ep-03/direct/shot-77")).toEqual({
      routeId: "directorDeskShot",
      scope: "EPISODE",
      projectId: "proj-999",
      episodeId: "ep-03",
      shotId: "shot-77",
    });

    expect(parseRouteContext("/projects/proj-999/episodes/ep-03/direct")).toEqual({
      routeId: "directorDesk",
      scope: "EPISODE",
      projectId: "proj-999",
      episodeId: "ep-03",
      shotId: null,
    });

    expect(parseRouteContext("/projects/proj-999/episodes/ep-03/generation/shot-88")).toEqual({
      routeId: "generationShot",
      scope: "EPISODE",
      projectId: "proj-999",
      episodeId: "ep-03",
      shotId: "shot-88",
    });
  });

  it("validates route ownership preventing cross-project context leaks", () => {
    const ctx = parseRouteContext("/projects/proj-100/episodes/ep-01/plan");
    expect(validateRouteOwnership(ctx, "proj-100", "ep-01")).toEqual({ valid: true });
    expect(validateRouteOwnership(ctx, "proj-200", "ep-01").valid).toBe(false);
    expect(validateRouteOwnership(ctx, "proj-100", "ep-99").valid).toBe(false);
  });

  it("builds correct breadcrumbs hierarchy", () => {
    const crumbs = buildBreadcrumbs({
      pathname: "/projects/proj-100/episodes/ep-01/direct/shot-12",
      projectTitle: "大唐双龙传",
      episodeTitle: "第1集 启程",
      shotCode: "S12-全景打斗",
    });

    expect(crumbs).toHaveLength(5);
    expect(crumbs[0]).toEqual({ label: "项目列表", to: "/projects" });
    expect(crumbs[1]).toEqual({ label: "大唐双龙传", to: "/projects/proj-100" });
    expect(crumbs[2]).toEqual({ label: "第1集 启程", to: "/projects/proj-100/episodes/ep-01/plan" });
    expect(crumbs[3]).toEqual({ label: "导演工作台", to: "/projects/proj-100/episodes/ep-01/direct" });
    expect(crumbs[4]).toEqual({ label: "S12-全景打斗", isCurrent: true });
  });
});
