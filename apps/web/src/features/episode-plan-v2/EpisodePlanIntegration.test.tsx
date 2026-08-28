import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  applyScriptBreakdownDraft,
  listEpisodes,
  listScriptBreakdownDrafts,
  listSeasons,
  type ScriptBreakdownDraft,
  ApiRequestError,
} from "../../generated/api";
import { getShotGroupWorkspace, type ShotGroupWorkspace } from "./shotGroupsApi";
import { planBeatReplan, applyBeatReplan } from "./beatReplanApi";
import { AIDraftReviewPanel } from "../projects/AIDraftReviewPanel";
import { SelectedBeatReplanPanel } from "./SelectedBeatReplanPanel";
import { ErrorBoundary, RouteErrorBoundary } from "../../components/ui/ErrorBoundary";

vi.mock("../../generated/api", () => ({
  listScriptBreakdownDrafts: vi.fn(),
  listSeasons: vi.fn(),
  listEpisodes: vi.fn(),
  applyScriptBreakdownDraft: vi.fn(),
  ApiRequestError: class ApiRequestError extends Error {
    constructor(
      message: string,
      public status: number,
      public code: string,
      public requestId: string | null,
      public retryable: boolean,
      public suggestedAction: string | null,
    ) {
      super(message);
      this.name = "ApiRequestError";
    }
  },
}));

vi.mock("./shotGroupsApi", () => ({
  getShotGroupWorkspace: vi.fn(),
}));

vi.mock("./beatReplanApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./beatReplanApi")>();
  return {
    ...actual,
    planBeatReplan: vi.fn(),
    applyBeatReplan: vi.fn(),
  };
});

const seasonItems = [{ id: "season-1", code: "SEASON_001", title: "第 1 季" }];
const episodeItems = [{ id: "ep-1", code: "EPISODE_001", title: "第 1 集", production_status: "NOT_STARTED" }];

const draftItem1: ScriptBreakdownDraft = {
  id: "draft-1",
  project_id: "project-1",
  source_document_version_id: "ver-1",
  import_session_id: "sess-1",
  status: "DRAFT_READY",
  revision: 1,
  model_draft_sha256: "a".repeat(64),
  effective_draft_revision_id: null,
  effective_draft_revision_no: 0,
  human_edited: false,
  source_document_code: "DOC_001",
  source_document_title: "第一幕草稿",
  draft: {
    scenes: [
      { scene_no: 1, summary: "开场对峙", shots: [{}, {}] },
      { scene_no: 2, summary: "追逐转场", shots: [{}] },
    ],
  },
  confidence: {
    profile_version_id: "prof-1",
    confidence: { overall: 0.88 },
    questions: ["是否需要雨夜环境？"],
    source_passages: [{ scene_no: 1, quote: "雨夜街头", source_start: 0, source_end: 10 }],
  },
  profile_version_id: "prof-1",
  evidence_status: "COMPLETE",
  application_status: "NOT_APPLIED",
  automatic_apply: false,
  requires_human_action: true,
  created_at: "2026-08-21T08:00:00Z",
};

const draftItem2: ScriptBreakdownDraft = {
  id: "draft-2",
  project_id: "project-1",
  source_document_version_id: "ver-2",
  import_session_id: "sess-2",
  status: "DRAFT_READY",
  revision: 1,
  model_draft_sha256: "b".repeat(64),
  effective_draft_revision_id: null,
  effective_draft_revision_no: 0,
  human_edited: false,
  source_document_code: "DOC_002",
  source_document_title: "第二幕草稿",
  draft: {
    scenes: [{ scene_no: 1, summary: "室内谈判", shots: [{}] }],
  },
  confidence: {
    profile_version_id: "prof-2",
    confidence: { overall: 0.92 },
    questions: [],
    source_passages: [],
  },
  profile_version_id: "prof-2",
  evidence_status: "COMPLETE",
  application_status: "NOT_APPLIED",
  automatic_apply: false,
  requires_human_action: true,
  created_at: "2026-08-21T08:10:00Z",
};

