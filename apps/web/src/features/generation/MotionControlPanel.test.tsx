import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMotionControl, listMotionControls } from "../../generated/api";
import { MotionControlPanel } from "./MotionControlPanel";

vi.mock("../../generated/api", () => ({ createMotionControl: vi.fn(), listMotionControls: vi.fn() }));

describe("MotionControlPanel", () => {
  beforeEach(() => {
    vi.mocked(listMotionControls).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(createMotionControl).mockReset().mockResolvedValue({ motion_control: { duplicate: false, id: "control-1", source_media_version_id: "source-1", control_media_version_id: "vector-1", profile_version_id: "profile-1", control_kind: "VECTOR", operation: "MOTION_BRUSH", subject_role: "subject", control_payload: {}, source_media: {}, control_media: {} } });
  });

  it("saves a local immutable control and refreshes the list", async () => {
    render(<MotionControlPanel sourceMediaVersionId="source-1" profileVersionId="profile-1" />);
    await waitFor(() => expect(listMotionControls).toHaveBeenCalledWith("source-1"));
    fireEvent.change(screen.getByLabelText("subject role"), { target: { value: "face" } });
    fireEvent.click(screen.getByRole("button", { name: "保存不可变控制" }));
    await waitFor(() => expect(createMotionControl).toHaveBeenCalledWith("source-1", expect.objectContaining({ subject_role: "face", profile_version_id: "profile-1" })));
    expect((await screen.findByRole("status")).textContent).toContain("源媒体未修改");
  });
});
