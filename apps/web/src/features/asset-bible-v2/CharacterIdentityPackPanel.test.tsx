import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CharacterIdentityPackPanel } from "./CharacterIdentityPackPanel";
import * as client from "./identityPackClient";
import { authorizeWorkspaceAsset } from "../../generated/api";

vi.mock("../../generated/api", () => ({
  authorizeWorkspaceAsset: vi.fn(),
}));

vi.mock("./identityPackClient", () => ({
  listCharacterIdentityPacks: vi.fn(),
  getCharacterIdentityPack: vi.fn(),
  getCharacterIdentityPackVersion: vi.fn(),
  createCharacterIdentityPack: vi.fn(),
  createCharacterIdentityPackVersion: vi.fn(),
  setCharacterIdentityPackSlot: vi.fn(),
  removeCharacterIdentityPackSlot: vi.fn(),
  approveCharacterIdentityPackVersion: vi.fn(),
  compareCharacterIdentityPackVersions: vi.fn(),
  getCharacterIdentityPackVersionImpact: vi.fn(),
  retireCharacterIdentityPackVersion: vi.fn(),
}));

vi.mock("../media-picker/MediaPicker", () => ({
  MediaPicker: ({ onChange }: { onChange: (id: string) => void }) => <button type="button" onClick={() => onChange("media-front-selected")}>选择 hero-front.png</button>,
}));

const baseVersion: client.CharacterIdentityPackVersion = {
  id: "v-1",
  pack_id: "pack-1",
  project_id: "prj-1",
  story_asset_id: "char-1",
  asset_state_id: null,
  version_no: 1,
  status: "DRAFT",
  slots_map: {},
  slots: [],
  missing_required_slots: ["FRONT", "LEFT", "RIGHT"],
  approval_ready: false,
  created_at: "2026-08-21T00:00:00Z",
  updated_at: "2026-08-21T00:00:00Z",
};

const basePack: client.CharacterIdentityPack = {
  id: "pack-1",
  project_id: "prj-1",
  story_asset_id: "char-1",
  asset_state_id: null,
  code: "DEFAULT",
  name: "标准西装外观",
  description: "",
  status: "ACTIVE",
  current_version_id: null,
  created_at: "2026-08-21T00:00:00Z",
  updated_at: "2026-08-21T00:00:00Z",
  versions: [baseVersion],
};

function prime(version = baseVersion, pack = basePack) {
  vi.mocked(client.listCharacterIdentityPacks).mockResolvedValue({ items: [pack] });
  vi.mocked(client.getCharacterIdentityPack).mockResolvedValue({ pack });
  vi.mocked(client.getCharacterIdentityPackVersion).mockResolvedValue({ version });
}

