import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AssetBiblePage } from "./AssetBiblePage";
import { createStoryAsset } from "../generated/api";
import { getAssetBible } from "../features/asset-bible-v2/api";

vi.mock("../generated/api", () => ({
  listProfiles: vi.fn().mockResolvedValue({ items: [] }),
  createStoryAsset: vi.fn().mockResolvedValue({ asset: { id: "new-1", kind: "CHARACTER", code: "CHAR_NEW", name: "新角色", description: "", canonical_media_version_id: null, status: "ACTIVE", revision: 1, created_at: "", updated_at: "", created_by: "", schema_version: "v2", extra: {} } }),
  archiveStoryAsset: vi.fn().mockResolvedValue({ asset: { id: "c1", status: "ARCHIVED" } }),
}));
vi.mock("../features/asset-bible-v2/api", () => ({
  getAssetBible: vi.fn(),
  getAssetReferenceMediaVersion: vi.fn(),
  createStoryAssetState: vi.fn(),
  createStoryAssetReference: vi.fn(),
}));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/projects/p1/assets"]}>
        <Routes>
          <Route path="/projects/:projectId/assets" element={<AssetBiblePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AssetBiblePage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows asset list and master detail with hero reference", async () => {
    vi.mocked(getAssetBible).mockResolvedValue({
      bible: {
        project_id: "p1",
        asset_count: 1,
        items: [
          {
            asset: { id: "c1", kind: "CHARACTER", code: "CHAR_HERO", name: "阿宁", description: "主角", status: "ACTIVE", revision: 2, canonical_media_version_id: "m1" },
            states: [],
            base_references: [{ id: "r1", project_id: "p1", story_asset_id: "c1", asset_state_id: null, media_version_id: "m1", reference_kind: "HERO", label: "", priority: 100, is_locked: 1, yaw_deg: null, pitch_deg: null, status: "ACTIVE", revision: 1 }],
            active_state_id: null,
            voice: null,
            usage: { episode_ids: [], episodes: [], shots: [], shot_count: 0 },
            readiness: { level: "BASIC", missing: ["FRONT", "LEFT", "RIGHT"] },
          },
        ],
      },
    });
    renderPage();
    expect((await screen.findAllByText("阿宁")).length).toBeGreaterThan(0);
    expect(screen.getByText("主角")).toBeTruthy();
    expect(screen.getByText(/缺参考/)).toBeTruthy();
    expect(screen.getByText(/主参考/, { selector: "figcaption" })).toBeTruthy();
    expect(screen.getByAltText("阿宁 资产缩略图").className).toContain("ui-media-thumb");
  });

  it("shows empty state when no assets in the selected kind", async () => {
    vi.mocked(getAssetBible).mockResolvedValue({ bible: { project_id: "p1", asset_count: 0, items: [] } });
    renderPage();
    expect(await screen.findByText("还没有角色资产")).toBeTruthy();
    expect(screen.getByText("请选择一项资产")).toBeTruthy();
  });

  it("shows first-loading and recoverable error states without a misleading empty bible", async () => {
    let reject!: (reason: Error) => void;
    vi.mocked(getAssetBible).mockImplementation(() => new Promise((_resolve, nextReject) => { reject = nextReject; }));
    renderPage();
    expect(screen.getByRole("status").getAttribute("aria-label")).toContain("正在读取资产");
    expect(screen.queryByText("当前类别还没有资产。")).toBeNull();
    reject(new Error("本机数据库暂不可用"));
    expect((await screen.findByRole("alert")).textContent).toContain("本机数据库暂不可用");
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });

  it("creates an asset and selects it", async () => {
    vi.mocked(getAssetBible).mockResolvedValue({ bible: { project_id: "p1", asset_count: 0, items: [] } });
    renderPage();
    fireEvent.click(await screen.findByText(/新建角色资产/));
    fireEvent.change(screen.getByLabelText("代码"), { target: { value: "CHAR_NEW" } });
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "新角色" } });
    fireEvent.click(screen.getByRole("button", { name: "创建资产" }));
    await waitFor(() => expect(createStoryAsset).toHaveBeenCalledWith("p1", expect.objectContaining({ code: "CHAR_NEW", name: "新角色" })));
  });
});
