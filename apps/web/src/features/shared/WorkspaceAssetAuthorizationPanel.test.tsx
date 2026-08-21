import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkspaceAssetAuthorizationPanel } from "./WorkspaceAssetAuthorizationPanel";

vi.mock("../../generated/api", () => ({ authorizeWorkspaceAsset: vi.fn(), revokeWorkspaceAssetAuthorization: vi.fn() }));

describe("WorkspaceAssetAuthorizationPanel labels", () => {
  it("uses shot code as the primary label and keeps the version id in advanced details", () => {
    render(<WorkspaceAssetAuthorizationPanel projectId="project-1" items={[{
      media_version_id: "technical-media-version-id", media_asset_id: "asset-1", project_id: "project-1",
      episode_code: "EP03", shot_code: "S12", media_kind: "VIDEO", stage: "PROXY", decision: null, is_stale: 0,
    }]} />);
    expect(screen.getByText("S12")).toBeTruthy();
    const details = screen.getByText("高级：版本标识").closest("details");
    expect(details?.open).toBe(false);
    expect(details?.textContent).toContain("technical-media-version-id");
  });
});
