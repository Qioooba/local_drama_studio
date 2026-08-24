import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ShotGroupPlanner } from "./ShotGroupPlanner";
import { assignShotScene, getShotGroupWorkspace } from "./shotGroupsApi";

vi.mock("./shotGroupsApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./shotGroupsApi")>();
  return {
    ...actual,
    getShotGroupWorkspace: vi.fn(),
    assignShotScene: vi.fn(),
  };
});

function renderPlanner() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><ShotGroupPlanner episodeId="episode-1" /></QueryClientProvider>);
}

describe("ShotGroupPlanner", () => {
  beforeEach(() => {
    vi.mocked(getShotGroupWorkspace).mockReset();
    vi.mocked(assignShotScene).mockReset();
    vi.mocked(getShotGroupWorkspace).mockResolvedValue({
      episode: { id: "episode-1", code: "EPISODE_001", title: "第 1 集", project_id: "project-1" },
      scenes: [{ id: "scene-1", code: "SC01", title: "失踪的灯塔", revision: 1 }],
      shots: [{ id: "shot-1", code: "EPISODE_001-01-01", shot_type: "STANDARD", target_duration_ms: 3000, status: "DRAFT", order_key: "1", scene_id: null, group_id: null, revision: 1 }],
      groups: [],
    });
    vi.mocked(assignShotScene).mockResolvedValue({});
  });

  it("captures the selected scene before the controlled select resets", async () => {
    renderPlanner();
    const select = await screen.findByRole("combobox", { name: "EPISODE_001-01-01 场景" });
    fireEvent.change(select, { target: { value: "scene-1" } });
    await waitFor(() => expect(assignShotScene).toHaveBeenCalledTimes(1));
    expect(assignShotScene).toHaveBeenCalledWith(expect.objectContaining({ id: "shot-1" }), "scene-1");
  });
});
