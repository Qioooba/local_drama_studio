import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { cancelJob, commitImportSession, getProjectConfiguration, importScriptDocument, listEpisodes, listJobs, listSeasons, pickLocalDocumentFile, retryJob } from "../../generated/api";
import { requestScriptBreakdown } from "../story-workspace-v2/breakdownClient";
import { ScriptImportPanel } from "./ScriptImportPanel";
import { queryKeys } from "../../query/queryKeys";

vi.mock("../../generated/api", () => ({
  commitImportSession: vi.fn(),
  importScriptDocument: vi.fn(),
  getProjectConfiguration: vi.fn(),
  listEpisodes: vi.fn(),
  listJobs: vi.fn(),
  listSeasons: vi.fn(),
  pickLocalDocumentFile: vi.fn(),
  cancelJob: vi.fn(),
  retryJob: vi.fn(),
}));

vi.mock("../story-workspace-v2/breakdownClient", () => ({
  requestScriptBreakdown: vi.fn(),
}));

const imported = {
  source_document_id: "source-1",
  source_document_version_id: "source-v1",
  import_session_id: "session-1",
  media_version_id: "media-1",
  status: "PREVIEW_READY" as const,
  preview_hash: "a".repeat(64),
  index_status: "READY" as const,
  preview: {
    character_count: 12,
    paragraph_count: 2,
    paragraphs: ["第一场", "人物进入"],
    preview_character_limit: 12_000,
    preview_truncated: false,
    offset_unit: "UNICODE_CODEPOINT" as const,
    requires_llm_confirmation: true as const,
  },
};

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return { ...render(<MemoryRouter><QueryClientProvider client={client}>{ui}</QueryClientProvider></MemoryRouter>), client };
}

