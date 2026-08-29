import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listCapabilityOptions } from "../../generated/api";
import { capabilityOptionFixture, capabilityOptionsFixture } from "../model-config/capabilityOptionsTestFixtures";
import { preflightAssetDetail } from "./detailClient";
import { GenerateDetailPanel } from "./GenerateDetailPanel";

vi.mock("../../generated/api", () => ({ listCapabilityOptions: vi.fn(), getProfileVersion: vi.fn() }));
vi.mock("./detailClient", async () => ({ ...(await vi.importActual<typeof import("./detailClient")>("./detailClient")), preflightAssetDetail: vi.fn(), submitAssetDetail: vi.fn(), getAssetDetailHistory: vi.fn() }));

describe("GenerateDetailPanel", () => {
  beforeEach(() => {
    vi.mocked(listCapabilityOptions).mockResolvedValue(capabilityOptionsFixture("IMAGE_EDIT", [
      capabilityOptionFixture("IMAGE_EDIT", "edit-v1", "局部重绘", "局部重绘配置", 3),
    ]));
    vi.mocked(preflightAssetDetail).mockResolvedValue({ preflight: { asset_id: "asset-1", project_id: "project-1", capability: "IMAGE_EDIT", status: "BLOCKED", ready: false, blockers: [{ code: "ASSET_DETAIL_CAPABILITY_UNAVAILABLE", message: "当前没有可执行的 IMAGE_EDIT Published Profile", suggested_action: null, details: {} }], hero: { media_version_id: "hero-1" }, profile_resolution: {}, plan_hash: "b".repeat(64), would_create_jobs: 0, would_create_variants: 0 } });
  });

  it("filters semantic profiles to IMAGE_EDIT and keeps submit disabled on a real blocker", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<MemoryRouter><QueryClientProvider client={client}><GenerateDetailPanel projectId="project-1" assetId="asset-1" assetStatus="ACTIVE" states={[]} baseReferences={[{ id: "ref-1", project_id: "project-1", story_asset_id: "asset-1", asset_state_id: null, media_version_id: "hero-1", reference_kind: "HERO", label: "", priority: 1, is_locked: true, yaw_deg: null, pitch_deg: null, status: "ACTIVE", revision: 1 }]} initialBatches={[]} onReferencesChanged={async () => {}} /></QueryClientProvider></MemoryRouter>);
    expect((await screen.findAllByRole("option", { name: /局部重绘/ })).length).toBe(2);
    expect(screen.queryByRole("option", { name: /表情模型/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /预检 \d+ 个近景细节/ }));
    await waitFor(() => expect(preflightAssetDetail).toHaveBeenCalled());
    expect(await screen.findByText(/当前没有可执行的 IMAGE_EDIT/)).toBeTruthy();
    expect((screen.getByRole("button", { name: /确认生成 \d+ 个独立槽/ }) as HTMLButtonElement).disabled).toBe(true);
  });
});
