import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { exportJianyingTimeline, exportTimelineRevision, type TimelineExport } from "../../generated/api";
import { TimelineExportAction } from "./TimelineExportAction";

vi.mock("../../generated/api", () => ({ exportTimelineRevision: vi.fn(), exportJianyingTimeline: vi.fn() }));

function renderAction(timelineRevisionId: string | null = "timeline-1") {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><TimelineExportAction timelineRevisionId={timelineRevisionId} /></QueryClientProvider>);
}

const standardExport: TimelineExport = {
  schema_version: "localdrama.timeline-export.v1", status: "EXPORTED", rel_path: "05_timelines/EP001/exports/v1",
  manifest_rel_path: "05_timelines/EP001/exports/v1/manifest.json", export_hash: "abc", reused: false,
  files: [{ rel_path: "EP001-v1.otio", byte_size: 100, sha256: "a" }, { rel_path: "EP001-v1.edl", byte_size: 80, sha256: "b" }],
  database_mutated: false, runtime_contacted: false, network_contacted: false,
};

const jianyingExport: TimelineExport = {
  schema_version: "localdrama.timeline-export.v1", status: "EXPORTED", rel_path: "05_timelines/EP001/exports/v1-jianying",
  manifest_rel_path: "05_timelines/EP001/exports/v1-jianying/manifest.json", export_hash: "def", reused: false,
  files: [
    { rel_path: "EP001-v1.draft/draft_content.json", byte_size: 500, sha256: "c" },
    { rel_path: "EP001-v1.draft/media/video_01.mp4", byte_size: 1024, sha256: "d" },
  ],
  database_mutated: false, runtime_contacted: false, network_contacted: false,
};

describe("TimelineExportAction", () => {
  beforeEach(() => {
    vi.mocked(exportTimelineRevision).mockReset().mockResolvedValue({ export: standardExport });
    vi.mocked(exportJianyingTimeline).mockReset().mockResolvedValue({ export: jianyingExport });
  });

  it("exports the frozen revision and displays its local directory", async () => {
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "导出 OTIO / EDL" }));
    await waitFor(() => expect(exportTimelineRevision).toHaveBeenCalledWith("timeline-1"));
    expect((await screen.findByRole("status")).textContent).toContain("已导出 2 个文件");
  });

  it("exports a Jianying draft through the dedicated endpoint and shows the path", async () => {
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "导出剪映草稿" }));
    await waitFor(() => expect(exportJianyingTimeline).toHaveBeenCalledWith("timeline-1"));
    const results = await screen.findAllByRole("status");
    const jianyingResult = results.find((node) => node.textContent?.includes("v1-jianying"));
    expect(jianyingResult?.textContent).toContain("已导出 2 个文件");
    expect(jianyingResult?.textContent).toContain("05_timelines/EP001/exports/v1-jianying");
  });

  it("is disabled without a persisted revision", () => {
    renderAction(null);
    expect(screen.getByRole("button", { name: "导出 OTIO / EDL" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "导出剪映草稿" }).hasAttribute("disabled")).toBe(true);
  });
});
