import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createShotRevision, markShotProductionReady } from "../../generated/api";
import { DirectorShotEditor } from "./DirectorShotEditor";

vi.mock("../../generated/api", () => ({ createShotRevision: vi.fn(), markShotProductionReady: vi.fn() }));

function renderEditor(shot: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const onChanged = vi.fn();
  render(<QueryClientProvider client={client}><DirectorShotEditor shot={shot} onChanged={onChanged} /></QueryClientProvider>);
  return onChanged;
}

describe("DirectorShotEditor", () => {
  beforeEach(() => {
    vi.mocked(createShotRevision).mockReset().mockResolvedValue({ shot_revision: { id: "revision-2" } });
    vi.mocked(markShotProductionReady).mockReset().mockResolvedValue({ shot: { id: "shot-1", status: "READY" } });
  });

  it("shows exact missing fields and saves a new frozen revision", async () => {
    const onChanged = renderEditor({ id: "shot-1", code: "S001", status: "DRAFT", current_revision_id: "revision-1", current_revision: {} });
    expect(screen.getByRole("status").textContent).toContain("还缺 7 项");
    fireEvent.change(screen.getByLabelText("景别"), { target: { value: "CLOSEUP" } });
    fireEvent.change(screen.getByLabelText("时长"), { target: { value: "4000" } });
    fireEvent.click(screen.getByRole("button", { name: "保存新 revision" }));
    await waitFor(() => expect(createShotRevision).toHaveBeenCalled());
    expect(vi.mocked(createShotRevision).mock.calls[0][0]).toBe("shot-1");
    expect(vi.mocked(createShotRevision).mock.calls[0][2]).toBe(true);
    expect(onChanged).toHaveBeenCalled();
  });

  it("enables Production Ready only for a complete directed revision", async () => {
    const fields = { shot_type: "CLOSEUP", composition: "center", subject_action: "turn", camera_plan: "STATIC", target_duration_ms: 4000, dialogue: "", environment: "", continuity: "same", creative_intent: "focus" };
    renderEditor({ id: "shot-1", code: "S001", status: "DIRECTED", current_revision_id: "revision-2", current_revision: fields, production_readiness: { state: "DIRECTED", blockers: [] } });
    const button = screen.getByRole("button", { name: "标记 Production Ready" }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(markShotProductionReady).toHaveBeenCalledWith("shot-1"));
  });
});
