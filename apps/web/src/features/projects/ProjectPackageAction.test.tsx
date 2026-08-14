import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { commitStagedProjectPackage, dryRunProjectPackage, exportProjectPackage, stageProjectPackage } from "../../generated/api";
import { ProjectPackageAction } from "./ProjectPackageAction";

vi.mock("../../generated/api", () => ({ exportProjectPackage: vi.fn(), dryRunProjectPackage: vi.fn(), stageProjectPackage: vi.fn(), commitStagedProjectPackage: vi.fn() }));

describe("ProjectPackageAction", () => {
  it("dry-runs only the exact registered path returned by export", async () => {
    vi.mocked(exportProjectPackage).mockResolvedValue({ package: { status: "EXPORTED", project_id: "p", rel_path: "exports/project-packages/p.ldspkg", byte_size: 100, sha256: "a".repeat(64), entry_count: 3, expanded_bytes: 200, reused: false, database_mutated: false, runtime_contacted: false, network_contacted: false } });
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
    vi.mocked(commitStagedProjectPackage).mockResolvedValue({ commit: { status: "IMPORTED", identity_mode: "IMPORT_AS_COPY_REWRITE_IDENTITY", project_id: "new", project_code: "new_code", stage_token: "b".repeat(64), staged_package_retained: true, runtime_contacted: false, network_contacted: false } });
    const onImported = vi.fn();
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ProjectPackageAction projectId="p" onImported={onImported} /></QueryClientProvider>);
    fireEvent.change(screen.getByLabelText("Inbox 文件名"), { target: { value: "incoming.ldspkg" } });
    fireEvent.click(screen.getByRole("button", { name: "暂存并预检" }));
    expect(await screen.findByText(/IDENTITY_CONFLICT/)).toBeTruthy();
    const commitButton = screen.getByRole("button", { name: "确认提交项目包" });
    expect((commitButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("新项目 code"), { target: { value: "new_code" } });
    fireEvent.change(screen.getByLabelText("新项目标题"), { target: { value: "项目副本" } });
    fireEvent.click(commitButton);
    await waitFor(() => expect(commitStagedProjectPackage).toHaveBeenCalledWith("b".repeat(64), { identity_mode: "IMPORT_AS_COPY_REWRITE_IDENTITY", code: "new_code", title: "项目副本" }));
    expect(onImported).toHaveBeenCalledWith("new");
  });
});
