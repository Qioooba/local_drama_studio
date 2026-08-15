import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ContinuityContext, ContinuityShot } from "../../generated/api";
import { ContinuityPanel } from "./ContinuityPanel";

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
  transitions: [{ id: "transition" }],
  read_only: true,
  runtime_contacted: false,
  network_contacted: false,
  mutated: false,
};

describe("ContinuityPanel", () => {
  it("renders adjacent revisions, six facets and only a small thumbnail request", () => {
    const { container } = render(<ContinuityPanel context={context} />);
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
    render(<ContinuityPanel context={{ ...context, shots: { ...context.shots, previous: null } }} />);
    expect(screen.getByText("无相邻镜头")).toBeTruthy();
    expect(screen.getByText("已到达当前分集边界")).toBeTruthy();
  });
});
