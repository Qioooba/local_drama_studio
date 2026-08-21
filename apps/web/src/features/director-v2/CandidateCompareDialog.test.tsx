import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CandidateCompareDialog } from "./CandidateCompareDialog";
import type { DirectorDeskCandidate } from "./types";

function candidate(index: number, kind = "VIDEO"): DirectorDeskCandidate {
  return {
    id: `variant-${index}`, intent_id: "intent", variant_no: index, variant_type: "STANDARD",
    parent_variant_id: null, branch_reason: index === 1 ? "" : "USER_REROLL", status: "SUCCEEDED",
    is_stale: false, stale_reason: null, media_asset_id: `asset-${index}`, media_kind: kind,
    media_version_id: `media-${index}`, version_no: 1, take_no: index, stage: "FORMAL",
    rel_path: `take-${index}.mp4`, mime_type: kind === "VIDEO" ? "video/mp4" : "image/png",
    duration_ms: kind === "VIDEO" ? index * 1_000 : null, integrity_status: "VERIFIED",
    selected: index === 1, approved: false, created_at: "2026-08-20T00:00:00Z",
  };
}

afterEach(() => cleanup());

describe("CandidateCompareDialog", () => {
  it("starts in 2-up and enforces the 4-up ceiling", () => {
    render(<CandidateCompareDialog candidates={[1, 2, 3, 4, 5].map((index) => candidate(index))} initialCandidateId="media-2" onClose={vi.fn()} />);
    expect(screen.getByText("已选择 2 / 4")).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/Take 3/));
    fireEvent.click(screen.getByLabelText(/Take 4/));
    expect(screen.getByText("已选择 4 / 4")).toBeTruthy();
    expect((screen.getByLabelText(/Take 5/) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText((_, node) => node?.textContent === "FORMAL · 1.00s")).toBeTruthy();
  });

  it("uses one play action for every visible video", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    render(<CandidateCompareDialog candidates={[candidate(1), candidate(2), candidate(3, "IMAGE")]} onClose={vi.fn()} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /全部播放/ })); await Promise.resolve(); });
    expect(play).toHaveBeenCalledTimes(2);
    play.mockRestore();
  });
});
