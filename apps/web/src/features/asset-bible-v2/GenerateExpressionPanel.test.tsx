import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listProfiles } from "../../generated/api";
import { preflightAssetExpression } from "./expressionClient";
import { GenerateExpressionPanel } from "./GenerateExpressionPanel";

vi.mock("../../generated/api", () => ({ listProfiles: vi.fn() }));
vi.mock("./expressionClient", async () => {
  const actual = await vi.importActual<typeof import("./expressionClient")>("./expressionClient");
  return { ...actual, preflightAssetExpression: vi.fn(), submitAssetExpression: vi.fn(), getAssetExpressionHistory: vi.fn() };
});

describe("GenerateExpressionPanel", () => {
  beforeEach(() => {
    vi.mocked(listProfiles).mockResolvedValue({ items: [
      { id: "p1", version_id: "expr-v1", title: "表情模型", capability: "IMAGE_EXPRESSION", status: "PUBLISHED", version_no: 2 },
      { id: "p2", version_id: "wrong-v1", title: "三视图模型", capability: "IMAGE_MULTI_VIEW", status: "PUBLISHED", version_no: 1 },
    ] } as never);
    vi.mocked(preflightAssetExpression).mockResolvedValue({ preflight: {
      asset_id: "asset-1", project_id: "project-1", capability: "IMAGE_EXPRESSION", status: "BLOCKED", ready: false,
      blockers: [{ code: "ASSET_MULTI_VIEW_CAPABILITY_UNAVAILABLE", message: "当前没有可执行的 IMAGE_EXPRESSION Published Profile", suggested_action: null, details: {} }],
      hero: { media_version_id: "hero-1" }, profile_resolution: {}, plan_hash: "a".repeat(64), would_create_jobs: 0, would_create_variants: 0,
    } });
  });

  it("offers only published IMAGE_EXPRESSION profiles and exposes honest preflight blockers", async () => {
    render(<GenerateExpressionPanel projectId="project-1" assetId="asset-1" assetStatus="ACTIVE" states={[]} baseReferences={[{ id: "ref-1", project_id: "project-1", story_asset_id: "asset-1", asset_state_id: null, media_version_id: "hero-1", reference_kind: "HERO", label: "", priority: 1, is_locked: true, yaw_deg: null, pitch_deg: null, status: "ACTIVE", revision: 1 }]} initialBatches={[]} onReferencesChanged={async () => {}} />);
    expect(await screen.findByRole("option", { name: /表情模型/ })).toBeTruthy();
    expect(screen.queryByRole("option", { name: /三视图模型/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "运行只读预检" }));
    await waitFor(() => expect(preflightAssetExpression).toHaveBeenCalled());
    expect(screen.getByText(/当前没有可执行的 IMAGE_EXPRESSION/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "确认生成九个独立槽" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
