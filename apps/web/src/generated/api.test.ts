import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  bindEpisodeAudio,
  buildDeliveryPackage,
  createSubtitleRevision,
  createTimelineRevision,
  renderEpisode,
  verifyDeliveryPackage,
  withdrawDeliveryPackage,
} from "./api";

describe("generated G8 timeline client", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("posts an immutable timeline revision with its input snapshot", async () => {
    await createTimelineRevision("episode/1", {
      items: [{ track_type: "VIDEO", media_version_id: "media-1", start_us: 0, end_us: 1_000_000 }],
      input_snapshot: { source_revision: 3 },
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/episodes/episode%2F1/timeline-revisions",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ items: [{ track_type: "VIDEO", media_version_id: "media-1", start_us: 0, end_us: 1_000_000 }], input_snapshot: { source_revision: 3 } }) }),
    );
  });

  it("posts subtitle and authorized audio bindings through encoded episode paths", async () => {
    await createSubtitleRevision("episode/1", { cues: [{ start_us: 0, end_us: 500_000, text: "local" }], format: "SRT" });
    await bindEpisodeAudio("episode/1", { media_version_id: "audio/1", track_type: "DIALOGUE", start_us: 0, end_us: 500_000, source_license_status: "USER_OWNED" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/episodes/episode%2F1/subtitle-revisions");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/episodes/episode%2F1/audio-bindings");
  });

  it("uses explicit render and delivery endpoints without hidden requests", async () => {
    await renderEpisode("timeline/1");
    await buildDeliveryPackage({ episode_render_version_id: "render/1", target_version_id: "target/1" });
    await verifyDeliveryPackage("package/1");
    await withdrawDeliveryPackage("package/1", "integrity review");
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/timeline-revisions/timeline%2F1:render",
      "/api/v1/delivery-packages",
      "/api/v1/delivery-packages/package%2F1:verify",
      "/api/v1/delivery-packages/package%2F1:withdraw",
    ]);
  });
});
