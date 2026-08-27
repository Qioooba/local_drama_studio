import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ContinuityContext, ContinuityShot } from "../../generated/api";
import { listShotStoryAssets } from "../../generated/api";
import { ContinuityPanel } from "./ContinuityPanel";

vi.mock("../../generated/api", () => ({ listShotStoryAssets: vi.fn() }));

function shot(position: ContinuityShot["position"], code: string): ContinuityShot {
  return {
    position,
    id: `${position}-id`,
    code,
    order_key: "1",
    status: "READY",
    target_duration_ms: 4_000,
    revision: { id: `${position}-revision`, revision_no: 2, is_frozen: true },
    facets: { appearance: "短发", costume: "蓝色外套", props: ["信件"], lighting: "暖光", spatial_direction: "画面左侧", continuity: "外套干燥" },
    missing_facets: [],
    references: position === "current" ? [{ media_asset_id: "asset", media_version_id: "version", purpose: "CONTINUITY_REFERENCE", media_kind: "IMAGE", selection_state: "APPROVED", version_no: 3, stage: "FORMAL", integrity_status: "VERIFIED" }] : [],
  };
}

const context: ContinuityContext = {
  episode_id: "episode",
  selected_shot_id: "current-id",
  shots: { previous: shot("previous", "S001"), current: shot("current", "S002"), next: shot("next", "S003") },
  transitions: [{
    id: "transition", from_shot_id: "previous-id", to_shot_id: "current-id",
    constraint_type: "FRAME_BRIDGE", enforcement: "ADVISORY",
    compatibility_status: "COMPATIBLE", is_stale: false, stale_reason: null,
    from_anchor_id: null, to_anchor_id: null, boundary_revision: 1,
  }],
  read_only: true,
  runtime_contacted: false,
  network_contacted: false,
  mutated: false,
  request_shape: "shot_continuity_context_v2",
};

function renderPanel(value: ContinuityContext | undefined) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><ContinuityPanel context={value} /></QueryClientProvider>);
}

describe("ContinuityPanel", () => {
  beforeEach(() => {
    vi.mocked(listShotStoryAssets).mockReset().mockResolvedValue({ items: [] });
  });

  it("renders adjacent revisions, six facets and only a small thumbnail request", () => {
    const { container } = renderPanel(context);
    expect(screen.getByText("S001")).toBeTruthy();
    expect(screen.getByText("S002")).toBeTruthy();
    expect(screen.getByText("S003")).toBeTruthy();
    expect(screen.getAllByText("人物外观")).toHaveLength(3);
    expect(screen.getByText("边界约束 1")).toBeTruthy();
    const image = container.querySelector("img") as HTMLImageElement;
    expect(image.getAttribute("src")).toContain("/thumbnail?size=small&frame=poster");
    expect(image.getAttribute("src")).not.toContain("/content");
  });

  it("shows an explicit episode boundary instead of inventing a neighbor", () => {
    renderPanel({ ...context, shots: { ...context.shots, previous: null } });
    expect(screen.getByText("无相邻镜头")).toBeTruthy();
    expect(screen.getByText("已到达当前分集边界")).toBeTruthy();
  });

  it("lists bound story assets under the continuity references", async () => {
    vi.mocked(listShotStoryAssets).mockResolvedValue({ items: [
      { binding_id: "binding-1", shot_id: "current-id", asset_id: "asset-1", name: "母亲", code: "CHAR_MOTHER", kind: "CHARACTER", status: "ACTIVE", canonical_media_version_id: null, role_in_shot: "main", created_at: "now", created_by: "local-user" },
      { binding_id: "binding-2", shot_id: "current-id", asset_id: "asset-2", name: "厨房", code: "SCENE_KITCHEN", kind: "SCENE", status: "ARCHIVED", canonical_media_version_id: null, role_in_shot: "location", created_at: "now", created_by: "local-user" },
    ] });
    renderPanel(context);
    expect(await screen.findByText("母亲")).toBeTruthy();
    expect(screen.getByText("厨房")).toBeTruthy();
    expect(screen.getByText(/角色 · main/)).toBeTruthy();
    expect(screen.getByText(/场景 · location/)).toBeTruthy();
    expect(screen.getByText("已归档")).toBeTruthy();
    expect(listShotStoryAssets).toHaveBeenCalledWith("current-id");
  });

  it("hides the bound asset section when the shot has none", async () => {
    renderPanel(context);
    await screen.findByText("S002");
    expect(screen.queryByLabelText("当前镜头绑定资产")).toBeNull();
  });
});
