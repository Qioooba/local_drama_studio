import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useEpisodeProductionQueries } from "./useEpisodeProductionQueries";

const mocks = vi.hoisted(() => ({
  overview: vi.fn(),
  shots: vi.fn(),
  replan: vi.fn(),
  onEvent: null as null | ((event: Record<string, unknown>) => void),
}));

vi.mock("../../generated/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../generated/api")>(),
  getEpisodeProductionOverviewV2: mocks.overview,
  listEpisodeProductionShotsV2: mocks.shots,
  getEpisodeProductionReplanV2: mocks.replan,
}));
vi.mock("../events/eventClient", () => ({
  subscribeStudioEvents: ({ onEvent }: { onEvent: (event: Record<string, unknown>) => void }) => {
    mocks.onEvent = onEvent;
    return () => { mocks.onEvent = null; };
  },
}));

function Probe() {
  const queries = useEpisodeProductionQueries("project-1", "episode-1");
  const attention = queries.attentionShots.data?.pages[0]?.total ?? -1;
  return <div>{queries.overview.data ? `ready:${attention}` : "loading"}</div>;
}

describe("useEpisodeProductionQueries event integration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.onEvent = null;
    mocks.overview.mockResolvedValue({ overview: { active_job_count: 0, replan_required: false } });
    mocks.shots.mockResolvedValue({ items: [], cursor: 0, limit: 100, total: 0, next_cursor: null });
  });

  it("refreshes overview plus all and attention shot siblings after JOB_FINISHED", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><Probe /></QueryClientProvider>);
    expect(await screen.findByText("ready:0")).toBeTruthy();
    await waitFor(() => expect(mocks.shots).toHaveBeenCalledTimes(2));

    act(() => mocks.onEvent?.({ event_type: "JOB_FINISHED" }));

    await waitFor(() => expect(mocks.overview).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mocks.shots).toHaveBeenCalledTimes(4));
    expect(mocks.shots).toHaveBeenCalledWith("episode-1", { cursor: 0, limit: 100 });
    expect(mocks.shots).toHaveBeenCalledWith("episode-1", {
      cursor: 0, limit: 100, states: ["BLOCKED", "FAILED", "NEEDS_REVIEW", "STALE"],
    });
  });
});
