/**
 * Step model + step bar + `?panel=progress` tests.
 *
 * These lock down the parts of the six-step navigation the design says must not
 * drift: the exact step order, the pure status model derived from real
 * workspace/run/step data, the fact that an incomplete step is still a working
 * link, and the URL-driven 制作进度 drawer.
 *
 * Matchers are plain Vitest assertions; this repository does not register
 * jest-dom matchers globally.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../generated/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../generated/api")>();
  return {
    ...actual,
    getExplainerOverview: vi.fn(),
    getExplainerRun: vi.fn(),
    listProjects: vi.fn(),
  };
});

import * as api from "../../generated/api";
import { EXPLAINER_PAGES, parseRouteContext, routes } from "../../app/routeRegistry";
import {
  EXPLAINER_STEP_STATUS_LABELS,
  ExplainerSteps,
  explainerProgressPanelOpen,
  explainerRunGuide,
  explainerStepStatuses,
  firstStepNeedingAttention,
  nextExplainerPage,
  previousExplainerPage,
  type ExplainerStepStatusMap,
} from "./ExplainerSteps";
import { ExplainerWorkspaceShell } from "./ExplainerWorkspaceShell";
import type { ExplainerOverview, ExplainerRun, ExplainerStep, ExplainerStepStatus } from "../../generated/api";

/* ------------------------------------------------------------------ fixtures */

const STEP_CODES = [
  "RESEARCH_ACQUIRE",
  "FACT_EXTRACT",
  "NARRATION_WRITE",
  "IDENTITY_ASSETS",
  "NARRATION_TTS",
  "NARRATION_ALIGN",
  "EXPLAINER_STORYBOARD",
  "VISUAL_GENERATION",
  "EXPLAINER_VISUAL_QC",
  "SUBTITLE_BUILD",
  "COMPOSITION_RENDER",
  "COMPOSITION_QC",
  "EXPLAINER_POLICY_EVALUATE",
  "EXPLAINER_EXPORT",
];

function step(code: string, status: ExplainerStepStatus, extra: Partial<ExplainerStep> = {}): ExplainerStep {
  return {
    id: `step-${code}`,
    run_id: "run-1",
    planned_step_code: code,
    task_key: `explainer:${code}`,
    status,
    job_id: null,
    job_state: null,
    attempt_count: 0,
    output_kind: code,
    skip_reason: null,
    blocker_code: null,
    started_at: null,
    finished_at: null,
    ...extra,
  };
}

function runWith(steps: ExplainerStep[], overrides: Partial<ExplainerRun> = {}): ExplainerRun {
  return {
    id: "run-1",
    project_id: "p1",
    video_id: "v1",
    status: "RUNNING",
    projected_status: "RUNNING",
    automation_mode: "AUTO_WITH_EXCEPTIONS",
    plan_hash: "hash-1",
    current_stage_code: null,
    progress_json: { completed_steps: 3, total_steps: STEP_CODES.length },
    budget_json: { max_gpu_seconds: 3600 },
    budget_used_json: {},
    blockers_json: [],
    inference_mode: "LOCAL_ONLY",
    research_mode: "OFFLINE_IMPORT",
    automation_workflow_run_id: null,
    steps,
    step_statuses: {},
    workflow_run: null,
    execution_authority: { source_of_truth: "workflow_runs", explainer_runs_is_projection: true, second_claim_queue: false },
    ...overrides,
  };
}

