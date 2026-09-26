import { describe, expect, it } from "vitest";
import {
  EXPLAINER_PAGES,
  EXPLAINER_PAGE_LABELS,
  buildBreadcrumbs,
  isExplainerPage,
  isExplainerLegacyPage,
  parseRouteContext,
  routes,
  validateRouteOwnership,
} from "./routeRegistry";
import {
  explainerProgressPanelOpen,
  explainerStepStatuses,
  firstStepNeedingAttention,
} from "../features/explainers/ExplainerSteps";
import type { ExplainerOverview, ExplainerRun, ExplainerStep, ExplainerStepStatus } from "../generated/api";

describe("canonical route registry", () => {
  it("builds the reduced product routes", () => {
    expect(routes.home()).toBe("/");
    expect(routes.quickCreate()).toBe("/quick-create");
    expect(routes.projects()).toBe("/projects");
    expect(routes.adaptationPlans("p 1")).toBe("/projects/p%201/story/plans");
    expect(routes.adaptationPlan("p1", "plan 1")).toBe("/projects/p1/story/plans/plan%201");
    expect(routes.settings("p 1", "quality")).toBe("/projects/p%201/settings/quality");
    expect(routes.projectDelivery("p 1")).toBe("/projects/p%201/delivery");
    expect(routes.projectDelivery("p1", "queue")).toBe("/projects/p1/delivery?view=queue");
    expect(routes.productionFactory("p 1")).toBe("/projects/p%201/factory");
    expect(routes.productionFactoryEpisode("p 1", "e 1")).toBe("/projects/p%201/factory?episode=e%201");
    expect(routes.shotStudio("p1", "e1", "s1")).toBe("/projects/p1/episodes/e1/studio/s1");
    expect(routes.episodeProduction("p1", "e1")).toBe("/projects/p1/episodes/e1/production");
    expect(routes.postReview("p1", "e1")).toBe("/projects/p1/episodes/e1/post/review");
    expect(routes.postAudio("p1", "e1")).toBe("/projects/p1/episodes/e1/post/audio");
    expect(routes.postEdit("p1", "e1")).toBe("/projects/p1/episodes/e1/post/edit");
    expect(routes.systemCapabilities("p1")).toBe("/projects/p1/models");
    expect(routes.systemJobs("p1")).toBe("/system/jobs?project=p1");
  });

  it("parses canonical contexts", () => {
    expect(parseRouteContext("/").routeId).toBe("home");
    expect(parseRouteContext("/quick-create").routeId).toBe("quickCreate");
    expect(parseRouteContext("/projects/p1/story/plans")).toMatchObject({ routeId: "adaptationPlans", scope: "PROJECT", projectId: "p1" });
    expect(parseRouteContext("/projects/p1/story/plans/plan1")).toMatchObject({ routeId: "adaptationPlan", scope: "PROJECT", projectId: "p1" });
    expect(parseRouteContext("/projects/p1/settings/quality").routeId).toBe("settings");
    expect(parseRouteContext("/projects/p1/assets")).toEqual({ routeId: "assets", scope: "PROJECT", projectId: "p1", episodeId: null, shotId: null, explainerPage: null });
    expect(parseRouteContext("/projects/p1/delivery")).toEqual({ routeId: "projectDelivery", scope: "PROJECT", projectId: "p1", episodeId: null, shotId: null, explainerPage: null });
    expect(parseRouteContext("/projects/p1/factory")).toEqual({ routeId: "productionFactory", scope: "PROJECT", projectId: "p1", episodeId: null, shotId: null, explainerPage: null });
    expect(parseRouteContext("/system/capabilities").routeId).toBe("systemCapabilities");
    expect(parseRouteContext("/projects/p1/episodes/e1/studio/s1")).toEqual({ routeId: "shotStudioShot", scope: "EPISODE", projectId: "p1", episodeId: "e1", shotId: "s1", explainerPage: null });
    expect(parseRouteContext("/projects/p1/episodes/e1/production").routeId).toBe("episodeProduction");
    expect(parseRouteContext("/projects/p1/episodes/e1/post/audio").routeId).toBe("postAudio");
  });

  it("treats the explainer factory as its own scope without any episode context", () => {
    expect(parseRouteContext("/explainers")).toEqual({ routeId: "explainers", scope: "GLOBAL", projectId: null, episodeId: null, shotId: null, explainerPage: null });
    expect(parseRouteContext("/explainers/new")).toEqual({ routeId: "explainerNew", scope: "GLOBAL", projectId: null, episodeId: null, shotId: null, explainerPage: null });
    expect(parseRouteContext("/explainers/p1/overview")).toEqual({ routeId: "explainerOverview", scope: "EXPLAINER", projectId: "p1", episodeId: null, shotId: null, explainerPage: "overview" });
    expect(parseRouteContext("/explainers/p1/storyboard")).toEqual({ routeId: "explainerStoryboard", scope: "EXPLAINER", projectId: "p1", episodeId: null, shotId: null, explainerPage: "storyboard" });
    expect(parseRouteContext("/explainers/p1/review")).toEqual({ routeId: "explainerReview", scope: "EXPLAINER", projectId: "p1", episodeId: null, shotId: null, explainerPage: "review" });
    // The explainer workspace must never resolve an episode, and an unknown
    // explainer sub-page fails closed instead of guessing a workspace.
    expect(parseRouteContext("/explainers/p1/episodes/e1/plan").routeId).toBeNull();
    expect(parseRouteContext("/explainers/p1/not-a-page").routeId).toBeNull();
    expect(routes.explainerOverview("p 1")).toBe("/explainers/p%201/overview");
    expect(routes.explainerPage("p1", "audio")).toBe("/explainers/p1/audio");
  });

  it("exposes the six documented explainer steps in production order", () => {
    expect([...EXPLAINER_PAGES]).toEqual(["script", "assets", "audio", "storyboard", "clips", "review"]);
    expect(EXPLAINER_PAGES).not.toContain("overview");
    expect(EXPLAINER_PAGE_LABELS).toEqual({
      script: "内容与讲稿",
      assets: "人物与风格",
      audio: "配音",
      storyboard: "分镜与画面",
      clips: "视频片段",
      review: "预览与导出",
    });
    expect(isExplainerPage("clips")).toBe(true);
    expect(isExplainerPage("overview")).toBe(false);
    expect(isExplainerLegacyPage("overview")).toBe(true);
  });

  it("resolves the clips step and its route helper", () => {
    expect(parseRouteContext("/explainers/p1/clips")).toEqual({
      routeId: "explainerClips",
      scope: "EXPLAINER",
      projectId: "p1",
      episodeId: null,
      shotId: null,
      explainerPage: "clips",
    });
    expect(routes.explainerClips("p 1")).toBe("/explainers/p%201/clips");
    expect(routes.explainerPage("p1", "clips")).toBe("/explainers/p1/clips");
  });

  it("keeps the explainer breadcrumb to a single crumb (the shell owns the header)", () => {
    expect(buildBreadcrumbs({ pathname: "/explainers/p1/audio", projectTitle: "灯塔" })).toEqual([
      { label: "配音", isCurrent: true },
    ]);
    expect(buildBreadcrumbs({ pathname: "/explainers/p1/overview", projectTitle: "灯塔" })).toEqual([
      { label: "制作进度", isCurrent: true },
    ]);
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

/* -------------------------------------------------------------------------- */
/* six-step status model and the retired overview redirect                     */
/* -------------------------------------------------------------------------- */

function step(code: string, status: ExplainerStepStatus, jobState: string | null = null): ExplainerStep {
  return {
    id: `step-${code}`,
    run_id: "run-1",
    planned_step_code: code,
    task_key: `explainer:${code}`,
    status,
    job_id: null,
    job_state: jobState,
    attempt_count: 0,
    output_kind: code,
    skip_reason: null,
    blocker_code: null,
    started_at: null,
    finished_at: null,
  };
}

function runOf(steps: ExplainerStep[], projected: ExplainerRun["projected_status"] = "RUNNING"): ExplainerRun {
  return {
    id: "run-1",
    project_id: "p1",
    video_id: "v1",
    status: projected,
    projected_status: projected,
    automation_mode: "AUTO_WITH_EXCEPTIONS",
    plan_hash: "hash-1",
    current_stage_code: null,
    progress_json: {},
    budget_json: {},
    budget_used_json: {},
    blockers_json: [],
    inference_mode: "LOCAL_ONLY",
    research_mode: "OFFLINE_IMPORT",
    automation_workflow_run_id: null,
    steps,
    step_statuses: {},
    workflow_run: null,
    execution_authority: { source_of_truth: "workflow_runs", explainer_runs_is_projection: true, second_claim_queue: false },
  };
}

function overviewOf(partial: Partial<ExplainerOverview> = {}): ExplainerOverview {
  return {
    project_id: "p1",
    video: {
      id: "v1",
      project_id: "p1",
      title: "灯塔",
      topic: "光斑",
      content_kind: "ORIGINAL_FICTION",
      status: "ACTIVE",
      target_seconds: 300,
      duration_mode: "TARGET",
      tolerance_percent: 5,
      automation_mode: "AUTO_WITH_EXCEPTIONS",
      inference_mode: "LOCAL_ONLY",
      research_mode: "OFFLINE_IMPORT",
      current_script_revision_id: null,
      revision: 1,
    },
    editions: [],
    beat_count: 0,
    render_type_counts: {},
    latest_run: null,
    open_issues: [],
    open_issue_count: 0,
    blocking_issue_count: 0,
    active_decisions: [],
    authority_labels: { machine: "自动检查结果", human: "人工确认", publication: "发布授权" },
    capability_snapshot: { probed: true, capabilities: [], unknown_count: 0, unavailable_count: 0 },
    ...partial,
  };
}

describe("explainer step-status model", () => {
  it("returns one of the six documented statuses per step with no data at all", () => {
    const statuses = explainerStepStatuses(null);
    expect(Object.keys(statuses)).toEqual(["script", "assets", "audio", "storyboard", "clips", "review"]);
    expect(Object.values(statuses).every((status) => status === "NOT_STARTED")).toBe(true);
  });

  it("derives 处理中 / 失败 / 需更新 / 已完成 from real run facts", () => {
    expect(explainerStepStatuses(overviewOf({
      latest_run: runOf([step("IDENTITY_ASSETS", "PENDING", "RUNNING")]),
    })).assets).toBe("RUNNING");
    expect(explainerStepStatuses(overviewOf({
      latest_run: runOf([step("COMPOSITION_RENDER", "TERMINAL_FAILED")]),
    })).review).toBe("FAILED");
    expect(explainerStepStatuses(overviewOf({
      latest_run: runOf([step("NARRATION_TTS", "STALE")]),
    })).audio).toBe("NEEDS_UPDATE");
    expect(explainerStepStatuses(overviewOf({
      video: { ...overviewOf().video, current_script_revision_id: "rev-1" },
      latest_run: runOf([step("NARRATION_WRITE", "SUCCEEDED")], "COMPLETED"),
    })).script).toBe("DONE");
  });

  it("sends a legacy /overview deep link to the first step that needs attention", () => {
    // Nothing produced yet: the redirect must land on step 1, never on a
    // retired seventh tab.
    expect(firstStepNeedingAttention(explainerStepStatuses(overviewOf()))).toBe("script");
    // A blocked identity step is the first step that really needs a person.
    expect(firstStepNeedingAttention(explainerStepStatuses(overviewOf({
      video: { ...overviewOf().video, current_script_revision_id: "rev-1" },
      latest_run: runOf([step("NARRATION_WRITE", "SUCCEEDED"), step("IDENTITY_ASSETS", "TERMINAL_FAILED")]),
    })))).toBe("assets");
  });

  it("reads ?panel=progress from the URL", () => {
    expect(explainerProgressPanelOpen("?panel=progress")).toBe(true);
    expect(explainerProgressPanelOpen("?beat=b1&panel=progress")).toBe(true);
    expect(explainerProgressPanelOpen("?beat=b1")).toBe(false);
    expect(explainerProgressPanelOpen("")).toBe(false);
  });
});
