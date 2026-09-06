import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { cancelJob, commitImportSession, deleteJob, getImportSessionParagraphs, getLatestProjectScriptImport, listCapabilityOptions, listEpisodes, listJobs, listSeasons, retryJob, uploadScriptDocument, type CapabilityOption, type CapabilityOptions } from "../../generated/api";
import { requestScriptBreakdown } from "../story-workspace-v2/breakdownClient";
import { ScriptImportPanel } from "./ScriptImportPanel";
import { queryKeys } from "../../query/queryKeys";

vi.mock("../../generated/api", () => ({
  commitImportSession: vi.fn(),
  getImportSessionParagraphs: vi.fn(),
  getLatestProjectScriptImport: vi.fn(),
  listEpisodes: vi.fn(),
  listJobs: vi.fn(),
  listCapabilityOptions: vi.fn(),
  listSeasons: vi.fn(),
  cancelJob: vi.fn(),
  deleteJob: vi.fn(),
  retryJob: vi.fn(),
  uploadScriptDocument: vi.fn(),
}));

vi.mock("../story-workspace-v2/breakdownClient", () => ({
  requestScriptBreakdown: vi.fn(),
}));

const imported = {
  source_document_id: "source-1",
  source_document_version_id: "source-v1",
  import_session_id: "session-1",
  media_version_id: "media-1",
  stored_source: {
    scope: "PROJECT" as const,
    kind: "FILE" as const,
    display_name: "照骨灯_凡人修仙原创长篇_约200分钟.txt",
    server_absolute_path: "F:\\DramaProjects\\project-1\\00_admin\\imports\\decd4c22-照骨灯_凡人修仙原创长篇_约200分钟.txt",
    rel_path: "00_admin/imports/decd4c22-照骨灯_凡人修仙原创长篇_约200分钟.txt",
    download_url: "/api/v1/media-versions/media-1/content",
    download_filename: "照骨灯_凡人修仙原创长篇_约200分钟.txt",
  },
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

function capabilityOption(profileVersionId: string, model: string, title: string, versionNo = 1): CapabilityOption {
  return {
    profile_version_id: profileVersionId,
    profile: { id: `profile-${profileVersionId}`, code: profileVersionId, title, version_no: versionNo, status: "PUBLISHED" },
    model: { name: model, provider: "OLLAMA_LOOPBACK" },
    runtime: { id: "ollama", title: "本机 Ollama", status: "AVAILABLE", transport: "LOOPBACK_HTTP" },
    workflow: { id: null, title: null, status: null },
    selectable: true,
    availability: "READY",
    blockers: [],
    warnings: [],
    execution_fingerprint: `sha256:${profileVersionId}`,
  };
}

function capabilityResponse(options: CapabilityOption[], selected = options[0] ?? null): CapabilityOptions {
  return {
    capability: "LLM_STORY_PARSE",
    scope: { project_id: "project-1", episode_id: null, shot_id: null },
    selection: {
      mode: "AUTO",
      source: "AUTO",
      profile_version_id: selected?.profile_version_id ?? null,
      ready: Boolean(selected),
      option: selected,
      blockers: selected ? [] : [{ code: "NO_COMPATIBLE_PROFILE", message: "当前继承链没有可执行的已发布配置" }],
    },
    options,
    configured_runtime: null,
    summary: { total_count: options.length, selectable_count: options.filter((item) => item.selectable).length, blocked_count: options.filter((item) => !item.selectable).length },
    repair_href: "/system/capabilities?view=resources",
    read_only: true,
    runtime_contacted: false,
    network_contacted: false,
    mutated: false,
  };
}

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return { ...render(<MemoryRouter><QueryClientProvider client={client}>{ui}</QueryClientProvider></MemoryRouter>), client };
}

async function uploadDocument(name = "episode.md") {
  const input = screen.getByLabelText("选择本地文档") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["第一场\n\n人物进入"], name, { type: "text/markdown" })] } });
  await screen.findByText("预览已就绪");
}

