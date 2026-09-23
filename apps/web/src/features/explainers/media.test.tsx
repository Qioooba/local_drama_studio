/**
 * Contracts for the real media players and the manifest-driven review timeline.
 *
 * These assert the corrected behaviour, not the old placeholder:
 *  - frame conversion uses the *rational* frame rate, not a hard-coded 25 fps;
 *  - frame stepping is clamped to ``0 … frame_count - 1``;
 *  - an unplayable render states why instead of showing "尚未生成媒体";
 *  - the timeline is built from real manifest ranges and is empty when there are none;
 *  - the issue list stays stable, sorted and fully reachable.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  RenderPlayer,
  ReviewTimeline,
  clampFrame,
  frameForMs,
  lanesFromManifest,
} from "./media";
import type { RenderMedia } from "./media";

function media(overrides: Partial<RenderMedia> = {}): RenderMedia {
  return {
    id: "render-1",
    media_version_id: "mv-1",
    mime_type: "video/mp4",
    byte_size: 4096,
    duration_ms: 8342,
    frame_count: 250,
    fps_num: 30000,
    fps_den: 1001,
    playback_url: "/api/v1/media-versions/mv-1/content",
    availability: "PLAYABLE",
    playable: true,
    sha256: "a".repeat(64),
    integrity_status: "VERIFIED",
    status: "SUCCEEDED",
    ...overrides,
  };
}

describe("frame arithmetic", () => {
  it("uses the rational frame rate instead of a hard-coded 25 fps", () => {
    // 1000 ms at 30000/1001 is frame 29, not frame 25.
    expect(frameForMs(1000, 30000, 1001)).toBe(29);
    expect(frameForMs(1000, 25, 1)).toBe(25);
    expect(frameForMs(400, 25, 1)).toBe(10);
  });

  it("refuses to convert when the version has no recorded frame rate", () => {
    expect(frameForMs(1000, null, null)).toBeNull();
    expect(frameForMs(1000, 25, 0)).toBeNull();
  });

  it("clamps a requested frame into the real frame range", () => {
    expect(clampFrame(-5, 250)).toBe(0);
    expect(clampFrame(300, 250)).toBe(249);
    expect(clampFrame(120, 250)).toBe(120);
    // With no known frame count the negative case is still clamped.
    expect(clampFrame(-1, null)).toBe(0);
  });
});

describe("RenderPlayer", () => {
  it("renders a real <video> element bound to the controlled media url", () => {
    const { container } = render(<RenderPlayer media={media()} />);
    const video = container.querySelector("video");
    expect(video).not.toBeNull();
    expect(video?.getAttribute("src")).toBe("/api/v1/media-versions/mv-1/content");
    expect(video?.hasAttribute("controls")).toBe(true);
    expect(screen.getByText("30000/1001 fps · 250 帧")).toBeTruthy();
  });

  it("says there is no render instead of pretending to have media", () => {
    const { container } = render(<RenderPlayer media={null} />);
    expect(container.querySelector("video")).toBeNull();
    expect(screen.getByText("尚未生成成片")).toBeTruthy();
  });

  it("reports a missing media reference explicitly", () => {
    const { container } = render(
      <RenderPlayer media={media({ availability: "MEDIA_REFERENCE_MISSING", playable: false, playback_url: null })} />,
    );
    expect(container.querySelector("video")).toBeNull();
    expect(screen.getByText("渲染记录存在，但已找不到对应的媒体版本")).toBeTruthy();
  });

  it("treats an unverified render as unplayable", () => {
    const { container } = render(
      <RenderPlayer media={media({ availability: "INTEGRITY_FAILED", playable: false })} />,
    );
    expect(container.querySelector("video")).toBeNull();
    // The reason is shown both in the placeholder and in the inline error.
    expect(screen.getAllByText("媒体完整性校验未通过，拒绝播放").length).toBeGreaterThan(0);
  });

  it("calls out a version without a frame rate instead of faking frame stepping", () => {
    render(<RenderPlayer media={media({ fps_num: null, fps_den: null })} />);
    expect(screen.getByText("该版本未记录帧率，不能按帧定位")).toBeTruthy();
  });
});

describe("lanesFromManifest", () => {
  const items = [
    { id: "v1", track: "VIDEO", item_kind: "VIDEO_CLIP", start_frame: 0, end_frame_exclusive: 50 },
    { id: "v2", track: "VIDEO", item_kind: "VIDEO_CLIP", start_frame: 50, end_frame_exclusive: 100 },
    { id: "n1", track: "NARRATION", item_kind: "AUDIO_CLIP", start_frame: 0, end_frame_exclusive: 100 },
  ];

  it("builds one lane per track with real millisecond ranges", () => {
    const lanes = lanesFromManifest(items, { fpsNum: 25, fpsDen: 1 });
    expect(lanes.map((lane) => lane.track)).toEqual(["VIDEO", "NARRATION"]);
    expect(lanes[0].segments).toHaveLength(2);
    expect(lanes[0].segments[1].startMs).toBe(2000);
    expect(lanes[0].segments[1].endMs).toBe(4000);
    expect(lanes[1].label).toBe("旁白");
  });

  it("returns nothing rather than inventing structure when the manifest is absent", () => {
    expect(lanesFromManifest([], { fpsNum: 25, fpsDen: 1 })).toEqual([]);
    expect(lanesFromManifest(items, { fpsNum: null, fpsDen: null })).toEqual([]);
  });
});

describe("ReviewTimeline", () => {
  it("draws the real lanes", () => {
    const lanes = lanesFromManifest(
      [{ id: "v1", track: "VIDEO", item_kind: "VIDEO_CLIP", start_frame: 0, end_frame_exclusive: 25 }],
      { fpsNum: 25, fpsDen: 1 },
    );
    render(<ReviewTimeline lanes={lanes} busy={false} />);
    expect(screen.getByText("画面")).toBeTruthy();
    expect(screen.queryByText("起始")).toBeNull();
  });

  it("explains an empty timeline instead of showing placeholder blocks", () => {
    render(<ReviewTimeline lanes={[]} busy={false} />);
    expect(screen.getByText(/尚无可绘制的 manifest 区间/)).toBeTruthy();
  });

  it("shows a loading line while the edition list is pending", () => {
    render(<ReviewTimeline lanes={[]} busy />);
    expect(screen.getByText(/正在载入冻结 manifest 时间线/)).toBeTruthy();
  });
});

describe("issue list reachability", () => {
  it("keeps every issue reachable and sorts stably by severity then time", async () => {
    const { useSortedIssues } = await import("./media");
    const issues = Array.from({ length: 12 }, (_, index) => ({
      id: `issue-${String(index).padStart(2, "0")}`,
      severity: index === 11 ? "BLOCKER" : "MINOR",
      start_ms: index * 100,
    }));
    function Probe() {
      const list = useSortedIssues(issues, { pageSize: 8 });
      return (
        <div>
          <span data-testid="visible">{list.visible.length}</span>
          <span data-testid="hidden">{list.hiddenCount}</span>
          <span data-testid="first">{String(list.sorted[0]?.id ?? "")}</span>
          <button type="button" onClick={list.showAll}>all</button>
        </div>
      );
    }
    render(<Probe />);
    expect(screen.getByTestId("visible").textContent).toBe("8");
    expect(screen.getByTestId("hidden").textContent).toBe("4");
    // The BLOCKER is first even though it has the largest start_ms.
    expect(screen.getByTestId("first").textContent).toBe("issue-11");
  });
});
