import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ImageCandidateGrid } from "./ImageCandidateGrid";

const image = { media_version_id: "image-001", media_asset_id: "asset-1", project_id: "project-1", media_kind: "IMAGE", stage: "KEYFRAME", decision: null, is_stale: 0 };
const image2 = { ...image, media_version_id: "image-002" };
const image3 = { ...image, media_version_id: "image-003" };
const video = { media_version_id: "video-001", media_asset_id: "asset-2", project_id: "project-1", media_kind: "VIDEO", stage: "PROXY", decision: null, is_stale: 0 };

describe("ImageCandidateGrid", () => {
  it("shows only image candidates with derived thumbnails and selects a card", () => {
    const onSelect = vi.fn();
    render(<ImageCandidateGrid items={[image, video] as never[]} selectedVersionId={null} onSelect={onSelect} />);
    expect(screen.getByRole("heading", { name: "图片候选缩略图网格" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /image-001/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /video-001/ })).toBeNull();
    expect(screen.getByRole("button", { name: /image-001/ }).querySelector("img")?.getAttribute("src")).toContain("thumbnail?size=small");
    fireEvent.click(screen.getByRole("button", { name: /image-001/ }));
    expect(onSelect).toHaveBeenCalledWith("image-001");
  });

  it("renders no panel when there are no image candidates", () => {
    render(<ImageCandidateGrid items={[video] as never[]} selectedVersionId={null} onSelect={vi.fn()} />);
    expect(screen.queryByRole("heading", { name: "图片候选缩略图网格" })).toBeNull();
  });

  it("supports roving keyboard selection without ever leaving the thumbnail surface", () => {
    const onSelect = vi.fn();
    render(<ImageCandidateGrid items={[image, image2, image3] as never[]} selectedVersionId="image-001" onSelect={onSelect} />);
    const first = screen.getByRole("button", { name: /image-001/ });
    const second = screen.getByRole("button", { name: /image-002/ });
    expect(first.getAttribute("tabindex")).toBe("0");
    expect(second.getAttribute("tabindex")).toBe("-1");
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(onSelect).toHaveBeenCalledWith("image-002");
    expect(document.activeElement).toBe(second);
    expect(second.querySelector("img")?.getAttribute("src")).toContain("/thumbnail?size=small&frame=poster");
  });
});
