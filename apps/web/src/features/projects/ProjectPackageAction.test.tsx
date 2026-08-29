import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { commitStagedProjectPackage, dryRunProjectPackage, exportProjectPackage, rebuildProjectThumbnails, requestJson, stageProjectPackage } from "../../generated/api";
import { ProjectPackageAction } from "./ProjectPackageAction";

vi.mock("../../generated/api", () => ({ exportProjectPackage: vi.fn(), dryRunProjectPackage: vi.fn(), stageProjectPackage: vi.fn(), commitStagedProjectPackage: vi.fn(), rebuildProjectThumbnails: vi.fn(), requestJson: vi.fn() }));

describe("ProjectPackageAction", () => {
  beforeEach(() => vi.mocked(requestJson).mockResolvedValue({ items: [] }));
  it("dry-runs only the exact registered path returned by export", async () => {
    vi.mocked(exportProjectPackage).mockResolvedValue({ package: { status: "EXPORTED", project_id: "p", artifact: { scope: "PROJECT", kind: "FILE", display_name: "p.ldspkg", server_absolute_path: "F:\\DramaProjects\\p\\exports\\project-packages\\p.ldspkg", rel_path: "exports/project-packages/p.ldspkg", download_url: "/api/v1/projects/p/packages:download?rel_path=exports%2Fproject-packages%2Fp.ldspkg", download_filename: "p.ldspkg" }, rel_path: "exports/project-packages/p.ldspkg", byte_size: 100, sha256: "a".repeat(64), entry_count: 3, expanded_bytes: 200, reused: false, database_mutated: false, runtime_contacted: false, network_contacted: false } });
    vi.mocked(dryRunProjectPackage).mockResolvedValue({ dry_run: { status: "READY_REBIND_EXISTING", project_id: "p", project_code: "p", entry_count: 3, expanded_bytes: 200, free_bytes: 1000, blockers: ["PROJECT_IDENTITY_CONFLICT"], conflict_options: ["REBIND_EXISTING", "IMPORT_AS_COPY_REWRITE_IDENTITY"], would_import: false, mutated: false, runtime_contacted: false, network_contacted: false } });
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ProjectPackageAction projectId="p" /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: "导出并 dry-run" }));
    await waitFor(() => expect(dryRunProjectPackage).toHaveBeenCalledWith("p", "exports/project-packages/p.ldspkg"));
    expect(await screen.findByText(/READY_REBIND_EXISTING/)).toBeTruthy();
    expect(screen.getByText(/SHA aaaaaaaaaaaa/)).toBeTruthy();
  });

  it("stages from the fixed inbox and requires an explicit copy identity before commit", async () => {
    vi.mocked(stageProjectPackage).mockResolvedValue({ staging: { status: "STAGED", stage_token: "b".repeat(64), source_name: "incoming.ldspkg", byte_size: 100, sha256: "b".repeat(64), reused: false, source_retained: true, dry_run: { status: "IDENTITY_CONFLICT", project_id: "old", project_code: "old", entry_count: 3, expanded_bytes: 200, free_bytes: 1000, blockers: ["PROJECT_IDENTITY_CONFLICT"], conflict_options: ["REBIND_EXISTING", "IMPORT_AS_COPY_REWRITE_IDENTITY"], would_import: false, mutated: false, runtime_contacted: false, network_contacted: false }, database_mutated: false, runtime_contacted: false, network_contacted: false } });
    vi.mocked(requestJson).mockResolvedValue({ items: [{ name: "incoming.ldspkg", byte_size: 100, modified_at: "now" }] });
    vi.mocked(commitStagedProjectPackage).mockResolvedValue({ commit: { status: "IMPORTED", identity_mode: "IMPORT_AS_COPY_REWRITE_IDENTITY", project_id: "new", project_code: "new_code", stage_token: "b".repeat(64), staged_package_retained: true, runtime_contacted: false, network_contacted: false } });
    const onImported = vi.fn();
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ProjectPackageAction projectId="p" onImported={onImported} /></QueryClientProvider>);
    fireEvent.change(await screen.findByRole("combobox", { name: /待导入项目包/ }), { target: { value: "incoming.ldspkg" } });
    const stageButton = screen.getByRole("button", { name: "暂存并预检" }) as HTMLButtonElement;
    await waitFor(() => expect(stageButton.disabled).toBe(false));
    fireEvent.click(stageButton);
    expect(await screen.findByText(/IDENTITY_CONFLICT/)).toBeTruthy();
    const commitButton = screen.getByRole("button", { name: "确认提交项目包" });
    expect((commitButton as HTMLButtonElement).disabled).toBe(false);
    fireEvent.change(screen.getByLabelText("新项目标题"), { target: { value: "项目副本" } });
    fireEvent.click(commitButton);
    await waitFor(() => expect(commitStagedProjectPackage).toHaveBeenCalledWith("b".repeat(64), { identity_mode: "IMPORT_AS_COPY_REWRITE_IDENTITY", code: expect.stringMatching(/^old_copy_/), title: "项目副本" }));
    expect(onImported).toHaveBeenCalledWith("new");
  });

  it("shows the exact failed thumbnail version and retry guidance", async () => {
    vi.mocked(rebuildProjectThumbnails).mockResolvedValue({ rebuild: { project_id: "p", requested: 2, created: 1, failed: 1, pending: 0, skipped: 0, created_media_version_ids: ["ok"], failures: [{ media_version_id: "bad-media-version", code: "THUMBNAIL_SOURCE_UNSUPPORTED" }], exclusions: [], runtime_contacted: false, network_contacted: false, mutated: true } });
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ProjectPackageAction projectId="p" /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: "重建项目缩略图" }));
    expect(await screen.findByText(/1\/2 成功，1 失败/)).toBeTruthy();
    expect(screen.getByText(/bad-media-ve/)).toBeTruthy();
    expect(screen.getByText(/THUMBNAIL_SOURCE_UNSUPPORTED/)).toBeTruthy();
    expect(screen.getByText(/可再次点击/)).toBeTruthy();
  });

  it("separates historical type mismatches from retryable thumbnail failures", async () => {
    vi.mocked(rebuildProjectThumbnails).mockResolvedValue({ rebuild: { project_id: "p", requested: 1, created: 1, failed: 0, pending: 0, skipped: 1, created_media_version_ids: ["ok"], failures: [], exclusions: [{ media_version_id: "legacy-json-video", code: "MEDIA_KIND_MIME_MISMATCH" }], runtime_contacted: false, network_contacted: false, mutated: true } });
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ProjectPackageAction projectId="p" /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: "重建项目缩略图" }));
    expect(await screen.findByText(/跳过 1 条历史类型不一致记录/)).toBeTruthy();
    expect(screen.getByText(/legacy-json-/)).toBeTruthy();
    expect(screen.getByText(/MEDIA_KIND_MIME_MISMATCH/)).toBeTruthy();
  });
});
