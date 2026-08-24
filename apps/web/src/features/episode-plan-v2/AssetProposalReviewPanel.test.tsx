import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { decideAssetProposal, listAssetProposals, type AssetProposal } from "./assetProposalsApi";
import { AssetProposalReviewPanel } from "./AssetProposalReviewPanel";
import { generateAssetCode } from "../shared/autoCode";

vi.mock("./assetProposalsApi", () => ({
  listAssetProposals: vi.fn(),
  decideAssetProposal: vi.fn(),
}));

const proposals: AssetProposal[] = [
  { id: "p-1", name: "母亲", kind: "CHARACTER", status: "PENDING", revision: 1, evidence: { scene_count: 2 }, suggested_asset_id: "asset-1", suggested_asset_code: "CHAR_MOTHER", suggested_asset_name: "母亲", resolved_asset_id: null },
  { id: "p-2", name: "孩子", kind: "CHARACTER", status: "PENDING", revision: 1, evidence: { scene_count: 1 }, suggested_asset_id: null, suggested_asset_code: null, suggested_asset_name: null, resolved_asset_id: null },
];

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><AssetProposalReviewPanel projectId="project-1" /></QueryClientProvider>);
}

describe("AssetProposalReviewPanel batch decisions", () => {
  beforeEach(() => {
    vi.mocked(listAssetProposals).mockReset().mockResolvedValue(proposals);
    vi.mocked(decideAssetProposal).mockReset().mockResolvedValue({} as never);
  });

  it("previews merge/create counts and processes every explicitly selected proposal", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("checkbox", { name: "选择全部 2 条建议" }));
    expect(screen.getByText("将合并 1 个精确同名角色，新建 1 个独立角色。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认处理所选 2 项" }));

    await waitFor(() => expect(decideAssetProposal).toHaveBeenCalledTimes(2));
    expect(decideAssetProposal).toHaveBeenNthCalledWith(1, proposals[0], "MERGE_EXISTING", expect.objectContaining({ targetAssetId: "asset-1" }));
    expect(decideAssetProposal).toHaveBeenNthCalledWith(2, proposals[1], "CREATE_NEW", expect.objectContaining({ newAssetCode: expect.any(String) }));
    expect(await screen.findByText("批量结果：成功 2 · 失败 0")).toBeInTheDocument();
  });

  it("reports partial failure by character name instead of claiming total success", async () => {
    vi.mocked(decideAssetProposal).mockResolvedValueOnce({} as never).mockRejectedValueOnce(new Error("code 冲突"));
    renderPanel();
    fireEvent.click(await screen.findByRole("checkbox", { name: "选择全部 2 条建议" }));
    fireEvent.click(screen.getByRole("button", { name: "确认处理所选 2 项" }));

    expect(await screen.findByText("批量结果：成功 1 · 失败 1")).toBeInTheDocument();
    expect(screen.getByText(/孩子：Error: code 冲突/)).toBeInTheDocument();
  });

  it("automatically derives the asset code instead of asking the creator for a machine identifier", async () => {
    renderPanel();
    expect(await screen.findByText(generateAssetCode("CHARACTER", "孩子"))).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /独立资产编号/ })).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: "保留为独立角色" })[1]);
    await waitFor(() => expect(decideAssetProposal).toHaveBeenCalledWith(proposals[1], "CREATE_NEW", expect.objectContaining({ newAssetCode: expect.stringMatching(/^CHAR_/) })));
  });
});
