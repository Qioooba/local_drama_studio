import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { putShotDraftV2 } from "../../generated/api";
import { DirectorIntentEditor } from "./DirectorIntentEditor";

vi.mock("../../generated/api", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("../../generated/api")>();
  return { ...original, putShotDraftV2: vi.fn().mockResolvedValue({ shot_revision: { id: "revision-2", shot_id: "shot-1", revision_no: 2, is_frozen: false, fields: {} }, shot: { id: "shot-1", status: "DIRECTED", current_revision_id: "revision-2", revision: 2, updated_at: "now" } }) };
});

const revision = { id: "revision-1", revision_no: 1, is_frozen: false, fields: { shot_type: "MEDIUM", subject_action: "站立", creative_intent: "紧张", composition: { preset: "CENTER" }, performance: { emotion: "克制" }, camera_plan: { movement: "STATIC" } } };

describe("DirectorIntentEditor keyboard scope", () => {
  it("blocks Ctrl+S behind a modal and restores it after the modal closes", async () => {
    const props = { shotId: "shot-1", shotCode: "S01", currentRevision: revision, canEdit: true };
    const { rerender } = render(<DirectorIntentEditor {...props} keyboardShortcutsEnabled={false} />);
    fireEvent.change(screen.getByLabelText("主体动作"), { target: { value: "转身" } });
    await act(async () => { fireEvent.keyDown(window, { key: "s", ctrlKey: true }); await Promise.resolve(); });
    expect(putShotDraftV2).not.toHaveBeenCalled();

    rerender(<DirectorIntentEditor {...props} keyboardShortcutsEnabled />);
    await act(async () => { fireEvent.keyDown(window, { key: "s", ctrlKey: true }); await Promise.resolve(); });
    expect(putShotDraftV2).toHaveBeenCalledWith("shot-1", expect.objectContaining({ expected_revision_no: 1 }));
  });
});
