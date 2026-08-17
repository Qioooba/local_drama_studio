import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { applyScriptBreakdownDraft, listEpisodes, listScriptBreakdownDrafts, listSeasons, type ScriptBreakdownDraft } from "../../generated/api";
import { AIDraftReviewPanel } from "./AIDraftReviewPanel";

vi.mock("../../generated/api", () => ({ listScriptBreakdownDrafts: vi.fn(), listSeasons: vi.fn(), listEpisodes: vi.fn(), applyScriptBreakdownDraft: vi.fn() }));
function renderPanel() { const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); return render(<QueryClientProvider client={client}><AIDraftReviewPanel projectId="project-1" /></QueryClientProvider>); }

const seasonItems = [{ id: "season-1", code: "SEASON_001", title: "第 1 季" }];
const episodeItems = [{ id: "ep-1", code: "EPISODE_001", title: "第 1 集", production_status: "NOT_STARTED" }];
const draftItem: ScriptBreakdownDraft = {
  id: "draft", project_id: "project-1", source_document_version_id: "version", import_session_id: "session", status: "DRAFT_READY",
  source_document_code: "script", source_document_title: "剧本",
  draft: { scenes: [{ shots: [{}, {}] }] },
  confidence: { profile_version_id: "profile-v1", confidence: { overall: 0.8 }, questions: ["服装？"], source_passages: [{ scene_no: 1, quote: "原文", source_start: 0, source_end: 2 }] },
  profile_version_id: "profile-v1", evidence_status: "COMPLETE", application_status: "NOT_APPLIED",
  automatic_apply: false, requires_human_action: true, created_at: "now",
};

describe("AIDraftReviewPanel", () => {
  beforeEach(() => {
    vi.mocked(listScriptBreakdownDrafts).mockReset();
    vi.mocked(listSeasons).mockReset();
    vi.mocked(listEpisodes).mockReset();
    vi.mocked(applyScriptBreakdownDraft).mockReset();
    vi.mocked(listSeasons).mockResolvedValue({ items: seasonItems });
    vi.mocked(listEpisodes).mockResolvedValue({ items: episodeItems });
  });
  it("labels persisted suggestions as not applied and exposes an explicit apply action", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({ automatic_apply: false, requires_human_action: true, items: [{ ...draftItem }] });
    renderPanel();
    expect(await screen.findByText(/DRAFT_READY · NOT_APPLIED/)).toBeTruthy();
    expect(screen.getByText("1 个建议场次 · 2 个建议镜头")).toBeTruthy();
    expect(screen.getByText(/证据完整 · 置信度 80%/)).toBeTruthy();
    expect(screen.getByLabelText("选择目标集")).toBeTruthy();
    expect(screen.getByRole("button", { name: "应用到成片" })).toBeTruthy();
    expect(screen.queryByText(/已应用/)).toBeNull();
  });
  it("applies a draft to the target episode and shows the result summary", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({ automatic_apply: false, requires_human_action: true, items: [{ ...draftItem }] });
    vi.mocked(applyScriptBreakdownDraft).mockResolvedValue({
      apply: { draft_id: "draft", episode_id: "ep-1", created: { scenes: 2, shots: 4, lines: 4 }, extracted_characters: [{ name: "母亲", scene_count: 2 }, { name: "孩子", scene_count: 1 }], applied: true },
    });
    renderPanel();
    const button = await screen.findByRole("button", { name: "应用到成片" });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    expect(await screen.findByText(/应用完成：创建 2 场 · 4 镜 · 4 条对白/)).toBeTruthy();
    expect(screen.getByText(/母亲×2、孩子×1/)).toBeTruthy();
    expect(applyScriptBreakdownDraft).toHaveBeenCalledWith("draft", { episode_id: "ep-1" });
  });
  it("shows applied state and disables further application for APPLIED drafts", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({
      automatic_apply: false, requires_human_action: true,
      items: [{ ...draftItem, status: "APPLIED", application_status: "APPLIED", requires_human_action: false }],
    });
    renderPanel();
    expect(await screen.findByText(/APPLIED · APPLIED/)).toBeTruthy();
    expect(screen.getByText(/已应用：本草稿的场次\/镜头\/对白已落地为生产实体/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("does not invent suggestions for an empty production project", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({ automatic_apply: false, requires_human_action: true, items: [] });
    renderPanel();
    expect(await screen.findByText(/不会显示模拟建议/)).toBeTruthy();
  });
});
