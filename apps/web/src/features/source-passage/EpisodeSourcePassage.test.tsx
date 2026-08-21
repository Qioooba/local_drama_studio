import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { listEpisodeSceneRanges, listScriptBreakdownDrafts } from "../../generated/api";
import { EpisodeSourcePassage } from "./EpisodeSourcePassage";

vi.mock("../../generated/api", () => ({ listEpisodeSceneRanges: vi.fn(), listScriptBreakdownDrafts: vi.fn() }));
vi.mock("./SourcePassagePanel", () => ({ SourcePassagePanel: (props: Record<string, unknown>) => <output data-testid="passage-props">{JSON.stringify(props)}</output> }));

afterEach(() => vi.clearAllMocks());

describe("EpisodeSourcePassage", () => {
  it("uses the applied source version and lets users jump between scene offsets", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({ items: [
      { source_document_version_id: "draft-source", application_status: "NOT_APPLIED" },
      { source_document_version_id: "applied-source", application_status: "APPLIED" },
    ] as never, automatic_apply: false, requires_human_action: true });
    vi.mocked(listEpisodeSceneRanges).mockResolvedValue({ items: [
      { id: "r1", ordinal: 1, scene_code: "SC-1", scene_title: "开场", source_start: 100, source_end: 300 },
      { id: "r2", ordinal: 2, scene_code: "SC-2", scene_title: "追逐", source_start: 9000, source_end: 9800 },
    ] as never });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><EpisodeSourcePassage projectId="p1" episodeId="e1" /></QueryClientProvider>);

    expect(await screen.findByText(/applied-source/)).toBeTruthy();
    expect(screen.getByTestId("passage-props").textContent).toContain('"initialStart":100');
    fireEvent.change(screen.getByLabelText("定位场景"), { target: { value: "r2" } });
    expect(screen.getByTestId("passage-props").textContent).toContain('"initialStart":9000');
  });

  it("does not attach scene offsets to an un-applied draft", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({ items: [
      { source_document_version_id: "unreviewed-source", application_status: "NOT_APPLIED" },
    ] as never, automatic_apply: false, requires_human_action: true });
    vi.mocked(listEpisodeSceneRanges).mockResolvedValue({ items: [] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><EpisodeSourcePassage projectId="p1" episodeId="e1" /></QueryClientProvider>);

    expect(await screen.findByText(/"sourceDocumentVersionId":null/)).toBeTruthy();
  });
});
