/**
 * Switching paragraphs must not discard unsaved script edits (LDS-11).
 *
 * The page held a single ``editing`` object.  Clicking "修改这一段" on another
 * paragraph replaced it outright — no dirty check, no draft registry — and the
 * save callback cleared the editor unconditionally, so a paragraph the operator
 * started editing *during* a save could be discarded by that save's response.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { patchExplainerSegment } from "../../generated/api";
import { useExplainerScript } from "./useExplainerQueries";
import { ExplainerScriptPage } from "./ScriptPage";

vi.mock("../../generated/api", () => ({
  patchExplainerSegment: vi.fn(),
  createExplainerScriptRevision: vi.fn(),
  freezeExplainerScript: vi.fn(),
  importExplainerSource: vi.fn(),
  startExplainerResearchRun: vi.fn(),
}));
vi.mock("./useExplainerQueries", () => ({ useExplainerScript: vi.fn() }));

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

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><ExplainerScriptPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

async function openFirstParagraph() {
  vi.mocked(useExplainerScript).mockReturnValue({ data: scriptFact(), isPending: false, isError: false } as never);
  mount();
  await screen.findByText("第一段原文");
  fireEvent.click(screen.getAllByRole("button", { name: "修改这一段" })[0]);
}

describe("ExplainerScriptPage draft protection (LDS-11)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(patchExplainerSegment).mockResolvedValue({ invalidated: [] } as never);
  });

  afterEach(() => {
    cleanup();
  });

  it("keeps the first paragraph's unsaved text when switching to another paragraph", async () => {
    await openFirstParagraph();
    // The page has other textareas (reference links, pasted script), so the editor
    // fields are addressed by their labels rather than by DOM order.
    const display = () => screen.getByLabelText("显示文本") as HTMLTextAreaElement;
    expect(display().value).toBe("第一段原文");

    fireEvent.change(display(), { target: { value: "第一段刚输入、尚未保存的新内容：应该保留。" } });
    expect(display().value).toContain("应该保留");

    // Switch to the second paragraph ...
    fireEvent.click(screen.getAllByRole("button", { name: "修改这一段" })[0]);
    expect((screen.getByLabelText("显示文本") as HTMLTextAreaElement).value).toBe("第二段原文");
    // ... and back to the first.
    fireEvent.click(screen.getByRole("button", { name: "修改这一段" }));
    expect((screen.getByLabelText("显示文本") as HTMLTextAreaElement).value).toBe(
      "第一段刚输入、尚未保存的新内容：应该保留。",
    );
    // The paragraph whose draft was kept is marked, and the second one is not.
    expect(screen.getByText(/有未保存修改/)).toBeTruthy();
  });

  it("marks a paragraph that still holds a draft", async () => {
    await openFirstParagraph();
    fireEvent.change(screen.getByLabelText("显示文本"), { target: { value: "改了一点" } });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getByText(/有未保存修改/)).toBeTruthy();
    // Re-opening restores the draft instead of the original text.
    fireEvent.click(screen.getAllByRole("button", { name: "修改这一段" })[0]);
    expect((screen.getByLabelText("显示文本") as HTMLTextAreaElement).value).toBe("改了一点");
  });

  it("retires only the submitted paragraph's draft after a successful save", async () => {
    await openFirstParagraph();
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
  });

  it("keeps the local text when the save conflicts", async () => {
    vi.mocked(patchExplainerSegment).mockRejectedValue(new Error("STALE_REVISION：讲稿已被其它标签页更新"));
    await openFirstParagraph();
    fireEvent.change(screen.getByLabelText("显示文本"), { target: { value: "本地保留的文本" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    await waitFor(() => expect(screen.getByText(/本地未保存文本已保留/)).toBeTruthy());
    expect((screen.getByLabelText("显示文本") as HTMLTextAreaElement).value).toBe("本地保留的文本");
  });
});

