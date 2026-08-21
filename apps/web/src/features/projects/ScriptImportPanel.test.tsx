import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { cancelJob, commitImportSession, importScriptDocument, listJobs, pickLocalDocumentFile, retryJob } from "../../generated/api";
import {
  getLocalLLMStatus,
  publishLocalLLMProfile,
  requestScriptBreakdown,
  syncLocalLLMProfile,
} from "../story-workspace-v2/breakdownClient";
import { ScriptImportPanel } from "./ScriptImportPanel";

vi.mock("../../generated/api", () => ({
  commitImportSession: vi.fn(),
  importScriptDocument: vi.fn(),
  listJobs: vi.fn(),
  pickLocalDocumentFile: vi.fn(),
  cancelJob: vi.fn(),
  retryJob: vi.fn(),
}));

vi.mock("../story-workspace-v2/breakdownClient", () => ({
  getLocalLLMStatus: vi.fn(),
  syncLocalLLMProfile: vi.fn(),
  publishLocalLLMProfile: vi.fn(),
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
  return render(<MemoryRouter><QueryClientProvider client={client}>{ui}</QueryClientProvider></MemoryRouter>);
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
    vi.mocked(cancelJob).mockReset().mockResolvedValue({ job: {} as never });
    vi.mocked(retryJob).mockReset().mockResolvedValue({ job: {} as never });
    vi.mocked(getLocalLLMStatus).mockReset().mockResolvedValue({
      status: { status: "PASS", model: "qwen2.5:7b" },
    });
    vi.mocked(syncLocalLLMProfile).mockReset().mockResolvedValue({
      profile: { profile_version_id: "prof-v1", profile_code: "llm_qwen", status: "CANDIDATE" },
    });
    vi.mocked(publishLocalLLMProfile).mockReset().mockResolvedValue({
      profile: { profile_version_id: "prof-v1", status: "PUBLISHED" },
    });
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
    const commit = screen.getByRole("button", { name: "确认 commit（不覆盖母本）" }) as HTMLButtonElement;
    expect(commit.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "浏览…" }));
    await waitFor(() =>
      expect((screen.getByPlaceholderText(/episode-01/) as HTMLInputElement).value).toBe(
        "D:\\Scripts\\episode.md"
      )
    );
    fireEvent.click(screen.getByRole("button", { name: "建立源版本并解析预览" }));
    expect(await screen.findByText(/PREVIEW_READY/)).toBeTruthy();
    expect(commit.disabled).toBe(false);
    fireEvent.click(commit);
    await waitFor(() => expect(commitImportSession).toHaveBeenCalledWith("session-1", "a".repeat(64)));
    expect(await screen.findByText(/COMMITTED/)).toBeTruthy();
  });

  it("triggers AI script breakdown after commit and surfaces draft readiness", async () => {
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    const path = screen.getByPlaceholderText(/episode-01/);
    fireEvent.change(path, { target: { value: "D:\\Scripts\\first.txt" } });
    fireEvent.click(screen.getByRole("button", { name: "建立源版本并解析预览" }));
    await screen.findByText(/PREVIEW_READY/);

    fireEvent.click(screen.getByRole("button", { name: "确认 commit（不覆盖母本）" }));
    await screen.findByText(/COMMITTED/);

    const breakdownBtn = await screen.findByRole("button", { name: "提交 AI 拆解任务" });
    expect(breakdownBtn).toBeInTheDocument();

    fireEvent.click(breakdownBtn);
    expect(await screen.findByText(/AI 拆解任务已持久化排队/)).toBeInTheDocument();
    expect(requestScriptBreakdown).toHaveBeenCalledWith("session-1", "prof-v1", expect.any(String));
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

  it("invalidates a prepared preview when the path changes", async () => {
    renderWithClient(<ScriptImportPanel projectId="project-1" />);
    const path = screen.getByPlaceholderText(/episode-01/);
    fireEvent.change(path, { target: { value: "D:\\Scripts\\first.txt" } });
    fireEvent.click(screen.getByRole("button", { name: "建立源版本并解析预览" }));
    await screen.findByText(/PREVIEW_READY/);
    fireEvent.change(path, { target: { value: "D:\\Scripts\\second.txt" } });
    expect(screen.queryByText(/PREVIEW_READY/)).toBeNull();
    expect(
      (screen.getByRole("button", { name: "确认 commit（不覆盖母本）" }) as HTMLButtonElement).disabled
    ).toBe(true);
  });
});
