import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { listQcEpisodes, listQcPolicies, listQcSeasons, listQcShots, putQcPolicy, resolveQcPolicy } from "./api";
import { QcPolicyManager } from "./QcPolicyManager";
import { autoSelectCascade, chooseCascadeLevel, initialCascadeSelection } from "./cascadeScope";

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return { ...actual, listQcEpisodes: vi.fn(), listQcPolicies: vi.fn(), listQcSeasons: vi.fn(), listQcShots: vi.fn(), putQcPolicy: vi.fn(), resolveQcPolicy: vi.fn() };
});

const policy = {
  policy_set_id: "set-1", project_id: "project-1", owner_type: "PROJECT" as const, owner_id: "project-1", stage: "VIDEO" as const,
  status: "ACTIVE", revision: 1, policy_version_id: "version-1", version_no: 1,
  policy: { checks: ["VISUAL" as const], thresholds: { VISUAL: 0.8 }, attention_selection: "REQUIRE_CONFIRMATION" as const },
  max_auto_rerolls: 0, auto_reroll_categories: [], is_frozen: true, reason: "baseline", created_at: "now", created_by: "test",
};

function renderManager() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><QcPolicyManager projectId="project-1" /></QueryClientProvider>);
}

function stubScope() {
  vi.mocked(listQcSeasons).mockResolvedValue([{ id: "season-1", code: "S1" }, { id: "season-2", code: "S2" }]);
  vi.mocked(listQcEpisodes).mockResolvedValue([{ id: "episode-1", code: "E1" }, { id: "episode-2", code: "E2" }]);
  vi.mocked(listQcShots).mockResolvedValue([{ id: "shot-1", code: "SH1" }, { id: "shot-2", code: "SH2" }]);
  vi.mocked(listQcPolicies).mockResolvedValue([policy]);
  vi.mocked(resolveQcPolicy).mockResolvedValue({ ...policy, source: "PROJECT" });
  vi.mocked(putQcPolicy).mockResolvedValue(policy);
}

describe("QC cascade scope model", () => {
  it("default-selects each level exactly once", () => {
    const initial = initialCascadeSelection();
    const first = autoSelectCascade(initial, { seasons: [{ id: "s1" }], episodes: [{ id: "e1" }], shots: [{ id: "sh1" }] });
    expect([first.season.selectedId, first.episode.selectedId, first.shot.selectedId]).toEqual(["s1", "e1", "sh1"]);
  });

  it("never refills a level the user explicitly cleared", () => {
    const decidedEmpty = { season: { selectedId: "s1", decided: true }, episode: { selectedId: "", decided: true }, shot: { selectedId: "", decided: true } };
    const next = autoSelectCascade(decidedEmpty, { seasons: [{ id: "s1" }], episodes: [{ id: "e1" }], shots: [{ id: "sh1" }] });
    expect(next).toBe(decidedEmpty);
  });

  it("resets only the children that belong to the changed parent", () => {
    const current = { season: { selectedId: "s1", decided: true }, episode: { selectedId: "e1", decided: true }, shot: { selectedId: "sh1", decided: true } };
    const changedSeason = chooseCascadeLevel(current, "season", "s2");
    expect(changedSeason.episode).toEqual({ selectedId: "", decided: false });
    expect(changedSeason.shot).toEqual({ selectedId: "", decided: false });
    const changedEpisode = chooseCascadeLevel(current, "episode", "e2");
    expect(changedEpisode.season.selectedId).toBe("s1");
    expect(changedEpisode.shot).toEqual({ selectedId: "", decided: false });
  });

  it("keeps a cleared season from being replaced by the first season again", () => {
    const cleared = chooseCascadeLevel({ ...initialCascadeSelection(), season: { selectedId: "s1", decided: true } }, "season", "");
    const next = autoSelectCascade(cleared, { seasons: [{ id: "s1" }], episodes: [{ id: "e1" }], shots: [{ id: "sh1" }] });
    expect(next.season.selectedId).toBe("");
    expect(next.episode.selectedId).toBe("");
  });
});

describe("QcPolicyManager upper-scope selection", () => {
  it("keeps 'project level only' selected and drops episode/shot from the resolve request", async () => {
    stubScope();
    renderManager();
    await waitFor(() => {
      const lastCall = vi.mocked(resolveQcPolicy).mock.calls.at(-1);
      expect(lastCall?.[2]).toBe("episode-1");
      expect(lastCall?.[3]).toBe("shot-1");
    });
    // Re-query after the loading branch has been replaced: the first render's nodes are detached.
    expect((screen.getByLabelText("查看分集") as HTMLSelectElement).value).toBe("episode-1");

    fireEvent.change(screen.getByLabelText("查看分集"), { target: { value: "" } });
    expect((screen.getByLabelText("查看分集") as HTMLSelectElement).value).toBe("");
    expect((screen.getByLabelText("查看镜头") as HTMLSelectElement).value).toBe("");
    expect((screen.getByLabelText("查看镜头") as HTMLSelectElement).disabled).toBe(true);

    await waitFor(() => {
      const lastCall = vi.mocked(resolveQcPolicy).mock.calls.at(-1);
      expect(lastCall?.[2]).toBeUndefined();
      expect(lastCall?.[3]).toBeUndefined();
    });
  });

  it("keeps 'episode level only' selected without re-selecting the first shot", async () => {
    stubScope();
    renderManager();
    await waitFor(() => expect(vi.mocked(resolveQcPolicy).mock.calls.at(-1)?.[3]).toBe("shot-1"));
    expect((screen.getByLabelText("查看镜头") as HTMLSelectElement).value).toBe("shot-1");

    fireEvent.change(screen.getByLabelText("查看镜头"), { target: { value: "" } });
    expect((screen.getByLabelText("查看镜头") as HTMLSelectElement).value).toBe("");

    await waitFor(() => {
      const lastCall = vi.mocked(resolveQcPolicy).mock.calls.at(-1);
      expect(lastCall?.[2]).toBe("episode-1");
      expect(lastCall?.[3]).toBeUndefined();
    });
  });

  it("sends the episode again after the user selects one explicitly", async () => {
    stubScope();
    renderManager();
    await waitFor(() => expect(vi.mocked(resolveQcPolicy).mock.calls.at(-1)?.[2]).toBe("episode-1"));
    fireEvent.change(screen.getByLabelText("查看分集"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("查看分集"), { target: { value: "episode-2" } });
    await waitFor(() => {
      const lastCall = vi.mocked(resolveQcPolicy).mock.calls.at(-1);
      expect(lastCall?.[2]).toBe("episode-2");
    });
  });

  it("switches the write target down when its scope level is cleared", async () => {
    stubScope();
    renderManager();
    await waitFor(() => expect(vi.mocked(resolveQcPolicy).mock.calls.at(-1)?.[3]).toBe("shot-1"));
    fireEvent.click(screen.getByRole("button", { name: "镜头覆盖" }));
    expect(screen.getByRole("button", { name: "镜头覆盖" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.change(screen.getByLabelText("查看分集"), { target: { value: "" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "项目默认" }).getAttribute("aria-pressed")).toBe("true"));
  });
});
