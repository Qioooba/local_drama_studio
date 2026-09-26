/**
 * Switching paragraphs must not discard unsaved script edits (LDS-11), and a
 * conflicting save must keep the local text (§B12.7).
 *
 * The single-paragraph editor used to hold one ``editing`` object, so clicking
 * another paragraph replaced it outright — no dirty check, no draft registry —
 * and the save callback cleared the editor unconditionally, discarding a
 * paragraph the operator started editing *during* a save.
 *
 * The page now keeps one draft per paragraph, registers every unsaved paragraph
 * in the shared `draftRegistry`, and only retires the paragraph it saved.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { listCapabilityOptions, patchExplainerSegment } from "../../generated/api";
import { draftRegistry } from "../drafts/draftRegistry";
import {
  useExplainerOverview,
  useExplainerScript,
  useExplainerSegments,
} from "./useExplainerQueries";
import { ExplainerScriptPage } from "./ScriptPage";

vi.mock("../../generated/api", () => ({
  patchExplainerSegment: vi.fn(),
  createExplainerScriptRevision: vi.fn(),
  freezeExplainerScript: vi.fn(),
  getExplainerClaimEvidence: vi.fn(),
  importExplainerSource: vi.fn(),
  startExplainerResearchRun: vi.fn(),
  breakdownExplainerStory: vi.fn(),
  preflightExplainerPlan: vi.fn(),
  startExplainerRun: vi.fn(),
  listCapabilityOptions: vi.fn(),
}));
// The page also reads the overview and the real TTS segments; both must be part
// of the mock or the whole suite fails on a missing export.
vi.mock("./useExplainerQueries", () => ({
  useExplainerScript: vi.fn(),
  useExplainerOverview: vi.fn(),
  useExplainerSegments: vi.fn(),
}));

function scriptFact() {
  return {
    revision: { id: "rev-1", revision_no: 1, status: "DRAFT", content_hash: "h".repeat(64) },
    segments: [
      {
        id: "seg-a",
        canonical_segment_id: "seg_001",
        ordinal: 0,
        revision: 1,
        display_text: "第一段原文",
        spoken_text: "第一段原文",
        statement_type: "FACT",
        claim_ids_json: [],
        pronunciation_map_json: [],
        content_locked_by_human: false,
        target_duration_ms: null,
      },
      {
        id: "seg-b",
        canonical_segment_id: "seg_002",
        ordinal: 1,
        revision: 1,
        display_text: "第二段原文",
        spoken_text: "第二段原文",
        statement_type: "FACT",
        claim_ids_json: [],
        pronunciation_map_json: [],
        content_locked_by_human: false,
        target_duration_ms: null,
      },
    ],
    claims: [],
    sources: [],
    open_core_conflicts: [],
    validity: {},
  };
}

function overviewFact(policy = "ADAPT_SOURCES") {
  return {
    project_id: "project-1",
    video: { id: "video-1", title: "测试作品", input_payload_json: { script_policy: policy }, automation_mode: "MANUAL_REVIEW", revision: 1 },
    editions: [],
    beat_count: 4,
    render_type_counts: {},
    latest_run: null,
    open_issues: [],
    open_issue_count: 0,
    blocking_issue_count: 0,
    active_decisions: [],
    authority_labels: { machine: "", human: "", publication: "" },
    capability_snapshot: { probed: true },
  };
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/explainers/project-1/script?segment=seg-a"]}>
        <Routes>
          <Route path="/explainers/:projectId/script" element={<ExplainerScriptPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The paragraph's own editor only opens on an explicit action (§B2.3). */
function openFirstParagraphEditor() {
  fireEvent.click(screen.getAllByRole("button", { name: "编辑这一段" })[0]);
}

async function renderWithScript() {
  vi.mocked(useExplainerScript).mockReturnValue({ data: scriptFact(), isPending: false, isError: false } as never);
  vi.mocked(useExplainerOverview).mockReturnValue({ data: overviewFact(), isPending: false, isError: false } as never);
  vi.mocked(useExplainerSegments).mockReturnValue({
    data: { video_id: "video-1", script_revision_id: "rev-1", segments: scriptFact().segments, selected_takes: [], measured_total_ms: null, timing_status: "NOT_STARTED" },
    isPending: false,
    isError: false,
  } as never);
  mount();
  // The paragraph text is visible in the directory and in the body.
  await screen.findAllByText("第一段原文");
}