function overviewWith(partial: Partial<ExplainerOverview> = {}): ExplainerOverview {
  return {
    project_id: "p1",
    video: {
      id: "v1",
      project_id: "p1",
      title: "灯塔最后一页值班记录",
      topic: "观察窗为什么短暂无光",
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

const ALL_DONE: ExplainerStepStatusMap = {
  script: "DONE",
  assets: "DONE",
  audio: "DONE",
  storyboard: "DONE",
  clips: "DONE",
  review: "DONE",
};

function renderWithProviders(element: React.ReactElement, path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getExplainerOverview).mockResolvedValue(overviewWith());
  vi.mocked(api.getExplainerRun).mockResolvedValue({ run: runWith([]) });
  vi.mocked(api.listProjects).mockResolvedValue({ items: [], page: { next_cursor: null } } as never);
});

/* --------------------------------------------------------------- step model */

describe("explainer step model", () => {
  it("keeps the six documented steps in production order", () => {
    expect([...EXPLAINER_PAGES]).toEqual(["script", "assets", "audio", "storyboard", "clips", "review"]);
    expect(parseRouteContext("/explainers/p1/clips")).toEqual({
      routeId: "explainerClips",
      scope: "EXPLAINER",
      projectId: "p1",
      episodeId: null,
      shotId: null,
      explainerPage: "clips",
    });
    expect(routes.explainerClips("p 1")).toBe("/explainers/p%201/clips");
  });

  it("reports every step as 未开始 when nothing is known yet", () => {
    expect(explainerStepStatuses(null)).toEqual({
      script: "NOT_STARTED",
      assets: "NOT_STARTED",
      audio: "NOT_STARTED",
      storyboard: "NOT_STARTED",
      clips: "NOT_STARTED",
      review: "NOT_STARTED",
    });
    expect(explainerStepStatuses(overviewWith())).toEqual({
      script: "NOT_STARTED",
      assets: "NOT_STARTED",
      audio: "NOT_STARTED",
      storyboard: "NOT_STARTED",
      clips: "NOT_STARTED",
      review: "NOT_STARTED",
    });
  });

  it("labels the six statuses with the documented Chinese wording", () => {
    expect(EXPLAINER_STEP_STATUS_LABELS).toEqual({
      NOT_STARTED: "未开始",
      RUNNING: "处理中",
      NEEDS_SELECTION: "待选择",
      DONE: "已完成",
      NEEDS_UPDATE: "需更新",
      FAILED: "失败",
    });
  });

  it("marks a step 已完成 only when its real product exists", () => {
    const statuses = explainerStepStatuses(overviewWith({
      video: { ...overviewWith().video, current_script_revision_id: "rev-1" },
      latest_run: runWith([step("NARRATION_WRITE", "SUCCEEDED")]),
    }));
    expect(statuses.script).toBe("DONE");
    expect(statuses.assets).toBe("NOT_STARTED");
    // A succeeded generation task without any real beat is not a finished step.
    const noBeats = explainerStepStatuses(overviewWith({
      latest_run: runWith([step("VISUAL_GENERATION", "SUCCEEDED")]),
    }));
    expect(noBeats.storyboard).toBe("NOT_STARTED");
    expect(noBeats.clips).toBe("NOT_STARTED");
  });

  it("derives 处理中 / 待选择 / 需更新 / 失败 from real run facts", () => {
    const running = explainerStepStatuses(overviewWith({
      latest_run: runWith([step("IDENTITY_ASSETS", "RUNNING", { job_state: "RUNNING" })]),
    }));
    expect(running.assets).toBe("RUNNING");

    // A submitted run creates every row as PENDING; that alone is not 处理中.
    const notDispatched = explainerStepStatuses(overviewWith({
      latest_run: runWith([step("NARRATION_TTS", "PENDING")]),
    }));
    expect(notDispatched.audio).toBe("NOT_STARTED");

    const queued = explainerStepStatuses(overviewWith({
      latest_run: runWith([step("NARRATION_TTS", "PENDING", { job_state: "QUEUED" })]),
    }));
    expect(queued.audio).toBe("RUNNING");

    const failed = explainerStepStatuses(overviewWith({
      latest_run: runWith([step("COMPOSITION_RENDER", "RETRYABLE_FAILED")]),
    }));
    expect(failed.review).toBe("FAILED");

    const stale = explainerStepStatuses(overviewWith({
      latest_run: runWith([step("EXPLAINER_STORYBOARD", "STALE")]),
    }));
    expect(stale.storyboard).toBe("NEEDS_UPDATE");

    const blocked = explainerStepStatuses(overviewWith({
      latest_run: runWith([step("NARRATION_ALIGN", "PENDING")]),
      open_issues: [{ id: "i1", severity: "BLOCKER", responsible_step_code: "NARRATION_ALIGN" }],
      open_issue_count: 1,
      blocking_issue_count: 1,
    }));
    expect(blocked.audio).toBe("NEEDS_SELECTION");
  });

  it("reports 已完成 for every step once the required products are really there", () => {
    const statuses = explainerStepStatuses(overviewWith({
      video: { ...overviewWith().video, current_script_revision_id: "rev-1" },
      beat_count: 12,
      editions: [{ id: "e1" }],
      latest_run: runWith(STEP_CODES.map((code) => step(code, "SUCCEEDED"))),
    }));
    expect(statuses).toEqual(ALL_DONE);
  });

  it("never trusts a task row over a failed task and never invents a percentage", () => {
    const statuses = explainerStepStatuses(overviewWith({
      video: { ...overviewWith().video, current_script_revision_id: "rev-1" },
      beat_count: 4,
      latest_run: runWith([
        step("NARRATION_WRITE", "SUCCEEDED"),
        step("IDENTITY_ASSETS", "TERMINAL_FAILED"),
      ]),
    }));
    expect(statuses.script).toBe("DONE");
    expect(statuses.assets).toBe("FAILED");
  });

  it("prefers the server readiness projection when it is provided", () => {
    const statuses = explainerStepStatuses(overviewWith(), {
      steps: [
        { page: "script", status: "DONE" },
        { page: "clips", status: "NEEDS_SELECTION" },
        { page: "review", status: "NOT_A_STATUS" },
      ],
    });
    expect(statuses.script).toBe("DONE");
    expect(statuses.clips).toBe("NEEDS_SELECTION");
    expect(statuses.review).toBe("NOT_STARTED");
  });

  it("sends the user to the first step that needs attention", () => {
    expect(firstStepNeedingAttention(explainerStepStatuses(overviewWith()))).toBe("script");
    expect(firstStepNeedingAttention({
      ...ALL_DONE,
      script: "DONE",
      assets: "DONE",
      audio: "FAILED",
    })).toBe("audio");
    expect(firstStepNeedingAttention(ALL_DONE)).toBe("review");
  });

  it("walks the six steps forward and backward", () => {
    expect(previousExplainerPage("script")).toBeNull();
    expect(nextExplainerPage("script")).toBe("assets");
    expect(previousExplainerPage("clips")).toBe("storyboard");
    expect(nextExplainerPage("review")).toBeNull();
  });
});

/* -------------------------------------------------------------- run mapping */

describe("§B8 run state mapping", () => {
  it("maps every real run status to one of the seven documented rows", () => {
    expect(explainerRunGuide(null).state).toBe("未启动");
    expect(explainerRunGuide("QUEUED").state).toBe("排队/运行中");
    expect(explainerRunGuide("RUNNING").state).toBe("排队/运行中");
    expect(explainerRunGuide("QC_RUNNING").state).toBe("排队/运行中");
    expect(explainerRunGuide("PAUSED").state).toBe("已暂停");
    expect(explainerRunGuide("WAITING_INPUT").state).toBe("等待用户");
    expect(explainerRunGuide("FAILED").state).toBe("部分失败");
    expect(explainerRunGuide("COMPLETED").state).toBe("已完成");
    expect(explainerRunGuide("CANCELLED").state).toBe("未启动");
  });

  it("treats an unrecognised status as 回执未知 instead of guessing", () => {
    const guide = explainerRunGuide("SOMETHING_NEW");
    expect(guide.state).toBe("回执未知");
    expect(guide.unknownReceipt).toBe(true);
    expect(guide.allowed).toEqual(["查询原回执"]);
    expect(explainerRunGuide("RUNNING").unknownReceipt).toBe(false);
  });

  it("reads ?panel=progress from the real search string", () => {
    expect(explainerProgressPanelOpen("?panel=progress")).toBe(true);
    expect(explainerProgressPanelOpen("panel=progress")).toBe(true);
    expect(explainerProgressPanelOpen("?beat=b1&panel=progress")).toBe(true);
    expect(explainerProgressPanelOpen("?panel=other")).toBe(false);
    expect(explainerProgressPanelOpen("?beat=b1")).toBe(false);
    expect(explainerProgressPanelOpen("")).toBe(false);
    expect(explainerProgressPanelOpen(undefined)).toBe(false);
  });
});

/* ------------------------------------------------------------------ step bar */

describe("explainer step bar", () => {
  it("renders the six numbered steps with a written status each", () => {
    const statuses: ExplainerStepStatusMap = {
      ...ALL_DONE,
      audio: "RUNNING",
      storyboard: "FAILED",
    };
    renderWithProviders(
      <ExplainerSteps projectId="p1" activePage="clips" statuses={statuses} />,
      "/explainers/p1/clips",
    );
    for (const label of ["内容与讲稿", "人物与风格", "配音", "分镜与画面", "视频片段", "预览与导出"]) {
      expect(screen.getByRole("link", { name: label })).toBeTruthy();
    }
    expect(screen.getByText("处理中")).toBeTruthy();
    expect(screen.getByText("失败")).toBeTruthy();
    // script / assets / clips / review are done; audio is running, storyboard failed.
    expect(screen.getAllByText("已完成").length).toBe(4);
    expect(screen.getByRole("link", { name: "视频片段" }).getAttribute("aria-current")).toBe("step");
    expect(screen.getByRole("link", { name: "内容与讲稿" }).getAttribute("href")).toBe("/explainers/p1/script");
  });

  it("keeps a not-yet-complete step clickable", () => {
    renderWithProviders(
      <ExplainerSteps projectId="p1" activePage="script" statuses={explainerStepStatuses(overviewWith())} />,
      "/explainers/p1/script",
    );
    const clips = screen.getByRole("link", { name: "视频片段" });
    expect(clips.getAttribute("href")).toBe("/explainers/p1/clips");
    expect(clips.getAttribute("aria-disabled")).toBeNull();
    expect(clips.textContent).toContain("未开始");
  });
});

/* ------------------------------------------------------ header and drawer */

function ShellProbe() {
  const location = useLocation();
  return <p data-testid="search">{location.search}</p>;
}

describe("explainer workspace shell", () => {
  it("renders the unified header and keeps 总览与生产 off the step bar", async () => {
    renderWithProviders(
      <Routes>
        <Route path="/explainers/:projectId" element={<ExplainerWorkspaceShell />}>
          <Route path="script" element={<p>script-body</p>} />
        </Route>
      </Routes>,
      "/explainers/p1/script",
    );
    await waitFor(() => expect(screen.getByText("script-body")).toBeTruthy());
    expect(screen.getByRole("link", { name: /返回作品列表/ })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "内容与讲稿" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "总览与生产" })).toBeNull();
    expect(screen.queryByRole("link", { name: "制作进度" })).toBeNull();
    expect(screen.getByRole("button", { name: "制作进度" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "一键生成到预览" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "更多" })).toBeTruthy();
  });

  it("shows 上一步 for later steps and 作品列表 on the first step", async () => {
    renderWithProviders(
      <Routes>
        <Route path="/explainers/:projectId" element={<ExplainerWorkspaceShell />}>
          <Route path="script" element={<p>script-body</p>} />
          <Route path="audio" element={<p>audio-body</p>} />
        </Route>
      </Routes>,
      "/explainers/p1/script",
    );
    await waitFor(() => expect(screen.getByText("script-body")).toBeTruthy());
    expect(screen.getByRole("link", { name: "作品列表" }).getAttribute("href")).toBe("/explainers");
    // No page registered bar content, so the bar falls back to the real next step.
    expect(await screen.findByRole("link", { name: "下一步：人物与风格" })).toBeTruthy();
  });

  it("opens the 制作进度 drawer from ?panel=progress and keeps it open on refresh", async () => {
    renderWithProviders(
      <Routes>
        <Route path="/explainers/:projectId" element={<ExplainerWorkspaceShell />}>
          <Route path="script" element={<><p>script-body</p><ShellProbe /></>} />
        </Route>
      </Routes>,
      "/explainers/p1/script?panel=progress",
    );
    await waitFor(() => expect(screen.getByText("script-body")).toBeTruthy());
    const dialog = await screen.findByRole("dialog", { name: "制作进度" });
    expect(dialog).toBeTruthy();
    expect(screen.getAllByText("六步就绪摘要").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "制作进度" }).getAttribute("aria-expanded")).toBe("true");
  });

  it("opens the drawer from the header button, moves focus into it, and restores focus on close", async () => {
    renderWithProviders(
      <Routes>
        <Route path="/explainers/:projectId" element={<ExplainerWorkspaceShell />}>
          <Route path="script" element={<><p>script-body</p><ShellProbe /></>} />
        </Route>
      </Routes>,
      "/explainers/p1/script",
    );
    await waitFor(() => expect(screen.getByText("script-body")).toBeTruthy());
    const trigger = screen.getByRole("button", { name: "制作进度" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    // jsdom's fireEvent.click does not move focus the way a real click does.
    trigger.focus();
    fireEvent.click(trigger);
    await waitFor(() => expect(screen.getByRole("dialog", { name: "制作进度" })).toBeTruthy());
    expect(screen.getByTestId("search").textContent).toContain("panel=progress");
    await waitFor(() => expect(screen.getByRole("dialog", { name: "制作进度" }).contains(document.activeElement)).toBe(true));
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "制作进度" })).toBeNull());
    expect(document.activeElement).toBe(trigger);
    expect(screen.getByTestId("search").textContent).not.toContain("panel=progress");
  });

  it("never renders a fake progress percentage without a real ratio", async () => {
    vi.mocked(api.getExplainerRun).mockResolvedValue({ run: runWith([], { progress_json: {} }) });
    vi.mocked(api.getExplainerOverview).mockResolvedValue(overviewWith({
      latest_run: runWith([], { progress_json: {} }),
    }));
    renderWithProviders(
      <Routes>
        <Route path="/explainers/:projectId" element={<ExplainerWorkspaceShell />}>
          <Route path="script" element={<p>script-body</p>} />
        </Route>
      </Routes>,
      "/explainers/p1/script?panel=progress",
    );
    await waitFor(() => expect(screen.getByText("script-body")).toBeTruthy());
    expect(await screen.findByText(/还没有可信的进度比例/)).toBeTruthy();
  });
});
