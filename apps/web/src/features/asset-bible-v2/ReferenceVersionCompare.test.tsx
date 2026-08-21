import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getAssetReferenceMediaVersion, type StoryAssetReference } from "./api";
import { ReferenceVersionCompare } from "./ReferenceVersionCompare";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, getAssetReferenceMediaVersion: vi.fn() };
});

function reference(id: string, mediaVersionId: string, kind: string, stateId: string | null = null): StoryAssetReference {
  return { id, project_id: "p1", story_asset_id: "asset-1", asset_state_id: stateId, media_version_id: mediaVersionId, reference_kind: kind, label: "", priority: 100, is_locked: false, yaw_deg: null, pitch_deg: null, status: "ACTIVE", revision: 1 };
}

function version(id: string, mediaKind: "IMAGE" | "VIDEO") {
  return {
    media_version: {
      id, media_asset_id: `asset-${id}`, version_no: id === "image-v1" ? 1 : 2, take_no: null,
      stage: "IMPORTED", mime_type: mediaKind === "VIDEO" ? "video/mp4" : "image/png", byte_size: 2048,
      duration_ms: mediaKind === "VIDEO" ? 2500 : null, sha256: "a".repeat(64), integrity_status: "VERIFIED",
      created_at: "2026-08-21T00:00:00Z", media_kind: mediaKind, probe: {},
    },
  };
}

function renderCompare(references: StoryAssetReference[]) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <ReferenceVersionCompare references={references} states={[{ id: "night", code: "NIGHT", label: "夜景", state_kind: "TIME_OF_DAY", description: "", state: {}, references: [] }]} />
  </QueryClientProvider>);
}

describe("ReferenceVersionCompare", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getAssetReferenceMediaVersion).mockImplementation(async (id) => version(id, id.startsWith("video") ? "VIDEO" : "IMAGE"));
  });

  it("compares immutable metadata while keeping images thumbnail-only and videos opt-in", async () => {
    const { container } = renderCompare([
      reference("r1", "image-v1", "HERO"),
      reference("r2", "video-v2", "SCENE_WIDE", "night"),
    ]);
    expect(await screen.findByText("v1")).toBeTruthy();
    expect(screen.getByText("v2")).toBeTruthy();
    expect(within(screen.getByLabelText("比较版本 A")).getByRole("option", { name: /主参考 · 基础资产/ })).toBeTruthy();
    expect(within(screen.getByLabelText("比较版本 B")).getByRole("option", { name: /大全景 · 夜景（NIGHT）/ })).toBeTruthy();
    const image = screen.getByAltText("版本 A 参考缩略图") as HTMLImageElement;
    expect(image.getAttribute("src")).toContain("/thumbnail?size=medium&frame=poster");
    expect(image.getAttribute("src")).not.toContain("/content");
    const video = container.querySelector("video") as HTMLVideoElement;
    expect(video.getAttribute("preload")).toBe("none");
    expect(video.getAttribute("poster")).toContain("/thumbnail?size=medium&frame=poster");
    expect(video.getAttribute("src")).toContain("/content");
    expect(container.textContent).not.toContain("rel_path");
  });

  it("uses distinct semantic selectors and fetches only the two selected versions", async () => {
    renderCompare([
      reference("r1", "image-v1", "HERO"),
      reference("r2", "video-v2", "FRONT"),
      reference("r3", "image-v3", "LEFT"),
    ]);
    await waitFor(() => expect(getAssetReferenceMediaVersion).toHaveBeenCalledTimes(2));
    fireEvent.change(screen.getByLabelText("比较版本 B"), { target: { value: "image-v3" } });
    await waitFor(() => expect(getAssetReferenceMediaVersion).toHaveBeenCalledWith("image-v3"));
    expect((within(screen.getByLabelText("比较版本 B")).getByRole("option", { name: /主参考 · 基础资产/ }) as HTMLOptionElement).disabled).toBe(true);
  });

  it("explains why comparison is unavailable for one immutable version", () => {
    renderCompare([reference("r1", "image-v1", "HERO"), reference("r2", "image-v1", "FRONT")]);
    expect(screen.getByText(/至少需要两个不同的参考媒体版本/)).toBeTruthy();
    expect(getAssetReferenceMediaVersion).not.toHaveBeenCalled();
  });
});
