import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createProjectAssetGrant, listProjectAssetGrantCandidates, listProjectAssetGrants, revokeProjectAssetGrant } from "../../generated/api";
import { ProjectAssetGrantPanel } from "./ProjectAssetGrantPanel";

vi.mock("../../generated/api", () => ({ createProjectAssetGrant: vi.fn(), listProjectAssetGrantCandidates: vi.fn(), listProjectAssetGrants: vi.fn(), revokeProjectAssetGrant: vi.fn() }));

const candidate = { authorization_id: "auth-1", source_project_id: "source-1", source_project_code: "SOURCE", source_project_title: "源项目", media_version_id: "media-1", asset_kind: "IMAGE", path_rel: "references/hero.png", authorization_sha256: "a".repeat(64), authorization_byte_size: 42, authorization_status: "AUTHORIZED", license_status: "LOCAL_PROJECT_AUTHORIZED", authorization_revision: 3, version_no: 1, stage: "SOURCE", integrity_status: "VERIFIED", media_sha256: "a".repeat(64), media_byte_size: 42, already_granted: false, grantable: true, impact: [] };
const grant = { id: "grant-1", source_project_id: "source-1", target_project_id: "target-1", source_authorization_id: "auth-1", media_version_id: "media-1", source_revision: 3, source_sha256: "a".repeat(64), source_byte_size: 42, access_mode: "READ_ONLY" as const, status: "ACTIVE" as const, source_project_code: "SOURCE", impact: [], usable: true };

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><ProjectAssetGrantPanel projectId="target-1" /></QueryClientProvider>);
}

describe("ProjectAssetGrantPanel", () => {
  beforeEach(() => {
    vi.mocked(listProjectAssetGrantCandidates).mockReset().mockResolvedValue({ items: [candidate] });
    vi.mocked(listProjectAssetGrants).mockReset().mockResolvedValue({ items: [grant] });
    vi.mocked(createProjectAssetGrant).mockReset().mockResolvedValue({ grant });
    vi.mocked(revokeProjectAssetGrant).mockReset().mockResolvedValue({ grant: { ...grant, status: "REVOKED" } });
  });

  it("freezes the selected source fingerprint and creates a local grant", async () => {
    renderPanel();
    await screen.findByText("SOURCE · IMAGE · references/hero.png");
    fireEvent.change(screen.getByLabelText("候选源资产"), { target: { value: "auth-1" } });
    fireEvent.change(screen.getByLabelText("授权用途"), { target: { value: "DERIVED" } });
    expect(screen.getByText(/v3 · aaaaaaaa/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "创建项目 Grant" }));
    await waitFor(() => expect(createProjectAssetGrant).toHaveBeenCalledWith("target-1", { authorization_id: "auth-1", access_mode: "DERIVED" }));
  });

  it("requires an explicit withdrawal reason for an active grant", async () => {
    renderPanel();
    await screen.findByText("SOURCE");
    fireEvent.click(screen.getByRole("button", { name: "撤回 Grant" }));
    const reasonInput = await screen.findByLabelText("撤回 Grant 原因");
    const confirmButton = screen.getByRole("button", { name: "确认撤回" }) as HTMLButtonElement;
    expect(confirmButton.disabled).toBe(true);
    fireEvent.change(reasonInput, { target: { value: "不再需要" } });
    fireEvent.click(screen.getByRole("button", { name: "确认撤回" }));
    await waitFor(() => expect(revokeProjectAssetGrant).toHaveBeenCalledWith("grant-1", "不再需要"));
  });
});