const workspaceData: ShotGroupWorkspace = {
  episode: { id: "ep-1", code: "EP_01", title: "第一集", project_id: "project-1" },
  scenes: [{ id: "scene-1", code: "SCENE_01", title: "街道", revision: 1 }],
  shots: [{ id: "shot-1", code: "SHOT_01", shot_type: "MEDIUM", target_duration_ms: 3000, status: "READY", order_key: "001", scene_id: "scene-1", group_id: "group-1", revision: 1 }],
  groups: [{
    id: "group-1",
    episode_id: "ep-1",
    scene_id: "scene-1",
    kind: "BEAT",
    code: "BEAT_01",
    title: "相遇节拍",
    order_key: "001",
    metadata: {},
    status: "ACTIVE",
    revision: 1,
    members: [{ shot_id: "shot-1", order_key: "001" }],
  }],
};

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
}

describe("Episode Plan Data Contract & Shared QueryClient Integration", () => {
  beforeEach(() => {
    vi.mocked(listScriptBreakdownDrafts).mockReset();
    vi.mocked(listSeasons).mockReset();
    vi.mocked(listEpisodes).mockReset();
    vi.mocked(applyScriptBreakdownDraft).mockReset();
    vi.mocked(getShotGroupWorkspace).mockReset();
    vi.mocked(planBeatReplan).mockReset();
    vi.mocked(applyBeatReplan).mockReset();

    vi.mocked(listSeasons).mockResolvedValue({ items: seasonItems });
    vi.mocked(listEpisodes).mockResolvedValue({ items: episodeItems });
    vi.mocked(getShotGroupWorkspace).mockResolvedValue(workspaceData);
  });

  it("mounts AIDraftReviewPanel first then SelectedBeatReplanPanel without cache collision", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({
      items: [draftItem1],
      automatic_apply: false,
      requires_human_action: true,
    });

    const client = createTestQueryClient();

    const { rerender } = render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <AIDraftReviewPanel projectId="project-1" />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText(/DRAFT_READY · NOT_APPLIED/)).toBeTruthy();
    expect(screen.getByText("第一幕草稿")).toBeTruthy();

    rerender(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <SelectedBeatReplanPanel projectId="project-1" episodeId="ep-1" />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("只重排选定 Beat")).toBeTruthy();
    expect(screen.getByText(/第一幕草稿 · 草稿待审核/)).toBeTruthy();
  });

  it("mounts SelectedBeatReplanPanel first then AIDraftReviewPanel without cache collision", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({
      items: [draftItem1],
      automatic_apply: false,
      requires_human_action: true,
    });

    const client = createTestQueryClient();

    const { rerender } = render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <SelectedBeatReplanPanel projectId="project-1" episodeId="ep-1" />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("只重排选定 Beat")).toBeTruthy();
    expect(await screen.findByText(/第一幕草稿 · 草稿待审核/)).toBeTruthy();

    rerender(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <AIDraftReviewPanel projectId="project-1" />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText(/DRAFT_READY · NOT_APPLIED/)).toBeTruthy();
    expect(screen.getByText("2 个建议场次 · 3 个建议镜头")).toBeTruthy();
  });

  it("mounts both panels simultaneously with a shared QueryClient with multi items", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({
      items: [draftItem1, draftItem2],
      automatic_apply: false,
      requires_human_action: true,
    });

    const client = createTestQueryClient();

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <div id="plan-breakdown">
            <AIDraftReviewPanel projectId="project-1" />
          </div>
          <div id="plan-replan">
            <SelectedBeatReplanPanel projectId="project-1" episodeId="ep-1" />
          </div>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("2 份")).toBeTruthy();
    expect(screen.getByText("第一幕草稿")).toBeTruthy();
    expect(screen.getByText("第二幕草稿")).toBeTruthy();
    expect(screen.getByText(/相遇节拍 · 第 1 次修订/)).toBeTruthy();
    expect(screen.getByText(/第一幕草稿 · 草稿待审核/)).toBeTruthy();
    expect(screen.getByText(/第二幕草稿 · 草稿待审核/)).toBeTruthy();
  });

  it("handles empty items safely in both panels", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({
      items: [],
      automatic_apply: false,
      requires_human_action: true,
    });

    const client = createTestQueryClient();

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <AIDraftReviewPanel projectId="project-1" />
          <SelectedBeatReplanPanel projectId="project-1" episodeId="ep-1" />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("当前没有本地大语言模型生成的拆解草稿；页面不会用模拟建议填充。")).toBeTruthy();
    expect(screen.getByText("0 份")).toBeTruthy();
  });

  it("handles API error with request ID and allows retry", async () => {
    const errorWithRequestId = new (vi.mocked(ApiRequestError) as any)(
      "无法读取本地草稿：数据库锁定 · 请求 ID req_draft_12345",
      500,
      "DB_LOCKED",
      "req_draft_12345",
      true,
      null,
    );
    vi.mocked(listScriptBreakdownDrafts).mockRejectedValueOnce(errorWithRequestId);

    const client = createTestQueryClient();

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <AIDraftReviewPanel projectId="project-1" />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText(/无法读取本地草稿：数据库锁定/)).toBeTruthy();

    vi.mocked(listScriptBreakdownDrafts).mockResolvedValueOnce({
      items: [draftItem1],
      automatic_apply: false,
      requires_human_action: true,
    });

    fireEvent.click(screen.getByRole("button", { name: "重新读取草稿" }));

    expect(await screen.findByText(/DRAFT_READY · NOT_APPLIED/)).toBeTruthy();
    expect(screen.getByText("第一幕草稿")).toBeTruthy();
  });

  it("refreshes storyboard, shot editing, and prompt-tool shot caches after applying a Beat replan", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({
      items: [draftItem1], automatic_apply: false, requires_human_action: true,
    });
    vi.mocked(planBeatReplan).mockResolvedValue({
      episode_id: "ep-1", group_id: "group-1", group_code: "BEAT_01", group_title: "相遇节拍",
      group_revision: 1, draft_id: "draft-1", proposal_scene_no: 1, plan_hash: "p".repeat(64),
      valid: true, issues: [], diff: [],
      summary: { KEEP: 1, ADD: 0, MODIFY: 0, DELETE: 0, PROTECTED: 0 },
      scope: { selected_group_only: true, outside_group_shots_touched: 0 },
    });
    vi.mocked(applyBeatReplan).mockResolvedValue({
      group_revision: 2, created_shot_ids: [], modified_shot_ids: [], archived_shot_ids: [],
      protected_shot_ids: [], historical_variants_deleted: 0,
    });
    const client = createTestQueryClient();
    const storyboardKey = ["storyboard", "ep-1"];
    const editKey = ["shot-edit-context", "ep-1"];
    const productionKey = ["episode", "ep-1", "production", "prompt-tools"];
    client.setQueryData(storyboardKey, { stale: true });
    client.setQueryData(editKey, { stale: true });
    client.setQueryData(productionKey, { stale: true });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter><SelectedBeatReplanPanel projectId="project-1" episodeId="ep-1" /></MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByText("只重排选定 Beat");
    await screen.findByRole("option", { name: /第一幕草稿 · 草稿待审核/ });
    fireEvent.change(screen.getByLabelText("选定剧情段落"), { target: { value: "group-1" } });
    fireEvent.change(screen.getByLabelText("AI 拆解草稿"), { target: { value: "draft-1" } });
    expect(await screen.findByRole("option", { name: /1 · 开场对峙/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "生成差异预览" }));
    expect(await screen.findByText("范围外触碰 0")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "确认应用以上差异" }));
    await waitFor(() => expect(applyBeatReplan).toHaveBeenCalled());
    await waitFor(() => {
      expect(client.getQueryState(storyboardKey)?.isInvalidated).toBe(true);
      expect(client.getQueryState(editKey)?.isInvalidated).toBe(true);
      expect(client.getQueryState(productionKey)?.isInvalidated).toBe(true);
    });
  });
});

