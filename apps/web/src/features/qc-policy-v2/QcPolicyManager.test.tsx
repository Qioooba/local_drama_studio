import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { listQcEpisodes, listQcPolicies, listQcSeasons, listQcShots, putQcPolicy, resolveQcPolicy } from "./api";
import { QcPolicyManager } from "./QcPolicyManager";

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return { ...actual, listQcEpisodes: vi.fn(), listQcPolicies: vi.fn(), listQcSeasons: vi.fn(), listQcShots: vi.fn(), putQcPolicy: vi.fn(), resolveQcPolicy: vi.fn() };
});

describe("QcPolicyManager keyboard controls", () => {
  it("adjusts thresholds and reroll limits with arrow keys", async () => {
    const policy = {
      policy_set_id: "set-1", project_id: "project-1", owner_type: "PROJECT" as const, owner_id: "project-1", stage: "VIDEO" as const,
      status: "ACTIVE", revision: 1, policy_version_id: "version-1", version_no: 1,
      policy: { checks: ["VISUAL" as const], thresholds: { VISUAL: 0.8 }, attention_selection: "REQUIRE_CONFIRMATION" as const },
      max_auto_rerolls: 0, auto_reroll_categories: [], is_frozen: true, reason: "baseline", created_at: "now", created_by: "test",
    };
    vi.mocked(listQcSeasons).mockResolvedValue([{ id: "season-1", code: "S1" }]);
    vi.mocked(listQcEpisodes).mockResolvedValue([]);
    vi.mocked(listQcShots).mockResolvedValue([]);
    vi.mocked(listQcPolicies).mockResolvedValue([policy]);
    vi.mocked(resolveQcPolicy).mockResolvedValue({ ...policy, source: "PROJECT" });
    vi.mocked(putQcPolicy).mockResolvedValue(policy);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><QcPolicyManager projectId="project-1" /></QueryClientProvider>);

    const threshold = (await screen.findAllByRole("spinbutton"))[0] as HTMLInputElement;
    fireEvent.keyDown(threshold, { key: "ArrowUp" });
    expect(threshold.value).toBe("0.85");

    const slider = screen.getByRole("slider") as HTMLInputElement;
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    expect(slider.value).toBe("1");
    const autoGroup = screen.getByRole("group", { name: "允许自动重抽的失败类别" });
    expect(autoGroup.hasAttribute("disabled")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "刷新版本" }));
    await screen.findByText("已从持久化版本重新载入，未保存修改已放弃。");
    expect(threshold.value).toBe("0.8");
    expect(slider.value).toBe("0");
    expect(autoGroup.hasAttribute("disabled")).toBe(true);
  });
});
