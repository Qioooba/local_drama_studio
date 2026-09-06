import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AssetBiblePage } from "./AssetBiblePage";
import { createStoryAsset, updateStoryAsset } from "../generated/api";
import { createStoryAssetReference, getAssetBible } from "../features/asset-bible-v2/api";

vi.mock("../generated/api", () => ({
  createStoryAsset: vi.fn().mockResolvedValue({ asset: { id: "new-1" } }),
  updateStoryAsset: vi.fn().mockResolvedValue({ asset: { id: "c1" } }),
}));
vi.mock("../features/asset-bible-v2/api", () => ({
  getAssetBible: vi.fn(),
  createStoryAssetReference: vi.fn().mockResolvedValue({ reference: { id: "ref-2" } }),
}));
vi.mock("../features/asset-bible-v2/ProjectAssetImageWorkbench", () => ({
  ProjectAssetImageWorkbench: () => <div>一键生成核心资产主图</div>,
}));
vi.mock("../features/asset-bible-v2/CharacterIdentityPackPanel", () => ({
  CharacterIdentityPackPanel: ({ assetName, baseReferences, onRequestMissingSlots }: { assetName: string; baseReferences?: Array<{ reference_kind: string }>; onRequestMissingSlots?: (slots: string[]) => void }) => <section aria-label="角色身份包审核"><strong>{assetName} 身份包审核</strong><small>项目级引用：{baseReferences?.map((reference) => reference.reference_kind).join(" / ") || "无"}</small><button type="button" onClick={() => onRequestMissingSlots?.(["FRONT", "LEFT", "RIGHT"])}>生成缺失三视图</button></section>,
}));
vi.mock("../features/media-picker/MediaPicker", () => ({
  MediaPicker: ({ onChange, label }: { onChange: (value: string) => void; label: string }) => <button type="button" onClick={() => onChange("media-2")}>{label}</button>,
}));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/projects/p1/assets"]}>
        <Routes><Route path="/projects/:projectId/assets" element={<AssetBiblePage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function characterItem() {
  return {
    asset: { id: "c1", kind: "CHARACTER", code: "CHAR_HERO", name: "阿宁", description: "倔强的年轻女主角", status: "ACTIVE", revision: 2, canonical_media_version_id: "m1" },
    states: [],
    base_references: [{ id: "r1", project_id: "p1", story_asset_id: "c1", asset_state_id: null, media_version_id: "m1", reference_kind: "HERO", label: "", priority: 100, is_locked: 1, yaw_deg: null, pitch_deg: null, status: "ACTIVE", revision: 1 }],
    active_state_id: null,
    voice: null,
    usage: { episode_ids: [], episodes: [], shots: [], shot_count: 0 },
    readiness: { level: "BASIC" as const, missing: ["FRONT", "LEFT", "RIGHT"] },
  };
}

describe("AssetBiblePage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("saves visual descriptions through the asset revision contract and resets unsaved text on character switch", async () => {
    const other = { ...characterItem(), asset: { ...characterItem().asset, id: "c2", name: "阿乔", description: "红衣短发" } };
    vi.mocked(getAssetBible).mockResolvedValue({ bible: { project_id: "p1", asset_count: 2, items: [characterItem(), other] } });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "编辑文字设定" }));
    fireEvent.change(screen.getByLabelText("外观与剧情设定"), { target: { value: "灰衣长发，冷静的年轻女性" } });
    fireEvent.click(screen.getByRole("button", { name: "保存文字设定" }));
    await waitFor(() => expect(updateStoryAsset).toHaveBeenCalledWith("c1", { expected_revision: 2, description: "灰衣长发，冷静的年轻女性" }));
    await screen.findByText("文字设定已保存，将用于后续生成。");
    fireEvent.click(screen.getByRole("button", { name: "编辑文字设定" }));
    fireEvent.change(screen.getByLabelText("外观与剧情设定"), { target: { value: "未保存文字" } });
    fireEvent.click(screen.getByRole("button", { name: /阿乔 资产缩略图/ }));
    fireEvent.click(screen.getByRole("button", { name: "编辑文字设定" }));
    expect((screen.getByLabelText("外观与剧情设定") as HTMLTextAreaElement).value).toBe("红衣短发");
  });

  it("shows only the compact core-asset workflow", async () => {
    vi.mocked(getAssetBible).mockResolvedValue({ bible: { project_id: "p1", asset_count: 1, items: [characterItem()] } });
    renderPage();

    expect((await screen.findAllByText("阿宁")).length).toBeGreaterThan(0);
    expect(screen.getByText("倔强的年轻女主角")).toBeTruthy();
    expect(screen.getByAltText("阿宁 主参考")).toBeTruthy();
    expect(screen.getByText("一键生成核心资产主图")).toBeTruthy();
    expect(screen.getByRole("region", { name: "角色身份包审核" })).toBeTruthy();
    expect(screen.getByText("阿宁 身份包审核")).toBeTruthy();
    expect(screen.queryByText("人物多视图")).toBeNull();
    expect(screen.queryByText("角色声音")).toBeNull();
    expect(screen.queryByText("剧情中的外观变化")).toBeNull();
  });

  it("passes the selected character's project-level references into the identity-pack workflow", async () => {
    const item = characterItem();
    item.base_references = ["FRONT", "LEFT", "RIGHT"].map((reference_kind, index) => ({
      id: `r-${reference_kind}`,
      project_id: "p1",
      story_asset_id: "c1",
      asset_state_id: null,
      media_version_id: `m-${index}`,
      reference_kind,
      label: "",
      priority: index + 1,
      is_locked: 1,
      yaw_deg: null,
      pitch_deg: null,
      status: "ACTIVE",
      revision: 1,
    }));
    vi.mocked(getAssetBible).mockResolvedValue({ bible: { project_id: "p1", asset_count: 1, items: [item] } });
    renderPage();

    expect(await screen.findByText("项目级引用：FRONT / LEFT / RIGHT")).toBeTruthy();
  });

  it("shows a simple empty state when AI has not created this kind", async () => {
    vi.mocked(getAssetBible).mockResolvedValue({ bible: { project_id: "p1", asset_count: 0, items: [] } });
    renderPage();
    expect(await screen.findByText("还没有人物资产")).toBeTruthy();
    expect(screen.getByText("当前没有可编辑资产")).toBeTruthy();
  });

  it("shows loading and a recoverable error", async () => {
    let reject!: (reason: Error) => void;
    vi.mocked(getAssetBible).mockImplementation(() => new Promise((_resolve, nextReject) => { reject = nextReject; }));
    renderPage();
    expect(screen.getByRole("status").getAttribute("aria-label")).toContain("正在读取核心资产");
    reject(new Error("本机数据库暂不可用"));
    expect((await screen.findByRole("alert")).textContent).toContain("本机数据库暂不可用");
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });

  it("keeps manual asset creation as an optional compact action", async () => {
    vi.mocked(getAssetBible).mockResolvedValue({ bible: { project_id: "p1", asset_count: 0, items: [] } });
    renderPage();
    fireEvent.click(await screen.findByText("手动添加人物（可选）"));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "新角色" } });
    fireEvent.click(screen.getByRole("button", { name: "添加资产" }));
    await waitFor(() => expect(createStoryAsset).toHaveBeenCalledWith("p1", expect.objectContaining({ name: "新角色", code: expect.stringMatching(/^CHAR_/) })));
  });

  it("lets the user replace only the main reference", async () => {
    vi.mocked(getAssetBible).mockResolvedValue({ bible: { project_id: "p1", asset_count: 1, items: [characterItem()] } });
    renderPage();
    fireEvent.click(await screen.findByText("替换主参考"));
    fireEvent.click(screen.getByRole("button", { name: "主参考图片" }));
    fireEvent.click(screen.getByRole("button", { name: "设为主参考" }));
    await waitFor(() => expect(createStoryAssetReference).toHaveBeenCalledWith("c1", {
      media_version_id: "media-2",
      reference_kind: "HERO",
      is_locked: true,
    }));
  });
});