describe("ScriptImportPanel", () => {
  beforeEach(() => {
    vi.mocked(pickLocalDocumentFile).mockReset().mockResolvedValue({
      selection: { selected: true, path: "D:\\Scripts\\episode.md", uploaded: false, copied: false },
    });
    vi.mocked(importScriptDocument).mockReset().mockResolvedValue({ import: imported });
    vi.mocked(commitImportSession).mockReset().mockResolvedValue({
      commit: {
        ...imported,
        id: "session-1",
        project_id: "project-1",
        status: "COMMITTED",
        revision: 2,
        validation: { valid: true, issue_count: 0 },
        items: [],
        source_document_version: {},
        idempotent: false,
        source_preserved: true,
      },
    });
    vi.mocked(listJobs).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(listSeasons).mockReset().mockResolvedValue({ items: [{ id: "season-1", code: "SEASON_001", title: "第 1 季" }] });
    vi.mocked(listEpisodes).mockReset().mockResolvedValue({ items: [{ id: "ep-1", code: "EPISODE_001", title: "第 1 集", production_status: "NOT_STARTED", target_duration_ms: 60_000 }] });
    vi.mocked(getProjectConfiguration).mockReset().mockResolvedValue({ configuration: { profile_bindings: [{ capability: "LLM_STORY_PARSE", binding_status: "ACTIVE", profile_status: "PUBLISHED", profile_version_id: "prof-v1", profile_title: "本地故事拆解", profile_code: "llm_qwen", version_no: 1 }] } } as never);
    vi.mocked(cancelJob).mockReset().mockResolvedValue({ job: {} as never });
    vi.mocked(retryJob).mockReset().mockResolvedValue({ job: {} as never });
    vi.mocked(requestScriptBreakdown).mockReset().mockResolvedValue({
      job: {
        id: "job-1",
        type: "SCRIPT_BREAKDOWN_LOCAL_LLM",
        project_id: "project-1",
        state: "QUEUED",
        channel: "CPU",
        priority: 60,
        max_attempts: 1,
        revision: 1,
      },
      automatic_apply: false,
      requires_human_action: true,
    });
  });

  it("uses the native picker, previews, then commits with the frozen preview hash", async () => {
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    const commit = screen.getByRole("button", { name: "确认导入所选原稿" }) as HTMLButtonElement;
    expect(commit.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "浏览…" }));
    await waitFor(() =>
      expect(screen.getByLabelText("已选择文档路径")).toHaveTextContent("D:\\Scripts\\episode.md")
    );
    fireEvent.click(screen.getByRole("button", { name: "读取文档并预览" }));
    expect(await screen.findByText(/预览已准备好/)).toBeTruthy();
    expect(commit.disabled).toBe(false);
    fireEvent.click(commit);
    await waitFor(() => expect(commitImportSession).toHaveBeenCalledWith("session-1", "a".repeat(64)));
    expect(await screen.findByText(/原稿已安全导入/)).toBeTruthy();
  });

  it("selects visible paragraph ranges with Shift and complete chapters with one action", async () => {
    vi.mocked(importScriptDocument).mockResolvedValueOnce({
      import: {
        ...imported,
        preview: {
          ...imported.preview,
          paragraph_count: 4,
          paragraphs: ["第一章", "雨夜来信", "第二章", "清晨重逢"],
          chapters: [
            { title: "第一章", start_paragraph: 1, end_paragraph: 2 },
            { title: "第二章", start_paragraph: 3, end_paragraph: 4 },
          ],
        },
      },
    });
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    vi.mocked(pickLocalDocumentFile).mockResolvedValueOnce({ selection: { selected: true, path: "D:\\Scripts\\chaptered.txt", uploaded: false, copied: false } });
    fireEvent.click(screen.getByRole("button", { name: "浏览…" }));
    await waitFor(() => expect(screen.getByLabelText("已选择文档路径")).toHaveTextContent("chaptered.txt"));
    fireEvent.click(screen.getByRole("button", { name: "读取文档并预览" }));
    await screen.findByText(/预览已准备好/);

    fireEvent.click(screen.getByRole("button", { name: /第 2 段.*雨夜来信/ }));
    fireEvent.click(screen.getByRole("button", { name: /第 1 段.*第一章/ }), { shiftKey: true });
    expect(screen.getByRole("button", { name: /第 1 段.*第一章/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /第 2 段.*雨夜来信/ })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "第二章 · 3–4 段" }));
    expect(screen.getByRole("button", { name: /第 3 段.*第二章/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /第 4 段.*清晨重逢/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("recovers the browse button when the native picker is cancelled", async () => {
    vi.mocked(pickLocalDocumentFile).mockResolvedValueOnce({
      selection: { selected: false, path: null, uploaded: false, copied: false },
    });
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    fireEvent.click(screen.getByRole("button", { name: "浏览…" }));
    expect(await screen.findByRole("button", { name: "浏览…" })).toBeEnabled();
    expect(screen.getByLabelText("已选择文档路径")).toHaveTextContent("尚未选择文档");
  });

  it("triggers AI script breakdown after commit and surfaces draft readiness", async () => {
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    fireEvent.click(screen.getByRole("button", { name: "浏览…" }));
    await waitFor(() => expect(screen.getByLabelText("已选择文档路径")).toHaveTextContent("episode.md"));
    fireEvent.click(screen.getByRole("button", { name: "读取文档并预览" }));
    await screen.findByText(/预览已准备好/);

    fireEvent.click(screen.getByRole("button", { name: "确认导入所选原稿" }));
    await screen.findByText(/原稿已安全导入/);

    const breakdownBtn = await screen.findByRole("button", { name: "开始 AI 拆解" });
    expect(breakdownBtn).toBeInTheDocument();

    fireEvent.click(breakdownBtn);
    expect(await screen.findByText(/AI 拆解已在后台排队/)).toBeInTheDocument();
    expect(screen.getByText(/目标成片时长 60 秒/)).toBeInTheDocument();
    expect(screen.getByLabelText("本集原文起始段")).toHaveValue(1);
    expect(screen.getByLabelText("本集原文结束段")).toHaveValue(2);
    expect(requestScriptBreakdown).toHaveBeenCalledWith("session-1", "prof-v1", "ep-1", expect.any(String), {
      sourceParagraphStart: 1,
      sourceParagraphEnd: 2,
    });
  });

  it("blocks an invalid episode source range before creating a Job", async () => {
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    fireEvent.click(screen.getByRole("button", { name: "浏览…" }));
    await waitFor(() => expect(screen.getByLabelText("已选择文档路径")).toHaveTextContent("episode.md"));
    fireEvent.click(screen.getByRole("button", { name: "读取文档并预览" }));
    await screen.findByText(/预览已准备好/);
    fireEvent.click(screen.getByRole("button", { name: "确认导入所选原稿" }));
    await screen.findByText(/原稿已安全导入/);
    fireEvent.change(screen.getByLabelText("本集原文起始段"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("本集原文结束段"), { target: { value: "1" } });
    expect(screen.getByRole("button", { name: "开始 AI 拆解" })).toBeDisabled();
    expect(requestScriptBreakdown).not.toHaveBeenCalled();
  });

  it("requires an explicitly published project binding and never invents or synchronizes a fallback Profile", async () => {
    vi.mocked(getProjectConfiguration).mockResolvedValue({ configuration: { profile_bindings: [] } } as never);
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    fireEvent.click(screen.getByRole("button", { name: "浏览…" }));
    await waitFor(() => expect(screen.getByLabelText("已选择文档路径")).toHaveTextContent("episode.md"));
    fireEvent.click(screen.getByRole("button", { name: "读取文档并预览" }));
    await screen.findByText(/预览已准备好/);
    fireEvent.click(screen.getByRole("button", { name: "确认导入所选原稿" }));
    await screen.findByText(/原稿已安全导入/);
    expect(screen.getByRole("button", { name: "开始 AI 拆解" })).toBeDisabled();
    expect(screen.getByRole("link", { name: /前往模型与能力/ }).getAttribute("href")).toBe("/models?project=project-1");
    expect(requestScriptBreakdown).not.toHaveBeenCalled();
  });

  it("restores persisted progress after refresh and can request cancellation", async () => {
    vi.mocked(listJobs).mockResolvedValue({
      items: [{
        id: "job-running",
        type: "SCRIPT_BREAKDOWN_LOCAL_LLM",
        project_id: "project-1",
        subject_id: "session-restored",
        state: "RUNNING",
        channel: "CPU",
        priority: 60,
        max_attempts: 1,
        revision: 3,
        progress: { phase: "CALLING_LOCAL_LLM", percent: 20 },
      }],
    });
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    expect(await screen.findByText("CALLING_LOCAL_LLM")).toBeInTheDocument();
    expect(screen.getByLabelText("AI 拆解进度 20%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消任务" }));
    await waitFor(() => expect(cancelJob).toHaveBeenCalledWith("job-running"));
  });

  it("offers explicit retry for a failed durable job", async () => {
    vi.mocked(listJobs).mockResolvedValue({
      items: [{
        id: "job-failed",
        type: "SCRIPT_BREAKDOWN_LOCAL_LLM",
        project_id: "project-1",
        subject_id: "session-failed",
        state: "FAILED",
        channel: "CPU",
        priority: 60,
        max_attempts: 1,
        revision: 4,
        progress: { phase: "CALLING_LOCAL_LLM", percent: 20 },
        last_error_code: "LOCAL_LLM_LOOPBACK_UNAVAILABLE",
      }],
    });
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "失败重试" }));
    await waitFor(() => expect(retryJob).toHaveBeenCalledWith("job-failed"));
  });

  it("invalidates drafts and announces a real running-to-succeeded transition", async () => {
    const runningJob = {
      id: "job-transition",
      type: "SCRIPT_BREAKDOWN_LOCAL_LLM",
      project_id: "project-1",
      subject_id: "session-transition",
      state: "RUNNING",
      channel: "CPU",
      priority: 60,
      max_attempts: 1,
      revision: 2,
      progress: { phase: "CALLING_LOCAL_LLM", percent: 80 },
    };
    vi.mocked(listJobs).mockResolvedValueOnce({ items: [runningJob] }).mockResolvedValue({ items: [{ ...runningJob, state: "SUCCEEDED", revision: 3, progress: { phase: "COMPLETED", percent: 100 } }] } as never);
    const onDraftReady = vi.fn();
    const { client } = renderWithClient(<ScriptImportPanel projectId="project-1" onDraftReady={onDraftReady} />);
    expect(await screen.findByText("CALLING_LOCAL_LLM")).toBeInTheDocument();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await client.invalidateQueries({ queryKey: queryKeys.scriptBreakdown.jobs("project-1") });
    await waitFor(() => expect(onDraftReady).toHaveBeenCalledWith(expect.objectContaining({ id: "job-transition", state: "SUCCEEDED" })));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.scriptBreakdown.all("project-1") });
  });

  it("invalidates a prepared preview when the path changes", async () => {
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    fireEvent.click(screen.getByRole("button", { name: "浏览…" }));
    await waitFor(() => expect(screen.getByLabelText("已选择文档路径")).toHaveTextContent("episode.md"));
    fireEvent.click(screen.getByRole("button", { name: "读取文档并预览" }));
    await screen.findByText(/预览已准备好/);
    vi.mocked(pickLocalDocumentFile).mockResolvedValueOnce({ selection: { selected: true, path: "D:\\Scripts\\second.txt", uploaded: false, copied: false } });
    fireEvent.click(screen.getByRole("button", { name: "浏览…" }));
    await waitFor(() => expect(screen.getByLabelText("已选择文档路径")).toHaveTextContent("second.txt"));
    expect(screen.queryByText(/预览已准备好/)).toBeNull();
    expect(
      (screen.getByRole("button", { name: "确认导入所选原稿" }) as HTMLButtonElement).disabled
    ).toBe(true);
  });
});
