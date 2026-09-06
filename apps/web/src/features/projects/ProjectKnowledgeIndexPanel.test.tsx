import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectKnowledgeIndexPanel } from "./ProjectKnowledgeIndexPanel";
import {
  listModelPlatformProjectKnowledgeIndexes,
  prepareModelPlatformProjectKnowledgeIndex,
  queueModelPlatformProjectKnowledgeIndex,
  searchModelPlatformProjectKnowledge,
} from "../model-platform-v2/api";

vi.mock("../model-platform-v2/api", () => ({
  listModelPlatformProjectKnowledgeIndexes: vi.fn(),
  prepareModelPlatformProjectKnowledgeIndex: vi.fn(),
  queueModelPlatformProjectKnowledgeIndex: vi.fn(),
  searchModelPlatformProjectKnowledge: vi.fn(),
}));

function renderPanel() {
  return render(<MemoryRouter><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ProjectKnowledgeIndexPanel projectId="project-1" sourceDocumentVersionId="source-v1" />
  </QueryClientProvider></MemoryRouter>);
}

describe("ProjectKnowledgeIndexPanel", () => {
  beforeEach(() => {
    vi.mocked(listModelPlatformProjectKnowledgeIndexes).mockReset();
    vi.mocked(prepareModelPlatformProjectKnowledgeIndex).mockReset();
    vi.mocked(queueModelPlatformProjectKnowledgeIndex).mockReset();
    vi.mocked(searchModelPlatformProjectKnowledge).mockReset();
  });

  it("prepares only the committed source document version and never asks for a path", async () => {
    vi.mocked(listModelPlatformProjectKnowledgeIndexes).mockResolvedValue({ items: [], count: 0, read_only: true });
    vi.mocked(prepareModelPlatformProjectKnowledgeIndex).mockResolvedValue({ index: {
      index_run_id: "knowledge-1", execution_profile_version_id: "profile-1", chunk_count: 12, reused: false, status: "PREPARED", attempt_no: 1,
    } });
    renderPanel();
    await screen.findByRole("button", { name: "准备知识索引" });
    await waitFor(() => expect(screen.getByRole("button", { name: "准备知识索引" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "准备知识索引" }));
    await waitFor(() => expect(prepareModelPlatformProjectKnowledgeIndex).toHaveBeenCalledWith("project-1", "source-v1"));
    expect(screen.queryByLabelText(/路径/i)).not.toBeInTheDocument();
  });

  it("queues a prepared run and describes its bounded background behavior", async () => {
    vi.mocked(listModelPlatformProjectKnowledgeIndexes).mockResolvedValue({ items: [{
      id: "knowledge-1", source_document_version_id: "source-v1", execution_profile_version_id: "profile-1",
      chunk_count: 12, attempt_no: 1, retry_of_index_run_id: null, completed_batch_count: 0, batch_count: 1, status: "PREPARED", failure_code: null,
      created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z",
    }], count: 1, read_only: true });
    vi.mocked(queueModelPlatformProjectKnowledgeIndex).mockResolvedValue({ index: {
      index_run_id: "knowledge-1", queued_batch_count: 1, job_ids: ["job-1"], status: "QUEUED",
    } });
    renderPanel();
    await screen.findByText("等待入队");
    expect(screen.getByText(/不使用 Ollama 对话模型/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "提交本机 Embedding" }));
    await waitFor(() => expect(queueModelPlatformProjectKnowledgeIndex).toHaveBeenCalledWith("knowledge-1"));
    expect(await screen.findByText(/已提交 1 个受控 Embedding 批次/)).toBeInTheDocument();
  });

  it("searches a successful V2 index with text only", async () => {
    vi.mocked(listModelPlatformProjectKnowledgeIndexes).mockResolvedValue({ items: [{
      id: "knowledge-1", source_document_version_id: "source-v1", execution_profile_version_id: "profile-1",
      chunk_count: 12, attempt_no: 2, retry_of_index_run_id: "knowledge-0", completed_batch_count: 1, batch_count: 1, status: "SUCCEEDED", failure_code: null,
      created_at: "2026-08-29T00:00:00Z", updated_at: "2026-08-29T00:00:00Z",
    }], count: 1, read_only: true });
    vi.mocked(searchModelPlatformProjectKnowledge).mockResolvedValue({ search: {
      execution_profile_version_id: "profile-1",
      items: [{ index_run_id: "knowledge-1", source_document_version_id: "source-v1", ordinal: 2, source_start: 12, source_end: 26, excerpt: "沈砚把照骨灯护在怀里。", score: 0.884 }],
    } });
    renderPanel();
    const input = await screen.findByPlaceholderText("输入一个与当前文档有关的问题");
    fireEvent.change(input, { target: { value: "主角做了什么？" } });
    fireEvent.click(screen.getByRole("button", { name: "检索" }));
    await waitFor(() => expect(searchModelPlatformProjectKnowledge).toHaveBeenCalledWith("project-1", "主角做了什么？", 5));
    expect(await screen.findByText("命中第 2 段")).toBeInTheDocument();
    expect(screen.getByText("沈砚把照骨灯护在怀里。")).toBeInTheDocument();
    expect(screen.getByText(/原文偏移 12–26/)).toBeInTheDocument();
  });
});
