import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { ShotStudioShotNavItem } from "../../generated/api";
import { ShotNavigator } from "./ShotNavigator";

const shotEdit = vi.hoisted(() => ({
  context: vi.fn(),
  plan: vi.fn(),
  commit: vi.fn(),
}));

vi.mock("../episode-plan-v2/shotEditingApi", () => ({
  getShotEditContext: shotEdit.context,
  planShotEdit: shotEdit.plan,
  commitShotEdit: shotEdit.commit,
}));

function shot(id: string, code: string, thumbnail: string | null = null): ShotStudioShotNavItem {
  return {
    id, code, order_key: code, scene_id: "scene-1", scene_code: "SC01", scene_title: "屋内",
    group_id: null, group_code: null, group_title: null, thumbnail_media_version_id: thumbnail, current_video_media_version_id: null,
    status: "READY", continuity_status: "CURRENT", job_status: null,
  };
}

function renderNavigator(initialEntry = "/projects/p1/episodes/e1/direct/s1", onChanged = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <ShotNavigator shots={[shot("s1", "S01", "media 1"), shot("s2", "S02"), shot("s3", "S03")]} selectedId="s1" projectId="p1" episodeId="e1" canEdit onChanged={onChanged} />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { onChanged, ...result };
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location-probe">{location.pathname}{location.search}</output>;
}

describe("ShotNavigator", () => {
  it("submits a formal plan and commit when a shot is dragged", async () => {
    shotEdit.context.mockResolvedValue({
      episode_id: "e1", ordering_token: "order-v7",
      items: [
        { id: "s1", code: "S01", order_key: "1", target_duration_ms: 1000, revision: 2 },
        { id: "s2", code: "S02", order_key: "2", target_duration_ms: 1000, revision: 3 },
        { id: "s3", code: "S03", order_key: "3", target_duration_ms: 1000, revision: 4 },
      ],
    });
    shotEdit.plan.mockResolvedValue({ valid: true, plan_hash: "plan-123", issues: [] });
    shotEdit.commit.mockResolvedValue({});
    const { onChanged } = renderNavigator();
    const sourceRow = screen.getByLabelText("选择镜头 S03").parentElement?.parentElement as HTMLElement;
    const targetRow = screen.getByLabelText("选择镜头 S01").parentElement?.parentElement as HTMLElement;
    const dataTransfer = { effectAllowed: "none", setData: vi.fn(), getData: vi.fn() };
    fireEvent.dragStart(sourceRow, { dataTransfer });
    fireEvent.dragOver(targetRow, { dataTransfer });
    fireEvent.drop(targetRow, { dataTransfer });

    await waitFor(() => expect(shotEdit.commit).toHaveBeenCalled());
    const expectedPayload = {
      ordering_token: "order-v7",
      reorder: { shot_id: "s3", before_shot_id: "s1", expected_revision: 4 },
      splits: [],
    };
    expect(shotEdit.plan).toHaveBeenCalledWith("e1", expectedPayload);
    expect(shotEdit.commit).toHaveBeenCalledWith("e1", expectedPayload, "plan-123");
    expect(onChanged).toHaveBeenCalled();
  });

  it("keeps previews on thumbnail endpoints and exposes honest selected-shot destinations", () => {
    const { container } = renderNavigator();
    const image = container.querySelector("img") as HTMLImageElement;
    expect(image.getAttribute("src")).toContain("/media-versions/media%201/thumbnail?size=medium&frame=poster");
    expect(image.getAttribute("src")).not.toContain("/content");

    fireEvent.click(screen.getByLabelText("选择镜头 S01"));
    fireEvent.click(screen.getByLabelText("选择镜头 S02"));
    expect(screen.getByRole("link", { name: "审核入口" }).getAttribute("href")).toBe("/projects/p1/episodes/e1/post/review");
    expect(screen.getByRole("link", { name: "生产入口" }).getAttribute("href")).toBe("/projects/p1/episodes/e1/production");
    expect(screen.getByText("2 已选")).toBeTruthy();
  });

  it("labels a bounded navigator window without claiming it is the whole episode", () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ShotNavigator shots={[shot("s11", "S11"), shot("s12", "S12")]} selectedId="s11" projectId="p1" episodeId="e1" totalShots={38} windowStart={10} windowEnd={12} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText("11–12 / 38 镜")).toBeTruthy();
    expect(screen.getByText(/当前为所选镜头附近的有界窗口/)).toBeTruthy();
  });

  it("starts a URL-recoverable ordered batch instead of opening only the first shot", async () => {
    renderNavigator();
    fireEvent.click(screen.getByLabelText("选择镜头 S02"));
    fireEvent.click(screen.getByLabelText("选择镜头 S03"));
    fireEvent.click(screen.getByRole("button", { name: "逐镜处理" }));
    await waitFor(() => expect(screen.getByTestId("location-probe").textContent).toMatch(
      /^\/projects\/p1\/episodes\/e1\/studio\/s2\?batch=ref%3A[^&]+&batchIndex=0$/,
    ));
    expect(screen.getByTestId("location-probe").textContent).not.toContain("s2%2Cs3");
  });

  it("restores the selected checkboxes from an existing batch URL", async () => {
    renderNavigator("/projects/p1/episodes/e1/studio/s2?batch=s1%2Cs2&batchIndex=1");
    await waitFor(() => expect(screen.getByText("2 已选")).toBeTruthy());
    expect((screen.getByLabelText("选择镜头 S01") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("选择镜头 S02") as HTMLInputElement).checked).toBe(true);
  });

  it("disables formal reorder while a search is active", () => {
    renderNavigator();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "S01" } });
    expect((screen.getByLabelText("拖动镜头 S01") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/筛选\/搜索时暂停排序/)).toBeTruthy();
  });

  it("preserves query params when clicking another shot card and avoids re-navigating active shot", () => {
    renderNavigator("/projects/p1/episodes/e1/studio/s1?batch=s1%2Cs2&batchIndex=0");
    const s2Link = screen.getByRole("link", { name: /S02/i });
    expect(s2Link.getAttribute("href")).toBe("/projects/p1/episodes/e1/studio/s2?batch=s1%2Cs2&batchIndex=0");

    const s1Link = screen.getByRole("link", { name: /S01/i });
    expect(s1Link.getAttribute("aria-current")).toBe("true");
    const clickEvent = new MouseEvent("click", { cancelable: true, bubbles: true });
    s1Link.dispatchEvent(clickEvent);
    expect(clickEvent.defaultPrevented).toBe(true);
  });
});
