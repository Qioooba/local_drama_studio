import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FreshnessPanel } from "./FreshnessPanel";
import { useProjectEventInvalidation } from "../events/useProjectEventInvalidation";
import { queryKeys } from "../../query/queryKeys";

vi.mock("../events/useProjectEventInvalidation", () => ({ useProjectEventInvalidation: vi.fn() }));

const response = {
  scope: { type: "EPISODE", id: "e1", project_id: "p1", episode_id: "e1", shot_id: null },
  summary: { returned: 1, stale: 1, current: 0, truncated: true },
  items: [{
    id: "v1", fact_type: "VARIANT", status: "STALE", project_id: "p1", episode_id: "e1", shot_id: "s1",
    source: { entity_type: "ASSET_REFERENCE", entity_id: "ref-old", revision: 2 },
    current: { entity_type: "ASSET_REFERENCE", entity_id: "ref-new", revision: 1 },
    reasons: [{ code: "ASSET_REFERENCE_CHANGED", message: "Variant 使用旧参考", source_revision: 2, current_revision: 1 }],
    remediation_links: [{ rel: "regenerate", href: "/director/s1?action=regenerate", method: "GET", label: "仅重生成当前镜" }],
  }],
  audit: { read_only: true, writes_performed: 0, query_count: 5, query_limit: 50 },
  local_only: true, network_contacted: false,
};

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><FreshnessPanel projectId="p1" scopeType="EPISODE" scopeId="e1" /></MemoryRouter></QueryClientProvider>);
}

describe("FreshnessPanel", () => {
  afterEach(() => { vi.restoreAllMocks(); vi.mocked(useProjectEventInvalidation).mockClear(); });

  it("shows reasons, source/current revisions, bounded limit, and safe deep links", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(response), { status: 200 }));
    setup();

    expect(await screen.findByText("资产参考已变化")).toBeTruthy();
    expect(screen.getByText("ASSET_REFERENCE · ref-old · rev 2")).toBeTruthy();
    expect(screen.getByText("ASSET_REFERENCE · ref-new · rev 1")).toBeTruthy();
    expect(screen.getByRole("link", { name: "仅重生成当前镜" }).getAttribute("href")).toBe("/projects/p1/episodes/e1/direct/s1");
    expect(screen.getByText(/结果已达到 limit/)).toBeTruthy();
    expect(screen.getByText(/未执行写入/)).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/episodes/e1/production-freshness?limit=50");

    fireEvent.change(screen.getByLabelText("Freshness 报告上限"), { target: { value: "100" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/v1/episodes/e1/production-freshness?limit=100"));
  });

  it("subscribes only the scoped freshness key to relevant production events", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ ...response, summary: { returned: 0, stale: 0, current: 0, truncated: false }, items: [] }), { status: 200 }));
    setup();
    expect(await screen.findByText(/当前范围没有可评估/)).toBeTruthy();
    expect(useProjectEventInvalidation).toHaveBeenCalled();
    const [projectId, events, keys] = vi.mocked(useProjectEventInvalidation).mock.calls.at(-1)!;
    expect(projectId).toBe("p1");
    expect(events).toContain("CONTINUITY_STALE_PROPAGATED");
    expect(events).toContain("SHOT_REVISION_CREATED");
    expect(events).not.toContain("JOB_HEARTBEAT");
    expect(keys).toEqual([queryKeys.freshness.scope("EPISODE", "e1")]);
  });

  it("groups historical revisions into one remediation while retaining raw evidence counts", async () => {
    const duplicate = { ...response.items[0], id: "v2" };
    const distinctRevision = {
      ...response.items[0],
      id: "v3",
      source: { ...response.items[0].source, revision: 3 },
      reasons: [{ ...response.items[0].reasons[0], source_revision: 3 }],
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      ...response,
      summary: { returned: 3, stale: 3, current: 0, truncated: false },
      items: [response.items[0], duplicate, distinctRevision],
    }), { status: 200 }));
    setup();

    expect(await screen.findByLabelText("包含 3 条原始事实")).toBeTruthy();
    expect(screen.getByText("3 条事实")).toBeTruthy();
    expect(screen.getByText(/3 条历史事实聚合为一次处置/)).toBeTruthy();
    expect(screen.getByText(/2 组生成时来源/)).toBeTruthy();
    expect(screen.getByText(/\/ 3 条事实/)).toBeTruthy();
    expect(screen.getAllByText("资产参考已变化")).toHaveLength(1);
    expect(screen.getAllByRole("link", { name: "仅重生成当前镜" })).toHaveLength(1);
  });
});