describe("ScriptImportPanel", () => {
  beforeEach(() => {
    vi.mocked(getLatestProjectScriptImport).mockReset().mockResolvedValue({ latest: null });
    vi.mocked(uploadScriptDocument).mockReset().mockResolvedValue({ import: imported });
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
    vi.mocked(getImportSessionParagraphs).mockReset().mockResolvedValue({
      session_id: "session-1",
      source_document_version_id: "source-v1",
      start_paragraph: 1,
      end_paragraph: 2,
      total_paragraph_count: 2,
      items: [
        { number: 1, text: "第一场", source_start: 0, source_end: 3, is_heading: false },
        { number: 2, text: "人物进入", source_start: 5, source_end: 9, is_heading: false },
      ],
      chapters: [],
      has_previous: false,
      has_more: false,
      read_only: true,
    });
    vi.mocked(listJobs).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(listSeasons).mockReset().mockResolvedValue({ items: [{ id: "season-1", code: "SEASON_001", title: "第 1 季" }] });
    vi.mocked(listEpisodes).mockReset().mockResolvedValue({ items: [{ id: "ep-1", code: "EPISODE_001", title: "第 1 集", production_status: "NOT_STARTED", target_duration_ms: 60_000 }] });
    vi.mocked(listCapabilityOptions).mockReset().mockResolvedValue(capabilityResponse([
      capabilityOption("prof-v1", "Qwen 故事模型", "本地故事拆解"),
    ]));
    vi.mocked(cancelJob).mockReset().mockResolvedValue({ job: {} as never });
    vi.mocked(deleteJob).mockReset().mockResolvedValue({ deleted: true, job_id: "job-failed", deleted_at: "2026-08-29T00:00:00Z", idempotent: false });
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

  it("uploads a browser-selected document, previews, then commits with the frozen preview hash", async () => {
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    expect(screen.queryByRole("button", { name: "确认并建立项目副本" })).not.toBeInTheDocument();
    await uploadDocument();
    expect(uploadScriptDocument).toHaveBeenCalledWith("project-1", expect.objectContaining({ name: "episode.md" }));
    expect(screen.getByText("原文件与项目副本")).toBeInTheDocument();
    expect(screen.getByLabelText("文件显示名")).toHaveTextContent(imported.stored_source.display_name);
    expect(screen.getByLabelText("服务器绝对路径")).toHaveTextContent(imported.stored_source.server_absolute_path);
    expect(screen.getByLabelText("项目相对路径")).toHaveTextContent(imported.stored_source.rel_path);
    expect(screen.getByRole("link", { name: "下载原稿" })).toHaveAttribute("href", imported.stored_source.download_url);
    expect(screen.getByRole("link", { name: "下载原稿" })).toHaveAttribute("download", imported.stored_source.download_filename);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    fireEvent.click(screen.getByRole("button", { name: "复制绝对路径" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(imported.stored_source.server_absolute_path));
    const commit = screen.getByRole("button", { name: "确认并建立项目副本" }) as HTMLButtonElement;
    expect(commit.disabled).toBe(false);
    fireEvent.click(commit);
    await waitFor(() => expect(commitImportSession).toHaveBeenCalledWith("session-1", {
      expected_preview_hash: "a".repeat(64),
      source_paragraph_start: 1,
      source_paragraph_end: 2,
    }));
    expect(await screen.findByText("已建立可追溯的项目副本")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "确认并建立项目副本" })).not.toBeInTheDocument();
    expect(screen.getByText("原文件与项目副本")).toBeInTheDocument();
  });

  it("opens the native file picker from the visible upload button", () => {
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    const input = screen.getByLabelText("选择本地文档") as HTMLInputElement;
    const inputClick = vi.spyOn(input, "click");

    fireEvent.click(screen.getByRole("button", { name: "选择并上传文档" }));

    expect(inputClick).toHaveBeenCalledTimes(1);
  });

  it("selects visible paragraph ranges with Shift and complete chapters with one action", async () => {
    vi.mocked(getImportSessionParagraphs).mockResolvedValueOnce({
      session_id: "session-1",
      source_document_version_id: "source-v1",
      start_paragraph: 1,
      end_paragraph: 4,
      total_paragraph_count: 4,
      items: [
        { number: 1, text: "第一章", source_start: 0, source_end: 3, is_heading: true },
        { number: 2, text: "雨夜来信", source_start: 5, source_end: 9, is_heading: false },
        { number: 3, text: "第二章", source_start: 11, source_end: 14, is_heading: true },
        { number: 4, text: "清晨重逢", source_start: 16, source_end: 20, is_heading: false },
      ],
      chapters: [
        { title: "第一章", start_paragraph: 1, end_paragraph: 2 },
        { title: "第二章", start_paragraph: 3, end_paragraph: 4 },
      ],
      has_previous: false,
      has_more: false,
      read_only: true,
    }).mockResolvedValue({
      session_id: "session-1",
      source_document_version_id: "source-v1",
      start_paragraph: 3,
      end_paragraph: 4,
      total_paragraph_count: 4,
      items: [
        { number: 3, text: "第二章", source_start: 11, source_end: 14, is_heading: true },
        { number: 4, text: "清晨重逢", source_start: 16, source_end: 20, is_heading: false },
      ],
      chapters: [
        { title: "第一章", start_paragraph: 1, end_paragraph: 2 },
        { title: "第二章", start_paragraph: 3, end_paragraph: 4 },
      ],
      has_previous: true,
      has_more: false,
      read_only: true,
    });
    vi.mocked(uploadScriptDocument).mockResolvedValueOnce({
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
    await uploadDocument("chaptered.txt");

    expect(screen.getByRole("button", { name: /第 1 段.*第一章/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /第 2 段.*雨夜来信/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /第 3 段.*第二章/ })).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(screen.getByRole("button", { name: /第 2 段.*雨夜来信/ }));
    fireEvent.click(screen.getByRole("button", { name: /第 1 段.*第一章/ }), { shiftKey: true });
    expect(screen.getByRole("button", { name: /第 1 段.*第一章/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /第 2 段.*雨夜来信/ })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: /第二章.*第 3–4 段/ }));
    expect(await screen.findByRole("button", { name: /第 3 段.*第二章/ })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByRole("button", { name: /第 4 段.*清晨重逢/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("clears the previous paragraph page while a distant page is loading", async () => {
    let resolveSecondPage: ((value: Awaited<ReturnType<typeof getImportSessionParagraphs>>) => void) | undefined;
    vi.mocked(uploadScriptDocument).mockResolvedValueOnce({
      import: {
        ...imported,
        preview: {
          ...imported.preview,
          paragraph_count: 80,
          paragraphs: ["第一页正文"],
        },
      },
    });
    vi.mocked(getImportSessionParagraphs).mockImplementation((_sessionId, start) => {
      if (start === 1) {
        return Promise.resolve({
          session_id: "session-1",
          source_document_version_id: "source-v1",
          start_paragraph: 1,
          end_paragraph: 40,
          total_paragraph_count: 80,
          items: [{ number: 1, text: "第一页正文", source_start: 0, source_end: 5, is_heading: false }],
          chapters: [],
          has_previous: false,
          has_more: true,
          read_only: true,
        });
      }
      return new Promise((resolve) => { resolveSecondPage = resolve; });
    });

    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    await uploadDocument("long.txt");
    expect(await screen.findByText("第一页正文")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));

    expect(await screen.findByText("正在读取第 41 段起的正文…")).toBeInTheDocument();
    expect(screen.queryByText("第一页正文")).not.toBeInTheDocument();

    resolveSecondPage?.({
      session_id: "session-1",
      source_document_version_id: "source-v1",
      start_paragraph: 41,
      end_paragraph: 80,
      total_paragraph_count: 80,
      items: [{ number: 41, text: "第二页正文", source_start: 10, source_end: 15, is_heading: false }],
      chapters: [],
      has_previous: true,
      has_more: false,
      read_only: true,
    });
    expect(await screen.findByText("第二页正文")).toBeInTheDocument();
  });

  it("uses one local-upload path without server pickers or pasted path text", () => {
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    expect(screen.getByRole("button", { name: "选择并上传文档" })).toBeInTheDocument();
    expect(screen.getByText("仅接受实际文件，不接受路径文字")).toBeInTheDocument();
    expect(screen.queryByText("Windows 服务端中的文档绝对路径")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "浏览…" })).not.toBeInTheDocument();
    expect(uploadScriptDocument).not.toHaveBeenCalled();
  });

  it("triggers AI script breakdown after commit and surfaces draft readiness", async () => {
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    await uploadDocument();

    fireEvent.click(screen.getByRole("button", { name: "确认并建立项目副本" }));
    await screen.findByText("已建立可追溯的项目副本");

    expect(screen.getByRole("heading", { name: "这段正文准备做成哪一集？" })).toBeInTheDocument();
    expect(screen.getByText(/这里只生成待审核草稿，不会覆盖该集现有内容/)).toBeInTheDocument();
    expect(screen.getByLabelText("草稿对应分集")).toHaveValue("ep-1");

    const breakdownBtn = await screen.findByRole("button", { name: "为第 1 集生成拆解草稿" });
    expect(breakdownBtn).toBeInTheDocument();

    fireEvent.click(breakdownBtn);
    expect(await screen.findByText(/AI 拆解已在后台排队/)).toBeInTheDocument();
    expect(screen.getByText("60 秒")).toBeInTheDocument();
    expect(screen.getByText("48–72 秒")).toBeInTheDocument();
    expect(screen.getByLabelText("本集原文起始段")).toHaveValue(1);
    expect(screen.getByLabelText("本集原文结束段")).toHaveValue(2);
    expect(requestScriptBreakdown).toHaveBeenCalledWith("session-1", "prof-v1", "ep-1", expect.any(String), {
      sourceParagraphStart: 1,
      sourceParagraphEnd: 2,
    });
  });

  it("blocks an invalid episode source range before creating a Job", async () => {
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    await uploadDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认并建立项目副本" }));
    await screen.findByText("已建立可追溯的项目副本");
    fireEvent.change(screen.getByLabelText("本集原文起始段"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("本集原文结束段"), { target: { value: "1" } });
    expect(screen.getByRole("button", { name: "为第 1 集生成拆解草稿" })).toBeDisabled();
    expect(requestScriptBreakdown).not.toHaveBeenCalled();
  });

  it("requires an explicitly published global story model and never invents or synchronizes a fallback Profile", async () => {
    vi.mocked(listCapabilityOptions).mockResolvedValue(capabilityResponse([]));
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    await uploadDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认并建立项目副本" }));
    await screen.findByText("已建立可追溯的项目副本");
    expect(screen.getByRole("button", { name: "为第 1 集生成拆解草稿" })).toBeDisabled();
    expect(screen.getByRole("link", { name: /前往能力与模型/ }).getAttribute("href")).toBe("/system/capabilities?view=resources");
    expect(requestScriptBreakdown).not.toHaveBeenCalled();
  });

  it("lets the user choose a published global story model and freezes that selection into the Job", async () => {
    vi.mocked(listCapabilityOptions).mockResolvedValue(capabilityResponse([
      capabilityOption("prof-v1", "Qwen 故事模型", "Qwen 拆解配置"),
      capabilityOption("prof-v2", "DeepSeek 故事模型", "DeepSeek 拆解配置", 2),
    ]));
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    await uploadDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认并建立项目副本" }));
    await screen.findByText("已建立可追溯的项目副本");

    const selector = screen.getByLabelText("拆解模型");
    expect(selector).toHaveValue("");
    fireEvent.change(selector, { target: { value: "prof-v2" } });
    expect(screen.getByText(/本次固定：DeepSeek 故事模型/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "为第 1 集生成拆解草稿" }));

    await waitFor(() => expect(requestScriptBreakdown).toHaveBeenCalledWith("session-1", "prof-v2", "ep-1", expect.any(String), {
      sourceParagraphStart: 1,
      sourceParagraphEnd: 2,
    }));
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
        input_snapshot: { model: "deepseek-r1:14b", profile_version_id: "historical-profile" },
      }],
    });
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    expect(await screen.findByText("本地模型正在生成（worker 心跳正常）")).toBeInTheDocument();
    expect(screen.getByText(/模型：deepseek-r1:14b/)).toBeInTheDocument();
    expect(screen.getByLabelText("AI 拆解正在由本地模型生成")).not.toHaveAttribute("value");
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

  it("deletes a terminal task after explicit confirmation", async () => {
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
      }],
    });
    vi.spyOn(window, "confirm").mockReturnValueOnce(true);
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "删除记录" }));
    await waitFor(() => expect(deleteJob).toHaveBeenCalledWith("job-failed"));
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
    expect(await screen.findByText("本地模型正在生成（worker 心跳正常）")).toBeInTheDocument();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await client.invalidateQueries({ queryKey: queryKeys.scriptBreakdown.jobs("project-1") });
    await waitFor(() => expect(onDraftReady).toHaveBeenCalledWith(expect.objectContaining({ id: "job-transition", state: "SUCCEEDED" })));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.scriptBreakdown.all("project-1") });
  });

  it("invalidates a prepared preview when another browser file is selected", async () => {
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    await uploadDocument();
    vi.mocked(uploadScriptDocument).mockReturnValueOnce(new Promise(() => undefined));
    const input = screen.getByLabelText("选择本地文档") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["第二稿"], "second.txt", { type: "text/plain" })] } });
    await waitFor(() => expect(screen.getByText("正在上传：second.txt")).toBeInTheDocument());
    expect(screen.queryByText("预览已就绪")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认并建立项目副本" })).not.toBeInTheDocument();
  });
});
