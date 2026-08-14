import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { dryRunProjectPackage, exportProjectPackage } from "../../generated/api";
import { ProjectPackageAction } from "./ProjectPackageAction";

vi.mock("../../generated/api", () => ({ exportProjectPackage: vi.fn(), dryRunProjectPackage: vi.fn() }));

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
});
