import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DirectorTakeAdoption } from "./DirectorTakeAdoption";
import type { DirectorDeskCandidate } from "./types";

function candidate(index: number, overrides: Partial<DirectorDeskCandidate> = {}): DirectorDeskCandidate {
  return {
    id: `variant-${index}`, intent_id: "intent", variant_no: index, variant_type: "STANDARD", parent_variant_id: null,
    branch_reason: "", status: "SUCCEEDED", is_stale: false, stale_reason: null, media_asset_id: `asset-${index}`,
    media_kind: "VIDEO", media_version_id: `media-${index}`, version_no: 1, take_no: index, stage: "FORMAL",
    rel_path: null, mime_type: "video/mp4", duration_ms: 1_000, integrity_status: "VERIFIED", selected: index === 1,
    approved: false, created_at: "2026-08-20T00:00:00Z", ...overrides,
  };
}

afterEach(() => cleanup());

describe("DirectorTakeAdoption", () => {
  it("opens confirmation on drop and writes only after confirm", async () => {
    const onAdopt = vi.fn().mockResolvedValue(undefined);
    render(<DirectorTakeAdoption candidates={[candidate(1), candidate(2)]} activeCandidateId="media-1" currentCandidateId="media-1" onActivate={vi.fn()} onAdopt={onAdopt} />);
    const dataTransfer = { setData: vi.fn(), getData: vi.fn().mockReturnValue("media-2"), effectAllowed: "none", dropEffect: "none" };
    fireEvent.dragStart(screen.getByText("Take 2").closest("figure")!, { dataTransfer });
    expect(dataTransfer.setData).toHaveBeenCalledWith("application/x-director-frame-candidate", expect.stringContaining('"media_kind":"VIDEO"'));
    fireEvent.drop(screen.getByLabelText("候选采用槽"), { dataTransfer });
    expect(onAdopt).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "确认采用" }));
    await waitFor(() => expect(onAdopt).toHaveBeenCalledWith(expect.objectContaining({ media_version_id: "media-2" }), "FORMAL_SELECTION"));
  });

  it("uses the same confirmation path for the keyboard-accessible button", () => {
    render(<DirectorTakeAdoption candidates={[candidate(1), candidate(2)]} activeCandidateId="media-1" currentCandidateId="media-1" onActivate={vi.fn()} onAdopt={vi.fn()} />);
    fireEvent.click(screen.getAllByRole("button", { name: "采用" })[0]);
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("undoes by creating a superseding selection for the previous candidate", async () => {
    const onAdopt = vi.fn().mockResolvedValue(undefined);
    render(<DirectorTakeAdoption candidates={[candidate(1), candidate(2)]} activeCandidateId="media-1" currentCandidateId="media-1" onActivate={vi.fn()} onAdopt={onAdopt} undoWindowMs={60_000} />);
    fireEvent.click(screen.getAllByRole("button", { name: "采用" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "确认采用" }));
    await screen.findByRole("button", { name: "撤销本次采用" });
    fireEvent.click(screen.getByRole("button", { name: "撤销本次采用" }));
    await waitFor(() => expect(onAdopt).toHaveBeenNthCalledWith(2, expect.objectContaining({ media_version_id: "media-1" }), "FORMAL_SELECTION"));
  });

  it("explains stale and unmappable candidates instead of allowing adoption", () => {
    render(<DirectorTakeAdoption candidates={[candidate(2, { is_stale: true, stale_reason: "输入已变更" }), candidate(3, { stage: null })]} activeCandidateId={null} currentCandidateId={null} onActivate={vi.fn()} onAdopt={vi.fn()} />);
    expect(screen.getByText(/候选已失效：输入已变更/)).toBeTruthy();
    expect(screen.getByText(/缺少可用的 selection_type/)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "采用" }).every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
  });
});
