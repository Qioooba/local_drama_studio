import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createSubtitleRevision, getEpisodeTTSSubtitleDraftPlan, listScriptBreakdownDrafts, listSubtitleStyleTemplates } from "../../generated/api";
import { SubtitleRevisionPanel } from "./SubtitleRevisionPanel";

vi.mock("../../generated/api", () => ({
  createSubtitleRevision: vi.fn(),
  getEpisodeTTSSubtitleDraftPlan: vi.fn(),
  getProjectConfiguration: vi.fn(() => Promise.resolve({ configuration: { production_plan: null } })),
  getSubtitleStyleTemplate: vi.fn(),
  listScriptBreakdownDrafts: vi.fn(),
  listSubtitleStyleTemplates: vi.fn(),
  saveSubtitleStyleTemplate: vi.fn(),
}));

describe("SubtitleRevisionPanel source authority", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listSubtitleStyleTemplates).mockResolvedValue({ items: [] });
    vi.mocked(getEpisodeTTSSubtitleDraftPlan).mockReset();
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({
      items: [{
        id: "draft-1", project_id: "project-1", source_document_version_id: "source-secret-1", import_session_id: "import-1",
        status: "READY", revision: 1, source_document_code: "EP03_SCRIPT", source_document_title: "第三集剧本", draft: {}, confidence: {},
        model_draft_sha256: "a".repeat(64), effective_draft_revision_id: null, effective_draft_revision_no: 0, human_edited: false,
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

    fireEvent.change(screen.getByLabelText("字幕 1 结束（微秒）"), { target: { value: "1000000" } });
    fireEvent.change(screen.getByLabelText("字幕 1 文本"), { target: { value: "你好" } });
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

  it("loads a reviewable draft from current TTS selections without creating a revision", async () => {
    vi.mocked(getEpisodeTTSSubtitleDraftPlan).mockResolvedValue({ plan: {
      episode_id: "episode-3", status: "PARTIAL", ready_to_load: true,
      source_document_version_id: "source-secret-1", source_document_candidates: ["source-secret-1"], authority: {},
      cues: [{ start_us: 0, end_us: 3_000_000, text: "你好" }],
      evidence: [], missing: [{ line_id: "line-2", code: "DLG-002", reason: "TTS_SELECTION_MISSING", message: "尚未采用 TTS 候选" }], blockers: [],
      warnings: [{ code: "TTS_SELECTIONS_INCOMPLETE", message: "1 条对白未进入草稿" }],
      summary: { dialogue_count: 2, cue_count: 1, missing_count: 1, duration_us: 3_000_000 },
      text_authority: "SCRIPT", timing_authority: "SELECTED_TTS_MEDIA", requires_human_review: true,
      would_create_revision: false, read_only: true, runtime_contacted: false, network_contacted: false, mutated: false,
    } });
    render(<SubtitleRevisionPanel episodeId="episode-3" projectId="project-1" />);
    await screen.findByRole("option", { name: "第三集剧本 · EP03_SCRIPT · 已应用" });
    fireEvent.click(screen.getByRole("button", { name: "载入 TTS 字幕草稿" }));
    await waitFor(() => expect(getEpisodeTTSSubtitleDraftPlan).toHaveBeenCalledWith("episode-3", "source-secret-1"));
    expect((screen.getByLabelText("字幕 1 文本") as HTMLInputElement).value).toBe("你好");
    expect(screen.getByText("TTS 草稿部分齐全")).toBeTruthy();
    expect(screen.getByText("DLG-002：尚未采用 TTS 候选")).toBeTruthy();
    expect(createSubtitleRevision).not.toHaveBeenCalled();
  });
});
