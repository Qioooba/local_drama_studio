import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { adoptShotWorkingVersionV2, getShotStudioV2, getStoryboardWorkspace, listEpisodeProductionShotsV2, submitShotGenerationV2 } from "../generated/api";
import { DirectorDeskPage } from "./DirectorDeskPage";

vi.mock("../generated/api", () => ({
  adoptShotWorkingVersionV2: vi.fn(),
  listEpisodeProductionShotsV2: vi.fn(),
  getStoryboardWorkspace: vi.fn(),
  getShotStudioV2: vi.fn(),
  submitShotGenerationV2: vi.fn(),
}));

vi.mock("../features/events/useProjectEventInvalidation", () => ({
  useProjectEventInvalidation: vi.fn(),
}));

const mockDeskData = (shotId = "shot-201", shotCode = "EP01_S01", index = 0) => ({
  project: { id: "proj-1", name: "测试短剧" },
  episode: { id: "ep-1", title: "第一集", code: "EP01", shot_count: 3, approved_count: 1 },
  allowed_actions: { edit_draft: true, mark_ready: true, generate: true, adopt_working_version: true, write_review_decision: false },
  review_handoff: { subject_type: null, subject_id: null, route_kind: "REVIEW", write_owner: "REVIEW_WORKSPACE" },
  read_only: true,
  shot_nav: {
    total: 3,
    selected_index: index,
    items: [
      { id: "shot-201", code: "EP01_S01", status: "READY", target_duration_ms: 3000, shot_type: "特写" },
      { id: "shot-202", code: "EP01_S02", status: "DRAFT", target_duration_ms: 2500, shot_type: "中景" },
      { id: "shot-203", code: "EP01_S03", status: "PRODUCTION_READY", target_duration_ms: 4000, shot_type: "全景" },
    ],
  },
  current_shot: {
    shot: {
      id: shotId,
      code: shotCode,
      status: "READY",
      target_duration_ms: 3000,
      shot_type: "中景",
      revision: 1,
    },
    current_revision: {
      id: "rev-1",
      revision_no: 1,
      is_frozen: false,
      fields: { title: "主角登场", summary: "主角推开大门" },
    },
    candidates: [],
    current_media: null,
    selected_variant: null,
    generation_preferences: { resolutions: [] },
    capability_options: [],
    active_jobs: [],
    blockers: [],
    source_context: { source_text: "门缓缓推开，阳光洒进来。" },
    frame_bridge: {
      previous: null,
      current_start: null,
      current_end: null,
    },
  },
});

