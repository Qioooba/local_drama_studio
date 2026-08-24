import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadDirectorDesk, rerollDirectorCandidate } from "../features/director-v2/DirectorDeskClient";
import { createKeyframeCandidate, getEpisodeProduction, getShotContinuityContext } from "../generated/api";
import { DirectorDeskPage } from "./DirectorDeskPage";

vi.mock("../features/director-v2/DirectorDeskClient", () => ({
  loadDirectorDesk: vi.fn(),
  approveFormalCandidate: vi.fn(),
  rerollDirectorCandidate: vi.fn(),
  selectDirectorCandidate: vi.fn(),
}));

vi.mock("../generated/api", () => ({
  createKeyframeCandidate: vi.fn(),
  getEpisodeProduction: vi.fn(),
  getShotContinuityContext: vi.fn(),
  listProfiles: vi.fn().mockResolvedValue({ items: [] }),
}));

vi.mock("../features/media-picker/MediaPicker", () => ({
  MediaPicker: ({ onChange }: { onChange: (id: string) => void }) => <button type="button" onClick={() => onChange("source-image-1")}>选择测试项目图片</button>,
}));

vi.mock("../features/events/useProjectEventInvalidation", () => ({
  useProjectEventInvalidation: vi.fn(),
}));

const mockDeskData = (shotId = "shot-201", shotCode = "EP01_S01", index = 0) => ({
  project: { id: "proj-1", name: "测试短剧" },
  episode: { id: "ep-1", title: "第一集", code: "EP01", shot_count: 3, approved_count: 1 },
  permissions: { can_edit: true, can_generate: true, can_approve: true },
  // The Director Desk endpoint is a read-only projection; write authority is
  // expressed independently by permissions.can_edit.
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

function renderDesk(entry = "/projects/proj-1/episodes/ep-1/direct/shot-201") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/projects/:projectId/episodes/:episodeId/direct" element={<DirectorDeskPage />} />
          <Route path="/projects/:projectId/episodes/:episodeId/direct/:shotId" element={<DirectorDeskPage />} />
          <Route path="/projects/:projectId/episodes/:episodeId/generation/:shotId" element={<div data-testid="generation-route">GenerationTarget</div>} />
          <Route path="/projects/:projectId/episodes/:episodeId/plan" element={<div>EpisodePlan</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("DirectorDeskPage (PR-CUR-002)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.mocked(getShotContinuityContext).mockResolvedValue({ continuity: {} } as never);
    vi.mocked(getEpisodeProduction).mockResolvedValue({ items: mockDeskData().shot_nav.items } as never);
    vi.mocked(loadDirectorDesk).mockResolvedValue(mockDeskData("shot-201", "EP01_S01", 0) as never);
  });

  it("requires an explicit shot selection before loading write-capable Director tools", async () => {
    renderDesk("/projects/proj-1/episodes/ep-1/direct");

    expect(await screen.findByRole("heading", { name: "先选择要精修的镜头" })).toBeTruthy();
    expect(loadDirectorDesk).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: /EP01_S02/ }).getAttribute("href")).toBe(
      "/projects/proj-1/episodes/ep-1/direct/shot-202",
    );
    expect(screen.queryByRole("button", { name: "重抽当前镜头" })).toBeNull();
  });

  it("deep-links directly to Shot 2 without falling back to Shot 1 and generates exact /generation/:shotId href", async () => {
    vi.mocked(loadDirectorDesk).mockResolvedValue(mockDeskData("shot-202", "EP01_S02", 1) as never);
    renderDesk("/projects/proj-1/episodes/ep-1/direct/shot-202");

    expect(await screen.findByText(/EP01_S02 · 主角登场/)).toBeTruthy();
    expect(screen.getByText("2 / 3")).toBeTruthy();
    expect((screen.getByRole("button", { name: "拖动镜头 EP01_S02" }) as HTMLButtonElement).disabled).toBe(false);

    const generationLink = screen.getByRole("link", { name: /生成首个候选/i });
    expect(generationLink.getAttribute("href")).toBe("/projects/proj-1/episodes/ep-1/generation/shot-202");
  });

  it("deep-links directly to Shot 3 and preserves shotId in generation link", async () => {
    vi.mocked(loadDirectorDesk).mockResolvedValue(mockDeskData("shot-203", "EP01_S03", 2) as never);
    renderDesk("/projects/proj-1/episodes/ep-1/direct/shot-203");

    expect(await screen.findByText(/EP01_S03 · 主角登场/)).toBeTruthy();
    expect(screen.getByText("3 / 3")).toBeTruthy();

    const generationLink = screen.getByRole("link", { name: /生成首个候选/i });
    expect(generationLink.getAttribute("href")).toBe("/projects/proj-1/episodes/ep-1/generation/shot-203");
  });

  it("creates a shot keyframe candidate from an explicitly selected project image without auto-approval", async () => {
    vi.mocked(createKeyframeCandidate).mockResolvedValue({ media: { id: "keyframe-4", duplicate: false } } as never);
    renderDesk();
    expect(await screen.findByText(/EP01_S01 · 主角登场/)).toBeTruthy();
    fireEvent.keyDown(window, { key: "g" });
    fireEvent.click(screen.getByRole("button", { name: "从项目图片创建关键帧候选" }));
    expect(screen.getByRole("dialog", { name: "为 EP01_S01 创建关键帧候选" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "选择测试项目图片" }));
    fireEvent.click(screen.getByRole("button", { name: "确认创建候选" }));
    await waitFor(() => expect(createKeyframeCandidate).toHaveBeenCalledWith("source-image-1", "shot-201"));
    expect(await screen.findByText("已创建本镜关键帧候选；仍需显式选择并在审核页批准。")).toBeTruthy();
  });

  it("restores a selected-shot batch from the URL and advances while preserving progress", async () => {
    vi.mocked(loadDirectorDesk)
      .mockResolvedValueOnce(mockDeskData("shot-201", "EP01_S01", 0) as never)
      .mockResolvedValueOnce(mockDeskData("shot-203", "EP01_S03", 2) as never);
    renderDesk("/projects/proj-1/episodes/ep-1/direct/shot-201?batch=shot-201%2Cshot-203&batchIndex=0");
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

    fireEvent.click(screen.getByRole("button", { name: "管理来源与锁定" }));
    expect(navContainer?.classList.contains("drawer-open")).toBe(false);
    expect(inspectorContainer?.classList.contains("drawer-open")).toBe(true);
    expect(inspectorContainer?.classList.contains("drawer-open")).toBe(true);
    expect(screen.getByRole("tab", { name: "连贯性" }).getAttribute("aria-selected")).toBe("true");
  });

  it("supports keyboard shortcuts N, I, Escape and tab shortcuts G, F, A, Enter", async () => {
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
    expect(screen.getByRole("tab", { name: "生成" }).getAttribute("aria-selected")).toBe("true");

    // Press 'F' to switch to continuity tab
    fireEvent.keyDown(window, { key: "f" });
    expect(screen.getByRole("tab", { name: "连贯性" }).getAttribute("aria-selected")).toBe("true");

    // Press 'A' to switch to assets tab
    fireEvent.keyDown(window, { key: "a" });
    expect(screen.getByRole("tab", { name: "角色场景" }).getAttribute("aria-selected")).toBe("true");

    // Press 'Enter' to switch to picture tab
    fireEvent.keyDown(window, { key: "Enter" });
    expect(screen.getByRole("tab", { name: "画面" }).getAttribute("aria-selected")).toBe("true");
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
    vi.mocked(loadDirectorDesk).mockResolvedValue(data as never);
    vi.mocked(rerollDirectorCandidate).mockResolvedValue({ variant: { id: "variant-2", status: "PLANNED", variant_no: 2 }, job: { id: "job-2", state: "QUEUED" }, reroll: { retry: false, parent_variant_id: "variant-1" } });
    renderDesk();
    await screen.findByText(/Take 1/);
    fireEvent.click(screen.getByRole("button", { name: "重抽当前镜头" }));
    expect(screen.queryByLabelText("新 Seed")).toBeNull();
    expect(screen.getByText(/自动生成不同的可复现随机值/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "构图不对" }));
    fireEvent.click(screen.getByRole("button", { name: "创建并排队" }));
    await waitFor(() => expect(rerollDirectorCandidate).toHaveBeenCalled());
    const generatedSeed = vi.mocked(rerollDirectorCandidate).mock.calls[0][3];
    expect(Number.isInteger(generatedSeed)).toBe(true);
    expect(generatedSeed).not.toBe(42);
    expect(rerollDirectorCandidate).toHaveBeenCalledWith("variant-1", "COMPOSITION_FIX", undefined, generatedSeed, undefined);
  });

  it("creates a model branch with the effective VIDEO_I2V Profile and preserves the parent seed", async () => {
    const data = mockDeskData();
    data.current_shot.candidates = [{
      id: "variant-1", intent_id: "intent-1", variant_no: 1, variant_type: "BASE", parent_variant_id: null,
      branch_reason: "UI_BASE_GENERATION", seed_policy: "EXPLICIT", explicit_seed: 42,
      capability_profile_version_id: "profile-v13", status: "SUCCEEDED",
      is_stale: false, stale_reason: null, media_asset_id: "asset-1", media_kind: "VIDEO",
      media_version_id: "media-1", version_no: 1, take_no: 1, stage: "FORMAL", rel_path: "video.mp4",
      mime_type: "video/mp4", duration_ms: 5000, integrity_status: "VERIFIED", selected: true,
      approved: false, created_at: "2026-08-22T00:00:00Z",
    }] as never;
    data.current_shot.generation_preferences = {
      resolutions: [{
        capability: "VIDEO_I2V",
        profile_version_id: "profile-v18",
        source: "SHOT",
        profile: { code: "h3-native-i2v", title: "H3 Native I2V", version_no: 18 },
      }],
    } as never;
    vi.mocked(loadDirectorDesk).mockResolvedValue(data as never);
    vi.mocked(rerollDirectorCandidate).mockResolvedValue({ variant: { id: "variant-2", status: "PLANNED", variant_no: 2 }, job: { id: "job-2", state: "QUEUED" }, reroll: { retry: false, parent_variant_id: "variant-1" } });
    renderDesk();
    await screen.findByText(/Take 1/);
    fireEvent.click(screen.getByRole("button", { name: "重抽当前镜头" }));

    expect((screen.getByRole("radio", { name: /改用当前推荐能力 v18/ }) as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByLabelText("新 Seed")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "创建并排队" }));

    await waitFor(() => expect(rerollDirectorCandidate).toHaveBeenCalledWith(
      "variant-1", "IDENTITY_FIX", undefined, undefined, "profile-v18",
    ));
  });
});
