import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { archiveStoryAsset, createStoryAsset, listStoryAssets, type StoryAsset } from "../../generated/api";
import { StoryAssetLibraryPanel } from "./StoryAssetLibraryPanel";

vi.mock("../../generated/api", () => ({ archiveStoryAsset: vi.fn(), createStoryAsset: vi.fn(), listStoryAssets: vi.fn() }));
vi.mock("../media-picker/MediaPicker", () => ({ MediaPicker: ({ onChange, label }: { onChange: (value: string) => void; label: string }) => <button type="button" aria-label={label} onClick={() => onChange("version-9")}>选择 妹妹主参考</button> }));

const character: StoryAsset = { id: "asset-1", project_id: "project-1", kind: "CHARACTER", code: "CHAR_MOTHER", name: "母亲", description: "短发", canonical_media_version_id: null, extra: {}, status: "ACTIVE", revision: 1, created_at: "now", updated_at: "now", created_by: "local-user", schema_version: "v2" };
const scene: StoryAsset = { ...character, id: "asset-2", kind: "SCENE", code: "SCENE_KITCHEN", name: "厨房", description: "暖光" };
const archivedCharacter: StoryAsset = { ...character, id: "asset-3", code: "CHAR_FATHER", name: "父亲", status: "ARCHIVED", revision: 2 };

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><StoryAssetLibraryPanel projectId="project-1" /></QueryClientProvider>);
}

describe("StoryAssetLibraryPanel", () => {
  beforeEach(() => {
    vi.mocked(listStoryAssets).mockReset().mockResolvedValue({ items: [character, scene, archivedCharacter] });
    vi.mocked(createStoryAsset).mockReset().mockResolvedValue({ asset: character });
    vi.mocked(archiveStoryAsset).mockReset().mockResolvedValue({ asset: { ...archivedCharacter, status: "ARCHIVED" } });
  });

  it("shows four tabs and filters cards by the active kind with status badges", async () => {
    renderPanel();
    expect(await screen.findByText("母亲")).toBeTruthy();
    expect(screen.getByText("CHAR_MOTHER")).toBeTruthy();
    expect(screen.getByText("已归档")).toBeTruthy();
    expect(screen.queryByText("厨房")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "场景" }));
    await waitFor(() => expect(screen.getByText("厨房")).toBeTruthy());
    expect(screen.queryByText("母亲")).toBeNull();
    expect(screen.getByText("启用中")).toBeTruthy();
  });

  it("creates an asset card with the tab kind and an optional canonical media version", async () => {
    renderPanel();
    await screen.findByText("母亲");
    fireEvent.click(screen.getByText("新建角色资产卡"));
    fireEvent.change(screen.getByLabelText("代码"), { target: { value: "CHAR_SISTER" } });
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "妹妹" } });
    fireEvent.change(screen.getByLabelText("描述"), { target: { value: "马尾" } });
    fireEvent.click(screen.getByRole("button", { name: "角色主参考选择器" }));
    fireEvent.click(screen.getByRole("button", { name: "创建角色资产卡" }));
    await waitFor(() => expect(createStoryAsset).toHaveBeenCalledWith("project-1", { kind: "CHARACTER", code: "CHAR_SISTER", name: "妹妹", description: "马尾", canonical_media_version_id: "version-9" }));
  });

  it("archives an active asset using its current revision", async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "归档 母亲" }));
    await waitFor(() => expect(archiveStoryAsset).toHaveBeenCalledWith("asset-1", { expected_revision: 1 }));
    expect((screen.getByRole("button", { name: "归档 父亲" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