describe("ErrorBoundary and RouteErrorBoundary Contract", () => {
  function ThrowingChild({ shouldThrow, error }: { shouldThrow: boolean; error?: Error }) {
    if (shouldThrow) {
      throw error ?? new Error("分集策划组件发生意外渲染故障");
    }
    return <div>正常工作区内容</div>;
  }

  it("catches render errors, displays friendly banner, request ID, and supports retry", () => {
    const apiError = new (vi.mocked(ApiRequestError) as any)(
      "渲染解析崩溃",
      500,
      "RENDER_ERROR",
      "req_boundary_999",
      true,
      null,
    );

    const onRetry = vi.fn();
    const { rerender } = render(
      <MemoryRouter>
        <ErrorBoundary projectId="proj-100" fallbackTitle="分集策划异常隔离" onRetry={onRetry} resetKeys={[true]}>
          <ThrowingChild shouldThrow={true} error={apiError} />
        </ErrorBoundary>
      </MemoryRouter>,
    );

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("分集策划异常隔离")).toBeTruthy();
    expect(screen.getByText("渲染解析崩溃")).toBeTruthy();
    expect(screen.getByText("req_boundary_999")).toBeTruthy();
    expect((screen.getByRole("link", { name: "返回项目" }) as HTMLAnchorElement).getAttribute("href")).toBe("/projects/proj-100");

    const retryBtn = screen.getByRole("button", { name: "重试" });
    fireEvent.click(retryBtn);
    expect(onRetry).toHaveBeenCalled();

    rerender(
      <MemoryRouter>
        <ErrorBoundary projectId="proj-100" fallbackTitle="分集策划异常隔离" onRetry={onRetry} resetKeys={[false]}>
          <ThrowingChild shouldThrow={false} />
        </ErrorBoundary>
      </MemoryRouter>,
    );

    expect(screen.getByText("正常工作区内容")).toBeTruthy();
  });

  it("extracts request ID from plain error messages containing 请求 ID format", () => {
    const error = new Error("后端接口返回 500 · 请求 ID req_plain_888");
    render(
      <MemoryRouter>
        <ErrorBoundary projectId="proj-200">
          <ThrowingChild shouldThrow={true} error={error} />
        </ErrorBoundary>
      </MemoryRouter>,
    );

    expect(screen.getByText("req_plain_888")).toBeTruthy();
  });

  it("renders RouteErrorBoundary with fallback links without crashing", () => {
    render(
      <MemoryRouter initialEntries={["/projects/p1/episodes/e1/plan"]}>
        <Routes>
          <Route
            path="/projects/:projectId/episodes/:episodeId/plan"
            element={<RouteErrorBoundary defaultProjectId="p1" error={new Error("工作区载入受阻")} />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "工作区载入受阻" })).toBeTruthy();
    expect((screen.getByRole("link", { name: "返回项目" }) as HTMLAnchorElement).getAttribute("href")).toBe("/projects/p1");
    expect(screen.getByRole("button", { name: "刷新重试" })).toBeTruthy();
  });

  it("identifies a stale dynamic chunk and offers to load the latest version", () => {
    render(
      <MemoryRouter initialEntries={["/projects/p1/story"]}>
        <Routes>
          <Route
            path="/projects/:projectId/story"
            element={<RouteErrorBoundary error={new TypeError("Failed to fetch dynamically imported module: http://127.0.0.1:3210/assets/StoryWorkspacePage-old.js")} />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { level: 2, name: "界面版本载入受阻" })).toBeTruthy();
    expect(screen.getByText(/当前页面仍引用上一版界面资源/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "载入最新版本" })).toBeTruthy();
  });
});
