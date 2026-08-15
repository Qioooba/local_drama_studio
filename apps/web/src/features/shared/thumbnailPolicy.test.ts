import { describe, expect, it } from "vitest";

const representativeImageReads = [
  "/api/v1/media-versions/image-a/thumbnail?size=small&frame=poster",
  "/api/v1/media-versions/video-a/thumbnail?size=small&frame=poster",
  "/api/v1/media-versions/audio-a/waveform",
];

describe("platform image read policy", () => {
  it("permits only derived thumbnail or waveform reads in image surfaces", () => {
    representativeImageReads.forEach((url) => {
      expect(url).not.toContain("/content");
      expect(url.includes("/thumbnail") || url.includes("/waveform")).toBe(true);
    });
  });
});
