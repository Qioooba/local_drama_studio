import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { compareCreativeEntryRevisions, createCreativeEntry, createCreativeEntryRevision, getCreativeEntry, listCreativeEntries, restoreCreativeEntryRevision } from "../../generated/api";
import { CreativeLibrary } from "./CreativeLibrary";

vi.mock("../../generated/api", () => ({ compareCreativeEntryRevisions: vi.fn(), createCreativeEntry: vi.fn(), createCreativeEntryRevision: vi.fn(), getCreativeEntry: vi.fn(), listCreativeEntries: vi.fn(), restoreCreativeEntryRevision: vi.fn() }));

const entry = { id: "entry-1", project_id: "project-1", kind: "CHARACTER", code: "CHAR_MOTHER", title: "母亲", current_revision_id: "rev-2", revision_no: 2, content_hash: "b".repeat(64), change_note: "改服装", content: { costume: "灰衣" }, revision: 2 };
const revisions = [{ id: "rev-2", entry_id: "entry-1", revision_no: 2, parent_revision_id: "rev-1", restored_from_revision_id: null, content_hash: "b".repeat(64), change_note: "改服装", content: { costume: "灰衣" }, created_at: "now" }, { id: "rev-1", entry_id: "entry-1", revision_no: 1, parent_revision_id: null, restored_from_revision_id: null, content_hash: "a".repeat(64), change_note: "初版", content: { costume: "蓝衣" }, created_at: "before" }];

function renderLibrary() { const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); return render(<QueryClientProvider client={client}><CreativeLibrary projectId="project-1" /></QueryClientProvider>); }

describe("CreativeLibrary", () => {
  beforeEach(() => {
    vi.mocked(listCreativeEntries).mockReset().mockResolvedValue({ items: [entry] });
    vi.mocked(getCreativeEntry).mockReset().mockResolvedValue({ entry, revisions });
    vi.mocked(compareCreativeEntryRevisions).mockReset().mockResolvedValue({ comparison: { entry_id: "entry-1", left: revisions[1], right: revisions[0], changes: [{ field: "costume", before: "蓝衣", after: "灰衣" }], read_only: true } });
    vi.mocked(createCreativeEntry).mockReset().mockResolvedValue({ entry });
    vi.mocked(createCreativeEntryRevision).mockReset().mockResolvedValue({ revision: revisions[0] });
    vi.mocked(restoreCreativeEntryRevision).mockReset().mockResolvedValue({ revision: revisions[0] });
  });

  it("shows revision history comparison and never labels restore as pointer rollback", async () => {
    renderLibrary();
    await screen.findByText("CHAR_MOTHER");
    await waitFor(() => expect(screen.getByText(/蓝衣/)).toBeTruthy());
    expect(screen.getByRole("button", { name: "从所选旧版派生回退 revision" })).toBeTruthy();
  });

  it("creates an explicitly typed structured entry", async () => {
    renderLibrary();
    fireEvent.click(screen.getByText("新建创作资料"));
    fireEvent.change(screen.getByLabelText("类型"), { target: { value: "VOICE" } });
    fireEvent.change(screen.getByLabelText("Code"), { target: { value: "VOICE_MOTHER" } });
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "母亲声音" } });
    fireEvent.change(screen.getByLabelText("初始内容 JSON"), { target: { value: '{"timbre":"warm"}' } });
    fireEvent.change(screen.getByLabelText("建立说明"), { target: { value: "建立声音资料" } });
    fireEvent.click(screen.getByRole("button", { name: "建立并保存 revision 1" }));
    await waitFor(() => expect(createCreativeEntry).toHaveBeenCalledWith({ project_id: "project-1", kind: "VOICE", code: "VOICE_MOTHER", title: "母亲声音", content: { timbre: "warm" }, change_note: "建立声音资料" }));
  });
});
