import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getCapacitySnapshot, listProfiles, listProjects, listWorkflowVersions } from "../generated/api";
import { HomePage } from "./HomePage";
import { apiJsonResponse } from "../test/apiResponse";

vi.mock("../features/projects/ProjectCreateWizard", () => ({ ProjectCreateWizard: () => <button type="button">新建项目</button> }));
vi.mock("../generated/api", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("../generated/api")>();
  return {
    ...original,
    getCapacitySnapshot: vi.fn(),
    listProfiles: vi.fn(),
    listProjects: vi.fn(),
    listWorkflowVersions: vi.fn(),
  };
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><HomePage /></MemoryRouter></QueryClientProvider>);
}

describe("HomePage global workspace", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).split("/").at(-1);
      const body = path === "dependencies"
        ? { status: "HEALTHY", checks: { ffmpeg: "discovered", database: "ok", comfy_designer: "ready", production_profiles: "synced_candidates", worker_supervisor: "ready:CPU,GPU_H3" } }
        : { status: "HEALTHY", checks: { database: "ok" } };
      return apiJsonResponse(body);
    }));
    vi.mocked(listProjects).mockResolvedValue({ items: [
      { id: "p-old", code: "OLD", title: "旧项目", status: "DRAFT", revision: 1, updated_at: "2026-08-01T00:00:00Z" },
      { id: "p-new", code: "NEW", title: "最近项目", status: "ACTIVE", revision: 2, updated_at: "2026-08-28T00:00:00Z", preview_media_version_id: "media-video-1", preview_media_kind: "VIDEO", preview_has_thumbnail: true },
    ] } as never);
    vi.mocked(listProfiles).mockResolvedValue({ items: [
      { id: "pf-1", code: "image", title: "图像", version_id: "v1", capability: "IMAGE", status: "PUBLISHED" },
      { id: "pf-2", code: "video", title: "视频", version_id: "v2", capability: "VIDEO", status: "DRAFT" },
    ] } as never);
    vi.mocked(listWorkflowVersions).mockResolvedValue({ items: [
      { id: "wf-1", workflow_id: "wf", code: "base", title: "基础", version_no: 1, content_hash: "h", status: "PUBLISHED", contract: {}, package_rel_path: null, published_at: null, created_at: "", updated_at: "", revision: 1 },
    ], runtime_contacted: false } as never);
    vi.mocked(getCapacitySnapshot).mockResolvedValue({ snapshot: {
      scope: { project_id: null }, observed_at: "2026-08-28T00:00:00Z", jobs_by_state: {}, jobs_by_channel: {},
      queued_count: 3, oldest_queued_age_seconds: 2, active_attempt_count: 1, active_worker_count: 2,
      gpu_active_count: 1, gpu_concurrency_limit: 1, completed_last_24h: 8,
      disk: { free_bytes: 128 * 1024 ** 3, total_bytes: 256 * 1024 ** 3, used_bytes: 128 * 1024 ** 3, source: "本机磁盘" },
      observation_status: "OBSERVED_NOT_BENCHMARKED", webhook_status: "LOOPBACK_EXPLICIT_BOUNDED",
      would_create_jobs: false, runtime_contacted: false, network_contacted: false, mutated: false,
    } } as never);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("separates the dashboard from the project library and exposes every system owner", async () => {
    renderPage();
    expect(screen.getByRole("heading", { name: "今天从哪里继续？" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "新建项目" })).toBeTruthy();
    const recent = await screen.findByRole("region", { name: "最近项目" });
    await within(recent).findByRole("link", { name: "最近项目：打开项目首页" });
    expect([...recent.querySelectorAll(".home-project-row strong")].map((node) => node.textContent)).toEqual(["最近项目", "旧项目"]);
    expect(within(recent).getByRole("link", { name: "最近项目：打开项目首页" }).getAttribute("href")).toBe("/projects/p-new");
    const preview = within(recent).getByRole("img", { name: "最近项目 项目缩略图" });
    expect(preview.getAttribute("src")).toBe("/api/v1/media-versions/media-video-1/thumbnail?size=small&frame=poster");
    expect(within(recent).getByText("视频")).toBeTruthy();
    expect(within(recent).getByRole("img", { name: "旧项目 项目缩略图：暂无图片或视频" })).toBeTruthy();

    const system = screen.getByRole("navigation", { name: "系统配置与维护入口" });
    expect(within(system).getByRole("link", { name: /能力与模型/ }).getAttribute("href")).toBe("/system/capabilities");
    expect(within(system).getByRole("link", { name: /任务与机器/ }).getAttribute("href")).toBe("/system/jobs");
    expect(within(system).getByRole("link", { name: /诊断与审计/ }).getAttribute("href")).toBe("/system/diagnostics");
    expect(within(system).getByRole("link", { name: /工作流与环境/ }).getAttribute("href")).toBe("/system/workflows");
    expect(await screen.findAllByText("生产环境正常")).toHaveLength(2);
  });
});
