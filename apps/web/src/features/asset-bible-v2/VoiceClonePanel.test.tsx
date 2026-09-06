import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { VoiceClonePanel } from "./VoiceClonePanel";

const api = vi.hoisted(() => ({ clone: vi.fn(), upload: vi.fn() }));

vi.mock("../../generated/api", () => ({ cloneCharacterVoice: api.clone }));
vi.mock("../media-picker/mediaPickerClient", () => ({ uploadProjectMediaFile: api.upload }));
vi.mock("../shared/mediaPlaybackPolicy", () => ({ mediaContentUrl: (id: string) => `/media/${id}` }));

describe("VoiceClonePanel", () => {
  it("uploads, previews, confirms consent, and binds a cloned character voice", async () => {
    api.upload.mockResolvedValue("media-reference");
    api.clone.mockResolvedValue({ voice: { title: "阿遥·平静" }, binding: { id: "binding-1" } });
    const onChanged = vi.fn();
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><VoiceClonePanel projectId="project-1" assetId="asset-1" assetName="阿遥" onChanged={onChanged} /></QueryClientProvider>);

    const file = new File([new Uint8Array([1, 2, 3])], "voice.wav", { type: "audio/wav" });
    fireEvent.change(screen.getByLabelText("参考音频（2—15 秒，wav/mp3）"), { target: { files: [file] } });
    await waitFor(() => expect(api.upload).toHaveBeenCalledWith("project-1", file));
    expect((await screen.findByLabelText("参考音频试听")).getAttribute("src")).toBe("/media/media-reference");
    fireEvent.change(screen.getByLabelText("声线名称"), { target: { value: "阿遥·平静" } });
    fireEvent.change(screen.getByLabelText(/参考音频的文字内容/), { target: { value: "今晚别回头" } });
    fireEvent.click(screen.getByLabelText(/我确认拥有该声音的克隆授权/));
    fireEvent.click(screen.getByRole("button", { name: "创建克隆声线并绑定角色" }));

    await waitFor(() => expect(api.clone).toHaveBeenCalledWith("project-1", "asset-1", {
      media_version_id: "media-reference",
      title: "阿遥·平静",
      transcript: "今晚别回头",
      consent: true,
    }));
    expect(onChanged).toHaveBeenCalled();
    expect((await screen.findByRole("status")).textContent).toContain("已创建克隆声线");
  });
});
