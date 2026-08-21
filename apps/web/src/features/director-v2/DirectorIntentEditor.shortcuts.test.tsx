import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { saveDirectorIntentRevision } from "./directorIntentClient";
import { DirectorIntentEditor } from "./DirectorIntentEditor";

vi.mock("./directorIntentClient", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("./directorIntentClient")>();
  return { ...original, saveDirectorIntentRevision: vi.fn().mockResolvedValue({ id: "revision-2", revision_no: 2, is_frozen: false, fields: {} }) };
});

const revision = { id: "revision-1", revision_no: 1, is_frozen: false, fields: { shot_type: "MEDIUM", subject_action: "站立", creative_intent: "紧张", composition: { preset: "CENTER" }, performance: { emotion: "克制" }, camera_plan: { movement: "STATIC" } } };

describe("DirectorIntentEditor keyboard scope", () => {
  it("blocks Ctrl+S behind a modal and restores it after the modal closes", async () => {
    const props = { shotId: "shot-1", shotCode: "S01", currentRevision: revision, canEdit: true };
    const { rerender } = render(<DirectorIntentEditor {...props} keyboardShortcutsEnabled={false} />);
    fireEvent.change(screen.getByLabelText("主体动作"), { target: { value: "转身" } });
    await act(async () => { fireEvent.keyDown(window, { key: "s", ctrlKey: true }); await Promise.resolve(); });
    expect(saveDirectorIntentRevision).not.toHaveBeenCalled();

    rerender(<DirectorIntentEditor {...props} keyboardShortcutsEnabled />);
    await act(async () => { fireEvent.keyDown(window, { key: "s", ctrlKey: true }); await Promise.resolve(); });
    expect(saveDirectorIntentRevision).toHaveBeenCalledWith(expect.objectContaining({ shotId: "shot-1", expectedRevisionNo: 1 }));
  });
});