describe("ExplainerScriptPage draft protection (LDS-11)", () => {
  beforeEach(() => {
    draftRegistry.clear();
    vi.clearAllMocks();
    vi.mocked(patchExplainerSegment).mockResolvedValue({ invalidated: [] } as never);
    // A real (empty) capability answer keeps the picker out of its error state.
    vi.mocked(listCapabilityOptions).mockResolvedValue({
      capability: "LLM_STORY_PARSE",
      scope: { project_id: "project-1", episode_id: null, shot_id: null },
      selection: { mode: "AUTO", source: "NONE", profile_version_id: null, ready: false, option: null, blockers: [] },
      options: [],
      configured_runtime: null,
      summary: { total_count: 0, selectable_count: 0, blocked_count: 0 },
      repair_href: "/system/capabilities",
      read_only: true,
      runtime_contacted: false,
      network_contacted: false,
      mutated: false,
    } as never);
  });

  afterEach(() => {
    cleanup();
    draftRegistry.clear();
  });

  it("keeps the first paragraph's unsaved text when switching to another paragraph", async () => {
    await renderWithScript();
    openFirstParagraphEditor();
    const display = () => screen.getByLabelText("显示文本") as HTMLTextAreaElement;
    expect(display().value).toBe("第一段原文");

    fireEvent.change(display(), { target: { value: "第一段刚输入、尚未保存的新内容：应该保留。" } });
    expect(display().value).toContain("应该保留");

    // Switch to the second paragraph …
    fireEvent.click(screen.getAllByRole("button", { name: /第二段原文/ })[0]);
    await waitFor(() => expect((screen.getByLabelText("显示文本") as HTMLTextAreaElement).value).toBe("第二段原文"));
    // … and back to the first.
    fireEvent.click(screen.getAllByRole("button", { name: /第一段原文/ })[0]);
    await waitFor(() => expect((screen.getByLabelText("显示文本") as HTMLTextAreaElement).value).toBe(
      "第一段刚输入、尚未保存的新内容：应该保留。",
    ));
    // The paragraph whose draft was kept is marked, and the second one is not.
    expect(screen.getAllByText(/有未保存修改/).length).toBeGreaterThan(0);
  });

  it("registers the unsaved paragraph in the shared draft registry", async () => {
    await renderWithScript();
    openFirstParagraphEditor();
    fireEvent.change(screen.getByLabelText("显示文本"), { target: { value: "改了一点" } });
    await waitFor(() => expect(draftRegistry.get("explainer-script-segment:project-1:seg-a")?.dirty).toBe(true));
    expect(draftRegistry.get("explainer-script-segment:project-1:seg-a")?.entityKey).toBe("第 1 段正文");
  });

  it("marks a paragraph that still holds a draft", async () => {
    await renderWithScript();
    openFirstParagraphEditor();
    fireEvent.change(screen.getByLabelText("显示文本"), { target: { value: "改了一点" } });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getAllByText(/有未保存修改/).length).toBeGreaterThan(0);
    // Re-opening restores the draft instead of the original text.
    openFirstParagraphEditor();
    expect((screen.getByLabelText("显示文本") as HTMLTextAreaElement).value).toBe("改了一点");
  });

  it("retires only the submitted paragraph's draft after a successful save", async () => {
    await renderWithScript();
    openFirstParagraphEditor();
    const editor = () => screen.getByLabelText("显示文本") as HTMLTextAreaElement;
    fireEvent.change(editor(), { target: { value: "第一段已修正" } });
    await waitFor(() => expect(editor().value).toBe("第一段已修正"));
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    await waitFor(() => expect(patchExplainerSegment).toHaveBeenCalled());
    expect(vi.mocked(patchExplainerSegment).mock.calls[0][2]).toMatchObject({
      expected_revision: 1,
      expected_script_revision_id: "rev-1",
      display_text: "第一段已修正",
    });
    // The editor closed for the saved paragraph and its draft marker is gone.
    // (The fixture is static, so the paragraph shows the server text again — the
    // point is that this segment's draft was retired.)
    await waitFor(() => expect(screen.queryByText(/有未保存修改/)).toBeNull());
    expect(screen.queryByLabelText("显示文本")).toBeNull();
    await waitFor(() => expect(draftRegistry.getDirty().length).toBe(0));
  });

  it("keeps the local text when the save conflicts", async () => {
    vi.mocked(patchExplainerSegment).mockRejectedValue(new Error("STALE_REVISION：讲稿已被其它标签页更新"));
    await renderWithScript();
    openFirstParagraphEditor();
    fireEvent.change(screen.getByLabelText("显示文本"), { target: { value: "本地保留的文本" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    await waitFor(() => expect(screen.getByText(/本地未保存文本已保留/)).toBeTruthy());
    expect((screen.getByLabelText("显示文本") as HTMLTextAreaElement).value).toBe("本地保留的文本");
  });
});
