import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createSubtitleRevision, listScriptBreakdownDrafts, listSubtitleStyleTemplates } from "../../generated/api";
import { SubtitleRevisionPanel } from "./SubtitleRevisionPanel";

vi.mock("../../generated/api", () => ({
  createSubtitleRevision: vi.fn(),
  getSubtitleStyleTemplate: vi.fn(),
  listScriptBreakdownDrafts: vi.fn(),
  listSubtitleStyleTemplates: vi.fn(),
  saveSubtitleStyleTemplate: vi.fn(),
}));

describe("SubtitleRevisionPanel source authority", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listSubtitleStyleTemplates).mockResolvedValue({ items: [] });
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({
      items: [{
        id: "draft-1", project_id: "project-1", source_document_version_id: "source-secret-1", import_session_id: "import-1",
        status: "READY", source_document_code: "EP03_SCRIPT", source_document_title: "第三集剧本", draft: {}, confidence: {},
        profile_version_id: null, evidence_status: "COMPLETE", application_status: "APPLIED", automatic_apply: false,
        requires_human_action: true, created_at: "2026-08-20T00:00:00Z",
      }], automatic_apply: false, requires_human_action: true,
    });
  });

  it("uses a semantic project document selector and sends the immutable version as authority", async () => {
    vi.mocked(createSubtitleRevision).mockResolvedValue({ subtitle: { revision_no: 1, format: "SRT", cues: [{ id: "cue-1" }] } } as unknown as Awaited<ReturnType<typeof createSubtitleRevision>>);
    render(<SubtitleRevisionPanel episodeId="episode-3" projectId="project-1" />);

    const select = await screen.findByRole("combobox", { name: /源剧本文档版本/ });
    await waitFor(() => expect((select as HTMLSelectElement).value).toBe("source-secret-1"));
    expect(screen.getByRole("option", { name: "第三集剧本 · EP03_SCRIPT · 已应用" })).toBeTruthy();
    expect(document.body.textContent).not.toContain("source-secret-1");

    fireEvent.change(screen.getByLabelText("字幕 cues JSON（start_us/end_us/text）"), { target: { value: '[{"start_us":0,"end_us":1000000,"text":"你好"}]' } });
    fireEvent.click(screen.getByRole("button", { name: "创建字幕 revision" }));
    await waitFor(() => expect(createSubtitleRevision).toHaveBeenCalledWith("episode-3", expect.objectContaining({ authority: { text_authority: "SCRIPT", source_document_version_id: "source-secret-1" } })));
  });

  it("keeps the current episode authority usable when the catalogue fails", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockRejectedValue(new Error("offline"));
    render(<SubtitleRevisionPanel episodeId="episode-3" projectId="project-1" defaultSourceDocumentVersionId="current-secret" />);
    expect((await screen.findByRole("alert")).textContent).toContain("仍可沿用当前本集字幕权威版本");
    expect(screen.getByRole("option", { name: "当前本集字幕权威版本" })).toBeTruthy();
    expect(document.body.textContent).not.toContain("current-secret");
    expect((screen.getByRole("button", { name: "创建字幕 revision" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
