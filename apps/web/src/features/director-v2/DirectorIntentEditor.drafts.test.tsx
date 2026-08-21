import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DirectorIntentApiError, saveDirectorIntentRevision } from "./directorIntentClient";
import { DirectorIntentEditor } from "./DirectorIntentEditor";

vi.mock("./directorIntentClient", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("./directorIntentClient")>();
  return { ...original, saveDirectorIntentRevision: vi.fn() };
});

const baseFields = {
  schema_version: "director-intent.v3", shot_type: "MEDIUM", subject_action: "服务端动作", creative_intent: "紧张",
  composition: { preset: "CENTER" }, performance: { emotion: "克制" }, camera_plan: { movement: "STATIC" },
};
const revision = (revisionNo: number) => ({ id: `revision-${revisionNo}`, revision_no: revisionNo, is_frozen: false, fields: baseFields });
const key = (revisionNo: number) => `local-drama:director-intent-draft:v1:shot-1:${revisionNo}:director-intent.v3`;
const stored = (revisionNo: number, action: string) => JSON.stringify({
  format_version: 1, shot_id: "shot-1", base_revision_no: revisionNo, schema_version: "director-intent.v3",
  saved_at: "2026-08-21T12:00:00.000Z", fields: { ...baseFields, subject_action: action },
});
const props = (revisionNo: number) => ({ shotId: "shot-1", shotCode: "S01", currentRevision: revision(revisionNo), canEdit: true });

describe("DirectorIntentEditor local draft buffer", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.clearAllMocks();
    vi.mocked(saveDirectorIntentRevision).mockResolvedValue({ id: "revision-2", shot_id: "shot-1", revision_no: 2, is_frozen: false, fields: {} });
  });

  it("offers recovery without silently replacing the authoritative revision", () => {
    window.localStorage.setItem(key(1), stored(1, "本地恢复动作"));
    render(<DirectorIntentEditor {...props(1)} />);
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("服务端动作");
    expect(screen.getByText("发现可恢复的本地草稿")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "恢复草稿" }));
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("本地恢复动作");
    expect(screen.getByText("未保存")).toBeTruthy();
  });

  it("clears the shot draft after a successful authoritative save", async () => {
    render(<DirectorIntentEditor {...props(1)} />);
    fireEvent.change(screen.getByLabelText("主体动作"), { target: { value: "待保存动作" } });
    await waitFor(() => expect(window.localStorage.getItem(key(1))).not.toBeNull(), { timeout: 1_500 });
    fireEvent.click(screen.getByRole("button", { name: /保存 revision 2/ }));
    await waitFor(() => expect(saveDirectorIntentRevision).toHaveBeenCalled());
    expect(window.localStorage.getItem(key(1))).toBeNull();
  });

  it("marks an older revision draft stale and migrates it only after explicit confirmation", () => {
    window.localStorage.setItem(key(1), stored(1, "旧 revision 动作"));
    render(<DirectorIntentEditor {...props(2)} />);
    expect(screen.getByText("发现过期的本地草稿")).toBeTruthy();
    expect(screen.getByText(/基于 revision 1.*当前为 revision 2.*不会自动套用/)).toBeTruthy();
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("服务端动作");
    fireEvent.click(screen.getByRole("button", { name: "显式迁移并复核" }));
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("旧 revision 动作");
    expect(screen.getByText(/已显式迁移 revision 1/)).toBeTruthy();
  });

  it("keeps a 409 draft and marks it stale after the authoritative revision reloads", async () => {
    vi.mocked(saveDirectorIntentRevision).mockRejectedValueOnce(new DirectorIntentApiError("revision conflict", 409, "REVISION_CONFLICT", { current_revision_no: 2 }));
    const { rerender } = render(<DirectorIntentEditor {...props(1)} />);
    fireEvent.change(screen.getByLabelText("主体动作"), { target: { value: "冲突中的本地动作" } });
    await waitFor(() => expect(window.localStorage.getItem(key(1))).not.toBeNull(), { timeout: 1_500 });
    fireEvent.click(screen.getByRole("button", { name: /保存 revision 2/ }));
    expect(await screen.findByText("保存冲突")).toBeTruthy();
    expect(window.localStorage.getItem(key(1))).not.toBeNull();
    rerender(<DirectorIntentEditor {...props(2)} />);
    expect(await screen.findByText("发现过期的本地草稿")).toBeTruthy();
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("服务端动作");
  });

  it("fails safely when browser storage rejects a debounced write", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("quota", "QuotaExceededError"); });
    render(<DirectorIntentEditor {...props(1)} />);
    fireEvent.change(screen.getByLabelText("主体动作"), { target: { value: "仍可编辑" } });
    expect((await screen.findByRole("alert", {}, { timeout: 1_500 })).textContent).toContain("本地草稿写入失败");
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("仍可编辑");
    expect((screen.getByRole("button", { name: /保存 revision 2/ }) as HTMLButtonElement).disabled).toBe(false);
    setItem.mockRestore();
  });

  it("ignores malformed local JSON and preserves the server revision", () => {
    window.localStorage.setItem(key(1), "{not-json");
    render(<DirectorIntentEditor {...props(1)} />);
    expect(screen.getByRole("alert").textContent).toContain("本地草稿 JSON 已损坏");
    expect((screen.getByLabelText("主体动作") as HTMLTextAreaElement).value).toBe("服务端动作");
    expect(screen.queryByText("发现可恢复的本地草稿")).toBeNull();
  });
});
