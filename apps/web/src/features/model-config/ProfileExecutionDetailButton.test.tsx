import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getProfileVersion } from "../../generated/api";
import { ProfileExecutionDetailButton } from "./ProfileExecutionDetailButton";

vi.mock("../../generated/api", () => ({ getProfileVersion: vi.fn() }));

describe("ProfileExecutionDetailButton", () => {
  beforeEach(() => {
    vi.mocked(getProfileVersion).mockReset().mockResolvedValue({
      profile_version: {
        title: "H3 T2V",
        version_no: 4,
        capability: "VIDEO_T2V",
        status: "PUBLISHED",
        execution: {
          runtime: { title: "Local ComfyUI", status: "READY" },
          workflow: { title: "H3 workflow", version_no: 2 },
          components: [],
          defaults: {},
          override_schema: { fields: {} },
          fingerprints: { execution: "sha256:" + "a".repeat(64), model_bundle: "a", workflow: "b", manifest: "c" },
          model_bundle: {},
        },
      },
    } as never);
  });

  it("loads details only after the selector's explicit action", async () => {
    render(<ProfileExecutionDetailButton profileVersionId="profile-v4" />);
    expect(getProfileVersion).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "查看执行详情" }));
    await waitFor(() => expect(getProfileVersion).toHaveBeenCalledWith("profile-v4"));
    expect(await screen.findByText("H3 T2V · 第 4 版执行详情")).toBeTruthy();
    expect(screen.getByText("Local ComfyUI")).toBeTruthy();
  });
});
