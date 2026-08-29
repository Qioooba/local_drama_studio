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
      artifact: { scope: "PROJECT", kind: "DIRECTORY", display_name: "EP001 · 第一集 · 联系表", server_absolute_path: "F:\\DramaProjects\\p\\exports\\contact-sheet-abc", rel_path: "exports/contact-sheet-abc", download_url: "/api/v1/episodes/episode-1/contact-sheet:download?rel_path=exports%2Fcontact-sheet-abc", download_filename: "contact-sheet-abc.zip" },
      manifest_rel_path: "exports/contact-sheet-abc/manifest.json", contact_sheet_rel_path: "exports/contact-sheet-abc/contact-sheet.html",
      export_hash: "abc", item_count: 2, reused: false, database_mutated: false, runtime_contacted: false, network_contacted: false,
    } });
  });

  it("exports the selected episode and reports the local path", async () => {
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "导出联系表" }));
    await waitFor(() => expect(exportEpisodeContactSheet).toHaveBeenCalledWith("episode-1"));
    expect(await screen.findByText("EP001 · 第一集 · 联系表")).toBeTruthy();
    expect(screen.getByText(/2 项已选媒体/)).toBeTruthy();
    expect(screen.getByText("F:\\DramaProjects\\p\\exports\\contact-sheet-abc")).toBeTruthy();
    expect(screen.getByRole("link", { name: "下载 ZIP" }).getAttribute("download")).toBe("contact-sheet-abc.zip");
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
