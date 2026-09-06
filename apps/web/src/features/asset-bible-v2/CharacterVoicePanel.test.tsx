import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CharacterVoicePanel } from "./CharacterVoicePanel";

const api = vi.hoisted(() => ({
  discover: vi.fn(),
  profiles: vi.fn(),
  bindings: vi.fn(),
  publish: vi.fn(),
  bind: vi.fn(),
}));

vi.mock("../../generated/api", () => ({
  discoverLocalSapiVoices: api.discover,
  listVoiceProfileVersions: api.profiles,
  listCharacterVoiceBindings: api.bindings,
  publishProjectLocalSapiVoiceProfile: api.publish,
  bindCharacterVoice: api.bind,
}));

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><CharacterVoicePanel projectId="project-1" assetId="asset-1" assetName="沈砚" /></QueryClientProvider>);
}

describe("CharacterVoicePanel", () => {
  it("makes local-only provenance and the publish/bind steps visible", async () => {
    api.profiles.mockResolvedValue({ items: [] });
    api.bindings.mockResolvedValue({ items: [] });
    api.discover.mockResolvedValue({ status: "AVAILABLE", items: [{ name: "Microsoft Huihui Desktop", culture: "zh-CN", gender: "Female", age: "Adult", voice_ref: "sapi:Microsoft Huihui Desktop" }], message: null, runtime_contacted: true, network_contacted: false, mutated: false });
    api.publish.mockResolvedValue({ voice_profile: { id: "profile-1", project_id: "project-1", code: "sapi-local-1", version_no: 1, title: "Huihui · 本机测试音色", voice_ref: "sapi:Microsoft Huihui Desktop", license_status: "VERIFIED_LOCAL", license_evidence: { provenance: "LOCAL_OS_INSTALLED", distribution_scope: "LOCAL_TEST_ONLY" }, provider_profile_version_id: "exec-1", status: "ACTIVE" } });
    api.bind.mockResolvedValue({ binding: { id: "binding-1" } });
    renderPanel();

    expect(screen.getByText(/不代表商业分发授权/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "扫描本机 SAPI 音色" }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "本机 SAPI 音色" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "冒烟并发布本机 Profile" }));
    await waitFor(() => expect(api.publish).toHaveBeenCalledWith("project-1", { voice_ref: "sapi:Microsoft Huihui Desktop", smoke_text: "本集对白本机离线试听" }));
    fireEvent.click(screen.getByRole("button", { name: "绑定到当前角色" }));
    await waitFor(() => expect(api.bind).toHaveBeenCalledWith("project-1", { character_asset_id: "asset-1", voice_profile_version_id: "profile-1" }));
    expect(await screen.findByText(/已绑定本机测试音色/)).toBeTruthy();
  });
});
