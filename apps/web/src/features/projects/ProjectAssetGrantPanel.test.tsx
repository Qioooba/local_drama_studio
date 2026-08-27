import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listProjectAssetGrantCandidates, listProjectAssetGrants, revokeProjectAssetGrant } from "../../generated/api";
import { ProjectAssetGrantPanel } from "./ProjectAssetGrantPanel";

vi.mock("../../generated/api", () => ({
  createProjectAssetGrant: vi.fn(),
  listProjectAssetGrantCandidates: vi.fn(),
  listProjectAssetGrants: vi.fn(),
  revokeProjectAssetGrant: vi.fn(),
}));

describe("ProjectAssetGrantPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listProjectAssetGrantCandidates).mockResolvedValue({ items: [] } as never);
    vi.mocked(listProjectAssetGrants).mockResolvedValue({ items: [{ id: "grant-1", source_project_id: "source-1", source_project_code: "SOURCE", access_mode: "READ_ONLY", status: "ACTIVE", media_version_id: "media-version-1", impact: [], usable: true }] } as never);
    vi.mocked(revokeProjectAssetGrant).mockResolvedValue({ grant: { id: "grant-1", status: "REVOKED" } } as never);
  });

  it("closes the revoke dialog and gives durable feedback after success", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><ProjectAssetGrantPanel projectId="project-1" /></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "撤回 Grant" }));
    fireEvent.change(screen.getByLabelText("撤回 Grant 原因"), { target: { value: "UAT 撤回" } });
    fireEvent.click(screen.getByRole("button", { name: "确认撤回" }));
    await waitFor(() => expect(revokeProjectAssetGrant).toHaveBeenCalledWith("grant-1", "UAT 撤回"));
    expect(screen.queryByRole("dialog", { name: "填写撤回 Grant 原因" })).toBeNull();
    expect(screen.getByText("项目 Grant 已撤回；历史记录保留。")).toBeTruthy();
  });
});