function renderDesk(entry = "/projects/proj-1/episodes/ep-1/studio/shot-201") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/projects/:projectId/episodes/:episodeId/studio" element={<DirectorDeskPage />} />
          <Route path="/projects/:projectId/episodes/:episodeId/studio/:shotId" element={<DirectorDeskPage />} />
          <Route path="/projects/:projectId/episodes/:episodeId/plan" element={<div>EpisodePlan</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("DirectorDeskPage (PR-CUR-002)", () => {
  beforeEach(() => {
    vi.mocked(adoptShotWorkingVersionV2).mockReset().mockResolvedValue({ adoption: { id: "slot-1", shot_id: "shot-201", media_asset_id: "asset-1", media_version_id: "media-1", slot_type: "KEYFRAME", selection_type: "KEYFRAME", status: "ADOPTED", replayed: false } });
    window.localStorage.clear();
    vi.mocked(listEpisodeProductionShotsV2).mockResolvedValue({ items: mockDeskData().shot_nav.items.map((shot) => ({ shot_id: shot.id, shot_code: shot.code, overall_state: shot.status })) } as never);
    vi.mocked(getStoryboardWorkspace).mockResolvedValue({ storyboard: { items: mockDeskData().shot_nav.items.map((shot) => ({ id: shot.id, code: shot.code, target_duration_ms: shot.target_duration_ms, fields: { subject_action: "主角推开大门", dialogue: "谁在那里？" } })) } } as never);
    vi.mocked(getShotStudioV2).mockResolvedValue(mockDeskData("shot-201", "EP01_S01", 0) as never);
  });

  it("requires an explicit shot selection before loading write-capable Director tools", async () => {
    renderDesk("/projects/proj-1/episodes/ep-1/studio");

    expect(await screen.findByRole("heading", { name: "一集 · 3 个镜头" })).toBeTruthy();
    expect(getShotStudioV2).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: /EP01_S02/ }).getAttribute("href")).toBe(
      "/projects/proj-1/episodes/ep-1/studio/shot-202?focus=generate",
    );
    expect(screen.queryByRole("button", { name: "重新生成当前镜头" })).toBeNull();
  });

  it("deep-links directly to Shot 2 without falling back to Shot 1 and generates exact /generation/:shotId href", async () => {
    vi.mocked(getShotStudioV2).mockResolvedValue(mockDeskData("shot-202", "EP01_S02", 1) as never);
    renderDesk("/projects/proj-1/episodes/ep-1/studio/shot-202");

    expect(await screen.findByText(/EP01_S02 · 主角登场/)).toBeTruthy();
    expect(screen.getByText("2 / 3")).toBeTruthy();
    expect((screen.getByRole("button", { name: "拖动镜头 EP01_S02" }) as HTMLButtonElement).disabled).toBe(false);

    const generationLink = screen.getByRole("link", { name: /生成首个候选/i });
    expect(generationLink.getAttribute("href")).toBe("/projects/proj-1/episodes/ep-1/studio/shot-202?focus=generate");
  });

  it("deep-links directly to Shot 3 and preserves shotId in generation link", async () => {
    vi.mocked(getShotStudioV2).mockResolvedValue(mockDeskData("shot-203", "EP01_S03", 2) as never);
    renderDesk("/projects/proj-1/episodes/ep-1/studio/shot-203");

    expect(await screen.findByText(/EP01_S03 · 主角登场/)).toBeTruthy();
    expect(screen.getByText("3 / 3")).toBeTruthy();

    const generationLink = screen.getByRole("link", { name: /生成首个候选/i });
    expect(generationLink.getAttribute("href")).toBe("/projects/proj-1/episodes/ep-1/studio/shot-203?focus=generate");
  });

  it("opens the generation inspector from both a deep link and the empty-candidate action", async () => {
    const deepLinked = renderDesk("/projects/proj-1/episodes/ep-1/studio/shot-201?focus=generate");
    expect(await screen.findByRole("heading", { name: "根据首尾帧生成候选" })).toBeTruthy();
    expect(deepLinked.container.querySelector(".director-inspector-container")?.classList.contains("drawer-open")).toBe(true);
    expect(screen.getByRole("tab", { name: "AI 生成" }).getAttribute("aria-selected")).toBe("true");
    deepLinked.unmount();

    const fromEmptyState = renderDesk();
    expect(await screen.findByText(/EP01_S01 · 主角登场/)).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name: /生成首个候选/i }));
    expect(fromEmptyState.container.querySelector(".director-inspector-container")?.classList.contains("drawer-open")).toBe(true);
    expect(screen.getByRole("tab", { name: "AI 生成" }).getAttribute("aria-selected")).toBe("true");
  });

  it("does not revive legacy revision media after the canonical working-media slot is empty", async () => {
    const data = mockDeskData();
    Object.assign(data.current_shot.current_revision.fields, {
      media_version_id: "legacy-unavailable-media",
      media_kind: "IMAGE",
    });
    vi.mocked(getShotStudioV2).mockResolvedValue(data as never);
    renderDesk();

    expect(await screen.findByText(/EP01_S01 · 主角登场/)).toBeTruthy();
    expect(screen.queryByRole("img", { name: /当前选中媒体/ })).toBeNull();
  });

  it("restores a selected-shot batch from the URL and advances while preserving progress", async () => {
    vi.mocked(getShotStudioV2)
      .mockResolvedValueOnce(mockDeskData("shot-201", "EP01_S01", 0) as never)
      .mockResolvedValueOnce(mockDeskData("shot-203", "EP01_S03", 2) as never);
    renderDesk("/projects/proj-1/episodes/ep-1/studio/shot-201?batch=shot-201%2Cshot-203&batchIndex=0");
    expect(await screen.findByText("1 / 2")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "标记本镜已处理" }));
    expect(await screen.findByText("1 镜已标记处理")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "下一项" }));
    expect(await screen.findByText(/EP01_S03 · 主角登场/)).toBeTruthy();
    expect(screen.getByText("2 / 2")).toBeTruthy();
    expect(screen.getByText("1 镜已标记处理")).toBeTruthy();
    expect((screen.getByRole("button", { name: "已到最后一项" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("toggles responsive Shot Navigator drawer and Inspector drawer via buttons", async () => {
    const { container } = renderDesk();

    expect(await screen.findByText(/EP01_S01 · 主角登场/)).toBeTruthy();

    const navContainer = container.querySelector(".director-shot-nav-container");
    const inspectorContainer = container.querySelector(".director-inspector-container");

    expect(navContainer?.classList.contains("drawer-open")).toBe(false);
    expect(inspectorContainer?.classList.contains("drawer-open")).toBe(false);

    // Click nav toggle
    const navBtn = screen.getByRole("button", { name: /切换镜头列表/i });
    fireEvent.click(navBtn);
    expect(navContainer?.classList.contains("drawer-open")).toBe(true);

    // Click inspector toggle
    const inspectorBtn = screen.getByRole("button", { name: /切换检查器/i });
    fireEvent.click(inspectorBtn);
    expect(inspectorContainer?.classList.contains("drawer-open")).toBe(true);
    expect(navContainer?.classList.contains("drawer-open")).toBe(false);

    fireEvent.click(navBtn);
    expect(navContainer?.classList.contains("drawer-open")).toBe(true);
    expect(inspectorContainer?.classList.contains("drawer-open")).toBe(false);

    expect(screen.getByText("AI 自动衔接")).toBeTruthy();
  });

  it("supports keyboard shortcuts N, I, Escape and AI draw shortcut G", async () => {
    const { container } = renderDesk();

    expect(await screen.findByText(/EP01_S01 · 主角登场/)).toBeTruthy();

    const navContainer = container.querySelector(".director-shot-nav-container");
    const inspectorContainer = container.querySelector(".director-inspector-container");

    // Press 'n' to open nav drawer
    fireEvent.keyDown(window, { key: "n" });
    expect(navContainer?.classList.contains("drawer-open")).toBe(true);

    // Press 'Escape' to close nav drawer
    fireEvent.keyDown(window, { key: "Escape" });
    expect(navContainer?.classList.contains("drawer-open")).toBe(false);

    // Press 'i' to open inspector drawer
    fireEvent.keyDown(window, { key: "i" });
    expect(inspectorContainer?.classList.contains("drawer-open")).toBe(true);

    // Press 'Escape' to close inspector drawer
    fireEvent.keyDown(window, { key: "Escape" });
    expect(inspectorContainer?.classList.contains("drawer-open")).toBe(false);

    // Press 'G' to switch to generate tab and open inspector drawer
    fireEvent.keyDown(window, { key: "g" });
    expect(inspectorContainer?.classList.contains("drawer-open")).toBe(true);
    expect(screen.getByRole("tab", { name: "AI 生成" }).getAttribute("aria-selected")).toBe("true");
  });

  it("lets a nested shared dialog consume Escape without also closing the Inspector", async () => {
    const { container } = renderDesk();
    expect(await screen.findByText(/EP01_S01 · 主角登场/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /切换检查器/i }));
    const inspectorContainer = container.querySelector(".director-inspector-container");
    expect(inspectorContainer?.classList.contains("drawer-open")).toBe(true);
    const dialog = document.createElement("section");
    dialog.setAttribute("role", "dialog");
    const dialogButton = document.createElement("button");
    dialog.appendChild(dialogButton);
    document.body.appendChild(dialog);
    fireEvent.keyDown(dialogButton, { key: "Escape" });
    expect(inspectorContainer?.classList.contains("drawer-open")).toBe(true);
    dialog.remove();
  });

  it("requires and submits a different explicit seed when rerolling an explicit-seed Variant", async () => {
    const data = mockDeskData();
    data.current_shot.candidates = [{
      id: "variant-1", intent_id: "intent-1", variant_no: 1, variant_type: "BASE", parent_variant_id: null,
      branch_reason: "UI_BASE_GENERATION", seed_policy: "EXPLICIT", explicit_seed: 42, status: "SUCCEEDED",
      is_stale: false, stale_reason: null, media_asset_id: "asset-1", media_kind: "VIDEO",
      media_version_id: "media-1", version_no: 1, take_no: 1, stage: "FORMAL", rel_path: "video.mp4",
      mime_type: "video/mp4", duration_ms: 5000, integrity_status: "VERIFIED", selected: true,
      approved: false, created_at: "2026-08-22T00:00:00Z",
    }] as never;
    vi.mocked(getShotStudioV2).mockResolvedValue(data as never);
    vi.mocked(submitShotGenerationV2).mockResolvedValue({ operation: "REROLL", variant: { id: "variant-2", intent_id: "intent-1", status: "PLANNED", variant_no: 2 }, job: { id: "job-2", state: "QUEUED" }, reroll: { retry: false, parent_variant_id: "variant-1" } });
    renderDesk();
    await screen.findByText(/Take 1/);
    fireEvent.click(screen.getByRole("button", { name: "重新生成当前镜头" }));
    await waitFor(() => expect(submitShotGenerationV2).toHaveBeenCalled());
    const generatedSeed = vi.mocked(submitShotGenerationV2).mock.calls[0][1].explicit_seed;
    expect(Number.isInteger(generatedSeed)).toBe(true);
    expect(generatedSeed).not.toBe(42);
    expect(submitShotGenerationV2).toHaveBeenCalledWith("shot-201", expect.objectContaining({ parent_variant_id: "variant-1", reason_code: "USER_REROLL", explicit_seed: generatedSeed }));
  });

  it("switches shots smoothly without unmounting desk or flashing full-page loading indicator", async () => {
    let resolveShot2!: (data: unknown) => void;
    const shot2Promise = new Promise((resolve) => {
      resolveShot2 = resolve;
    });

    vi.mocked(getShotStudioV2).mockImplementation((_epId, shotId) => {
      if (shotId === "shot-202") return shot2Promise as never;
      return Promise.resolve(mockDeskData("shot-201", "EP01_S01", 0) as never);
    });

    renderDesk("/projects/proj-1/episodes/ep-1/studio/shot-201");
    expect(await screen.findByText(/EP01_S01 · 主角登场/)).toBeTruthy();

    // Click on Shot 2 in ShotNavigator
    const shot2Card = screen.getByRole("link", { name: /EP01_S02/i });
    fireEvent.click(shot2Card);

    // Verify the desk does NOT unmount or show the full-screen loading indicator
    expect(screen.queryByText("正在打开导演台…")).toBeNull();
    // Navigator and stage remain mounted
    expect(screen.getByRole("complementary", { name: "镜头导航" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "当前镜头媒体舞台" })).toBeTruthy();

    // Resolve Shot 2
    resolveShot2(mockDeskData("shot-202", "EP01_S02", 1));
    expect(await screen.findByText(/EP01_S02 · 主角登场/)).toBeTruthy();
  });
});
