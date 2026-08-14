import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { exportTimelineRevision } from "../../generated/api";
import { TimelineExportAction } from "./TimelineExportAction";

vi.mock("../../generated/api", () => ({ exportTimelineRevision: vi.fn() }));

function renderAction(timelineRevisionId: string | null = "timeline-1") {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><TimelineExportAction timelineRevisionId={timelineRevisionId} /></QueryClientProvider>);
}

describe("TimelineExportAction", () => {
  beforeEach(() => {
    vi.mocked(exportTimelineRevision).mockReset().mockResolvedValue({ export: {
      schema_version: "localdrama.timeline-export.v1", status: "EXPORTED", rel_path: "05_timelines/EP001/exports/v1",
      manifest_rel_path: "05_timelines/EP001/exports/v1/manifest.json", export_hash: "abc", reused: false,
      files: [{ rel_path: "EP001-v1.otio", byte_size: 100, sha256: "a" }, { rel_path: "EP001-v1.edl", byte_size: 80, sha256: "b" }],
      database_mutated: false, runtime_contacted: false, network_contacted: false,
    } });
  });

  it("exports the frozen revision and displays its local directory", async () => {
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "导出 OTIO / EDL" }));
    await waitFor(() => expect(exportTimelineRevision).toHaveBeenCalledWith("timeline-1"));
    expect((await screen.findByRole("status")).textContent).toContain("已导出 2 个文件");
  });

  it("is disabled without a persisted revision", () => {
    renderAction(null);
    expect(screen.getByRole("button", { name: "导出 OTIO / EDL" }).hasAttribute("disabled")).toBe(true);
  });
});
