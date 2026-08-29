import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listCapabilityOptions } from "../../generated/api";
import { capabilityOptionFixture, capabilityOptionsFixture } from "../model-config/capabilityOptionsTestFixtures";
import { preflightAssetExpression } from "./expressionClient";
import { GenerateExpressionPanel } from "./GenerateExpressionPanel";

vi.mock("../../generated/api", () => ({ listCapabilityOptions: vi.fn(), getProfileVersion: vi.fn() }));
vi.mock("./expressionClient", async () => {
  const actual = await vi.importActual<typeof import("./expressionClient")>("./expressionClient");
  return { ...actual, preflightAssetExpression: vi.fn(), submitAssetExpression: vi.fn(), getAssetExpressionHistory: vi.fn() };
});

describe("GenerateExpressionPanel", () => {
  beforeEach(() => {
    vi.mocked(listCapabilityOptions).mockResolvedValue(capabilityOptionsFixture("IMAGE_EXPRESSION", [
      capabilityOptionFixture("IMAGE_EXPRESSION", "expr-v1", "表情模型", "表情生成配置", 2),
    ]));
    vi.mocked(preflightAssetExpression).mockResolvedValue({ preflight: {
      asset_id: "asset-1", project_id: "project-1", capability: "IMAGE_EXPRESSION", status: "BLOCKED", ready: false,
      blockers: [{ code: "ASSET_EXPRESSION_CAPABILITY_UNAVAILABLE", message: "当前没有可执行的 IMAGE_EXPRESSION Published Profile", suggested_action: null, details: {} }],
      hero: { media_version_id: "hero-1" }, profile_resolution: {}, plan_hash: "a".repeat(64), would_create_jobs: 0, would_create_variants: 0,
    } });
  });

  it("offers only published IMAGE_EXPRESSION profiles and exposes honest preflight blockers", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<MemoryRouter><QueryClientProvider client={client}><GenerateExpressionPanel projectId="project-1" assetId="asset-1" assetStatus="ACTIVE" states={[]} baseReferences={[{ id: "ref-1", project_id: "project-1", story_asset_id: "asset-1", asset_state_id: null, media_version_id: "hero-1", reference_kind: "HERO", label: "", priority: 1, is_locked: true, yaw_deg: null, pitch_deg: null, status: "ACTIVE", revision: 1 }]} initialBatches={[]} onReferencesChanged={async () => {}} /></QueryClientProvider></MemoryRouter>);
    expect((await screen.findAllByRole("option", { name: /表情模型/ })).length).toBe(2);
    expect(screen.queryByRole("option", { name: /三视图模型/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /预检 \d+ 个表情槽/ }));
    await waitFor(() => expect(preflightAssetExpression).toHaveBeenCalled());
    expect(screen.getByText(/当前没有可执行的 IMAGE_EXPRESSION/)).toBeTruthy();
    expect((screen.getByRole("button", { name: /确认生成 \d+ 个独立槽/ }) as HTMLButtonElement).disabled).toBe(true);
  });
});
