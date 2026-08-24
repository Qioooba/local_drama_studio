import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MediaPicker } from "./MediaPicker";

const item = {
  media_version_id: "version-1",
  media_asset_id: "asset-1",
  version_no: 2,
  take_no: 2,
  stage: "IMPORTED",
  source_name: "hero.png",
  mime_type: "image/png",
  byte_size: 2048,
  duration_ms: null,
  updated_at: "2026-08-20T00:00:00Z",
  purpose: "ASSET_REFERENCE",
  media_kind: "IMAGE",
};

function renderPicker(onChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MediaPicker projectId="project-1" value="" onChange={onChange} /></QueryClientProvider>);
  return onChange;
}

describe("MediaPicker", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows only thumbnail cards and returns the immutable version selection", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [item] }), { status: 200, headers: { "Content-Type": "application/json" } })));
    const onChange = renderPicker();
    const card = await screen.findByRole("radio", { name: /hero\.png/ });
    const thumbnail = document.querySelector(".media-picker-card img");
    expect(thumbnail?.getAttribute("src")).toContain("/thumbnail?size=small&frame=poster");
    expect(thumbnail?.getAttribute("src")).not.toContain("/content");
    fireEvent.click(card);
    expect(onChange).toHaveBeenCalledWith("version-1", expect.objectContaining({ source_name: "hero.png" }));
  });

  it("uploads an image and selects the returned version", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/session/bootstrap")) return new Response(JSON.stringify({ token: "local-token", mode: "LOCAL_ONLY" }), { status: 200 });
      if (url.includes("/media:upload")) return new Response(JSON.stringify({ media: { media_version_id: "uploaded-version" } }), { status: 201 });
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetch);
    const onChange = renderPicker();
    const input = await screen.findByLabelText("上传图片");
    fireEvent.change(input, { target: { files: [new File(["image"], "portrait.png", { type: "image/png" })] } });
    await waitFor(() => expect(onChange).toHaveBeenCalledWith("uploaded-version"));
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/media:upload"), expect.objectContaining({ method: "POST", body: expect.any(File) }));
  });

  it("lists AUDIO semantically without requesting an image or original media", async () => {
    const audio = { ...item, media_version_id: "audio-version", source_name: "dialogue-preview.wav", mime_type: "audio/wav", media_kind: "AUDIO", duration_ms: 4200 };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [audio] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MediaPicker projectId="project-1" mediaKind="AUDIO" allowUpload={false} value="" onChange={vi.fn()} label="音频选择器" /></QueryClientProvider>);
    expect(await screen.findByRole("radio", { name: /dialogue-preview\.wav/ })).toBeTruthy();
    expect(document.querySelector(".media-picker-card img")).toBeNull();
    expect(String(fetch.mock.calls[0]?.[0])).toContain("media_kind=AUDIO");
    expect(String(fetch.mock.calls[0]?.[0])).not.toContain("/content");
  });
});