describe("CharacterIdentityPackPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(authorizeWorkspaceAsset).mockResolvedValue({ authorization: {} });
  });

  it("renders an actionable empty state without manufacturing an approved pack", async () => {
    vi.mocked(client.listCharacterIdentityPacks).mockResolvedValue({ items: [] });
    render(<CharacterIdentityPackPanel projectId="prj-1" storyAssetId="char-1" assetName="主角林远" />);
    expect(await screen.findByText("还没有角色造型参考")).toBeTruthy();
    expect(screen.getByRole("button", { name: "新建造型" })).toBeTruthy();
    expect(client.approveCharacterIdentityPackVersion).not.toHaveBeenCalled();
  });

  it("creates a named look with a system-generated code instead of asking the creator for one", async () => {
    vi.mocked(client.listCharacterIdentityPacks).mockResolvedValue({ items: [] });
    vi.mocked(client.createCharacterIdentityPack).mockResolvedValue({ pack: basePack });
    render(<CharacterIdentityPackPanel projectId="prj-1" storyAssetId="char-1" assetName="主角林远" />);
    fireEvent.click(await screen.findByRole("button", { name: "新建造型" }));
    expect(screen.queryByLabelText("身份包代码")).toBeNull();
    fireEvent.change(screen.getByLabelText("造型名称"), { target: { value: "雨夜造型" } });
    fireEvent.click(screen.getByRole("button", { name: "创建并开始补参考图" }));
    await waitFor(() => expect(client.createCharacterIdentityPack).toHaveBeenCalledWith("char-1", {
      project_id: "prj-1",
      code: expect.stringMatching(/^LOOK_/),
      name: "雨夜造型",
    }));
  });

  it("shows every missing required view and keeps approval fail-closed", async () => {
    prime();
    const requestMissing = vi.fn();
    render(<CharacterIdentityPackPanel projectId="prj-1" storyAssetId="char-1" assetName="主角林远" onRequestMissingSlots={requestMissing} />);

    expect(await screen.findByText(/缺少必需视角：FRONT \/ LEFT \/ RIGHT/)).toBeTruthy();
    const approve = screen.getByRole("button", { name: "人工审核并批准" });
    expect(approve.hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "生成缺失三视图" }));
    expect(requestMissing).toHaveBeenCalledWith(["FRONT", "LEFT", "RIGHT"]);
    expect(client.approveCharacterIdentityPackVersion).not.toHaveBeenCalled();
  });

  it("uses the project media picker instead of a UUID text field", async () => {
    prime();
    const updated = {
      ...baseVersion,
      slots_map: { FRONT: "media-front-selected" },
      slots: [{ id: "slot-1", pack_version_id: "v-1", slot_kind: "FRONT", media_version_id: "media-front-selected", is_primary: true, created_at: "2026-08-21T00:00:00Z", integrity_status: "VERIFIED", media_kind: "IMAGE" }],
      missing_required_slots: ["LEFT", "RIGHT"],
    } satisfies client.CharacterIdentityPackVersion;
    vi.mocked(client.setCharacterIdentityPackSlot).mockResolvedValue({ version: updated });

    render(<CharacterIdentityPackPanel projectId="prj-1" storyAssetId="char-1" assetName="主角林远" />);
    await screen.findByTestId("slot-FRONT");
    expect(screen.queryByPlaceholderText("媒体ID")).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: "选择图片" })[0]);
    fireEvent.click(await screen.findByRole("button", { name: "选择 hero-front.png" }));
    fireEvent.click(screen.getByRole("button", { name: "授权并绑定此版本" }));
    await waitFor(() => expect(authorizeWorkspaceAsset).toHaveBeenCalledWith("prj-1", "media-front-selected"));
    await waitFor(() => expect(client.setCharacterIdentityPackSlot).toHaveBeenCalledWith("v-1", {
      slot_kind: "FRONT",
      media_version_id: "media-front-selected",
    }));
  });

  it("requires an explicit human comment before approving a complete unique three-view", async () => {
    const complete = {
      ...baseVersion,
      slots_map: { FRONT: "front", LEFT: "left", RIGHT: "right" },
      slots: [
        { id: "s-front", pack_version_id: "v-1", slot_kind: "FRONT", media_version_id: "front", is_primary: true, created_at: "now", integrity_status: "VERIFIED" },
        { id: "s-left", pack_version_id: "v-1", slot_kind: "LEFT", media_version_id: "left", is_primary: true, created_at: "now", integrity_status: "VERIFIED" },
        { id: "s-right", pack_version_id: "v-1", slot_kind: "RIGHT", media_version_id: "right", is_primary: true, created_at: "now", integrity_status: "VERIFIED" },
      ],
      missing_required_slots: [],
      approval_ready: true,
    } satisfies client.CharacterIdentityPackVersion;
    const pack = { ...basePack, versions: [complete] };
    prime(complete, pack);
    vi.mocked(client.approveCharacterIdentityPackVersion).mockResolvedValue({ version: { ...complete, status: "APPROVED" } });

    render(<CharacterIdentityPackPanel projectId="prj-1" storyAssetId="char-1" assetName="主角林远" />);
    fireEvent.click(await screen.findByRole("button", { name: "人工审核并批准" }));
    const confirm = screen.getByRole("button", { name: "确认批准并锁定" });
    expect(confirm.hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText("审核说明"), { target: { value: "三视图身份与服装细节人工核对通过" } });
    fireEvent.click(confirm);
    await waitFor(() => expect(client.approveCharacterIdentityPackVersion).toHaveBeenCalledWith("v-1", "三视图身份与服装细节人工核对通过"));
  });

  it("does not expose slot mutation controls on superseded evidence", async () => {
    const superseded = { ...baseVersion, status: "SUPERSEDED" as const };
    prime(superseded, { ...basePack, versions: [superseded] });
    render(<CharacterIdentityPackPanel projectId="prj-1" storyAssetId="char-1" assetName="主角林远" />);
    await screen.findByText("已被新版替代");
    expect(screen.queryByRole("button", { name: "选择图片" })).toBeNull();
    expect(screen.queryByRole("button", { name: "人工审核并批准" })).toBeNull();
  });
});
