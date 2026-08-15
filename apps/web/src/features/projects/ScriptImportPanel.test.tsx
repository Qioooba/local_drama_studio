import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { commitImportSession, importScriptDocument, pickLocalDocumentFile } from "../../generated/api";
import { ScriptImportPanel } from "./ScriptImportPanel";

vi.mock("../../generated/api", () => ({ commitImportSession: vi.fn(), importScriptDocument: vi.fn(), pickLocalDocumentFile: vi.fn() }));

const imported = {
  source_document_id: "source-1",
  source_document_version_id: "source-v1",
  import_session_id: "session-1",
  media_version_id: "media-1",
  status: "PREVIEW_READY" as const,
  preview_hash: "a".repeat(64),
  preview: { character_count: 12, paragraph_count: 2, paragraphs: ["第一场", "人物进入"], requires_llm_confirmation: true as const },
};

describe("ScriptImportPanel", () => {
  beforeEach(() => {
    vi.mocked(pickLocalDocumentFile).mockReset().mockResolvedValue({ selection: { selected: true, path: "D:\\Scripts\\episode.md", uploaded: false, copied: false } });
    vi.mocked(importScriptDocument).mockReset().mockResolvedValue({ import: imported });
    vi.mocked(commitImportSession).mockReset().mockResolvedValue({ commit: { ...imported, id: "session-1", project_id: "project-1", status: "COMMITTED", revision: 2, validation: { valid: true, issue_count: 0 }, items: [], source_document_version: {}, idempotent: false, source_preserved: true } });
  });

  it("uses the native picker, previews, then commits with the frozen preview hash", async () => {
    render(<ScriptImportPanel projectId="project-1" />);
    const commit = screen.getByRole("button", { name: "确认 commit（不覆盖母本）" }) as HTMLButtonElement;
    expect(commit.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "浏览…" }));
    await waitFor(() => expect((screen.getByPlaceholderText(/episode-01/) as HTMLInputElement).value).toBe("D:\\Scripts\\episode.md"));
    fireEvent.click(screen.getByRole("button", { name: "建立源版本并解析预览" }));
    expect(await screen.findByText(/PREVIEW_READY/)).toBeTruthy();
    expect(commit.disabled).toBe(false);
    fireEvent.click(commit);
    await waitFor(() => expect(commitImportSession).toHaveBeenCalledWith("session-1", "a".repeat(64)));
    expect(await screen.findByText(/COMMITTED/)).toBeTruthy();
  });

  it("invalidates a prepared preview when the path changes", async () => {
    render(<ScriptImportPanel projectId="project-1" />);
    const path = screen.getByPlaceholderText(/episode-01/);
    fireEvent.change(path, { target: { value: "D:\\Scripts\\first.txt" } });
    fireEvent.click(screen.getByRole("button", { name: "建立源版本并解析预览" }));
    await screen.findByText(/PREVIEW_READY/);
    fireEvent.change(path, { target: { value: "D:\\Scripts\\second.txt" } });
    expect(screen.queryByText(/PREVIEW_READY/)).toBeNull();
    expect((screen.getByRole("button", { name: "确认 commit（不覆盖母本）" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
