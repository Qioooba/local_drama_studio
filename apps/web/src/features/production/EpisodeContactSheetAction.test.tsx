import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { exportEpisodeContactSheet } from "../../generated/api";
import { EpisodeContactSheetAction } from "./EpisodeContactSheetAction";

vi.mock("../../generated/api", () => ({ exportEpisodeContactSheet: vi.fn() }));

function renderAction(episodeId: string | null = "episode-1") {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><EpisodeContactSheetAction episodeId={episodeId} /></QueryClientProvider>);
}

describe("EpisodeContactSheetAction", () => {
  beforeEach(() => {
    vi.mocked(exportEpisodeContactSheet).mockReset().mockResolvedValue({ export: {
      schema_version: "localdrama.contact-sheet.v1", status: "EXPORTED", rel_path: "exports/contact-sheet-abc",
      manifest_rel_path: "exports/contact-sheet-abc/manifest.json", contact_sheet_rel_path: "exports/contact-sheet-abc/contact-sheet.html",
      export_hash: "abc", item_count: 2, reused: false, database_mutated: false, runtime_contacted: false, network_contacted: false,
    } });
  });

  it("exports the selected episode and reports the local path", async () => {
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "导出联系表" }));
    await waitFor(() => expect(exportEpisodeContactSheet).toHaveBeenCalledWith("episode-1"));
    expect((await screen.findByRole("status")).textContent).toContain("已导出 2 项");
    expect(screen.getByRole("status").textContent).toContain("contact-sheet.html");
  });

  it("stays disabled until an episode exists and exposes backend errors", async () => {
    const disabled = renderAction(null);
    expect(screen.getByRole("button", { name: "导出联系表" }).hasAttribute("disabled")).toBe(true);
    disabled.unmount();
    vi.mocked(exportEpisodeContactSheet).mockRejectedValueOnce(new Error("当前集没有已选择的图片或视频版本"));
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "导出联系表" }));
    expect((await screen.findByRole("alert")).textContent).toContain("当前集没有已选择的图片或视频版本");
  });
});
