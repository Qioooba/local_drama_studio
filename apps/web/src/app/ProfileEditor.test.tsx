import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ModelsPage } from "../pages/ModelsPage";
import type { Profile, ProfileVersionDetail, WorkflowVersionSummary } from "../generated/api";

vi.mock("../features/preferences-v2/GenerationPreferencePanel", () => ({ GenerationPreferencePanel: () => null }));

const published: Profile = {
  id: "prof-1",
  code: "h3-native-i2v",
  title: "H3 I2V",
  version_id: "v-published",
  capability: "I2V",
  status: "PUBLISHED",
  version_no: 4,
};
const draft: Profile = {
  id: "prof-1",
  code: "h3-native-i2v",
  title: "H3 I2V",
  version_id: "v-draft",
  capability: "I2V",
  status: "DRAFT",
  version_no: 5,
};

const publishedDetail: ProfileVersionDetail = {
  id: "v-published",
  execution_profile_id: "prof-1",
  code: "h3-native-i2v",
  title: "H3 I2V",
  version_no: 4,
  capability: "I2V",
  status: "PUBLISHED",
  revision: 1,
  input_contract: { transport: "LOOPBACK_HTTP", input_slots: { FIRST_FRAME: { min: 1, max: 1 } } },
  parameter_schema: { seed: { determinism: "profile_declared", required: true } },
  output_contract: {},
  resource_policy: {},
  contract_hash: "53cf79d5816712a7dc06945326f778dbf75564b26a62ed0ce686ee16609fd2b1",
  validation: null,
};

const draftDetail: ProfileVersionDetail = {
  id: "v-draft",
  execution_profile_id: "prof-1",
  code: "h3-native-i2v",
  title: "H3 I2V",
  version_no: 5,
  capability: "I2V",
  status: "DRAFT",
  revision: 1,
  input_contract: { transport: "LOOPBACK_HTTP", input_slots: { FIRST_FRAME: { min: 1, max: 1 } } },
  parameter_schema: { seed: { determinism: "profile_declared", required: true } },
  output_contract: { media_kind: "VIDEO", container: "mp4", codec: "h264" },
  resource_policy: { gpu_heavy_concurrency: 1, worker_policy: "ONE_H3_WORKER_ONE_GPU_TASK" },
  contract_hash: "53cf79d5816712a7dc06945326f778dbf75564b26a62ed0ce686ee16609fd2b1",
  validation: null,
};

let draftValidation: ProfileVersionDetail["validation"] = null;
let validationResult: { id: string; profile_version_id: string; contract_hash: string; status: "PASS" | "FAIL"; checks: Array<{ code: string; passed: boolean }>; runtime_contacted: false; network_contacted: false } | null = null;

const workflows: WorkflowVersionSummary[] = [{
  id: "wf-1",
  workflow_id: "wfp-1",
  code: "fl2va-first-frame",
  title: "FL2VA First Frame",
  version_no: 2,
  content_hash: "3a1557a4ea8ffaa4539b6522500fd8f09e0adbbb86b6f7f35a8cee6bfc9dc354",
  status: "PUBLISHED",
  contract: { capability: "H3_FL2VA_I2V_CANDIDATE" },
  package_rel_path: "workflows/fl2va.json",
  published_at: "2026-08-13T00:00:00+00:00",
  created_at: "2026-08-13T00:00:00+00:00",
  updated_at: "2026-08-13T00:00:00+00:00",
  revision: 1,
}];

vi.mock("../generated/api", () => ({
  healthLive: vi.fn().mockResolvedValue({ status: "HEALTHY", checks: { mode: "LOCAL_ONLY" } }),
  systemContract: vi.fn().mockResolvedValue({ mode: "LOCAL_ONLY", remote_provider: "disabled", legacy_migration: "deferred_to_g11" }),
  listProjects: vi.fn().mockResolvedValue({ items: [] }),
  listProfiles: vi.fn(),
  listWorkflowVersions: vi.fn(),
  getProfileVersion: vi.fn(),
  deriveProfileContractVersion: vi.fn(),
  validateProfileContractVersion: vi.fn(),
  publishProfileContractVersion: vi.fn(),
  validateWorkflowLocal: vi.fn(),
  publishWorkflowVersion: vi.fn(),
  revokeWorkflowVersion: vi.fn(),
  rollbackWorkflowVersion: vi.fn(),
  latestDiagnostics: vi.fn().mockResolvedValue({ run: null }),
  runDiagnostics: vi.fn().mockResolvedValue({ run: { status: "HEALTHY", checks: [] } }),
  listSeasons: vi.fn().mockResolvedValue({ items: [] }),
  listEpisodes: vi.fn().mockResolvedValue({ items: [] }),
  getEpisodeProduction: vi.fn().mockResolvedValue({ episode: {}, items: [] }),
  h3CandidateRuntime: vi.fn().mockResolvedValue({ runtime: { status: "BLOCKED" } }),
  reviewInbox: vi.fn().mockResolvedValue({ items: [] }),
  listReviewTemplates: vi.fn().mockResolvedValue({ items: [] }),
  getReviewContext: vi.fn(),
  submitReview: vi.fn(),
  selectMediaVersion: vi.fn(),
  listJobs: vi.fn().mockResolvedValue({ items: [] }),
  getG6Readiness: vi.fn(),
  planG6I2VProbe: vi.fn(),
  getProductionCanvas: vi.fn(),
  preflightProductionCanvasRun: vi.fn(),
  saveProductionCanvasLayout: vi.fn(),
}));

import * as api from "../generated/api";

function renderProfilesView(path = "/?view=profile-contracts") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <ModelsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Profile contract editor interactions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/?view=profile-contracts");
    draftValidation = null;
    validationResult = null;
    vi.mocked(api.listProfiles).mockResolvedValue({ items: [published, draft] });
    vi.mocked(api.listWorkflowVersions).mockResolvedValue({ items: workflows, runtime_contacted: false });
    vi.mocked(api.getProfileVersion).mockImplementation((id) =>
      Promise.resolve({ profile_version: id === "v-draft" ? { ...draftDetail, validation: draftValidation } : publishedDetail }),
    );
    vi.mocked(api.deriveProfileContractVersion).mockResolvedValue({ profile_version: draftDetail });
    vi.mocked(api.validateProfileContractVersion).mockImplementation(async () => {
      draftValidation = { id: "att-1", status: "PASS", contract_hash: "53cf79d5816712a7dc06945326f778dbf75564b26a62ed0ce686ee16609fd2b1", checks: [{ code: "OUTPUT_CONTRACT", passed: true }] };
      validationResult = { id: "att-1", profile_version_id: "v-draft", contract_hash: "53cf79d5816712a7dc06945326f778dbf75564b26a62ed0ce686ee16609fd2b1", status: "PASS", checks: [{ code: "OUTPUT_CONTRACT", passed: true }], runtime_contacted: false, network_contacted: false };
      return { validation: validationResult };
    });
    vi.mocked(api.publishProfileContractVersion).mockRejectedValue(new Error("PROFILE_REAL_EVIDENCE_REQUIRED"));
    vi.mocked(api.validateWorkflowLocal).mockResolvedValue({
      validation: { id: "wf-validation-1", workflow_version_id: "wf-1", status: "PASS", checks: [] },
    });
    vi.mocked(api.publishWorkflowVersion).mockResolvedValue({ workflow_version: workflows[0] });
    vi.mocked(api.revokeWorkflowVersion).mockResolvedValue({ workflow_version: { ...workflows[0], status: "RETIRED" } });
    vi.mocked(api.rollbackWorkflowVersion).mockResolvedValue({ workflow_version: workflows[0] });
  });

  it("derives an immutable DRAFT and does not overwrite the published source", async () => {
    renderProfilesView();
    expect(await screen.findByRole("button", { name: "保存为新 DRAFT" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "保存为新 DRAFT" }));
    await waitFor(() => {
      expect(api.deriveProfileContractVersion).toHaveBeenCalled();
    });
    const [derivedId] = vi.mocked(api.deriveProfileContractVersion).mock.calls[0] as [string, unknown];
    expect(derivedId).toBe("v-published");
    expect(await screen.findByText(/已创建不可变 DRAFT v5/)).toBeTruthy();
    expect(await screen.findByText("运行本地契约验证")).toBeTruthy();
  });

  it("keeps publish disabled until a PASS validation exists", async () => {
    renderProfilesView();
    await screen.findByRole("button", { name: "保存为新 DRAFT" });
    fireEvent.click(screen.getByRole("button", { name: "保存为新 DRAFT" }));
    await screen.findByText("运行本地契约验证");
    const publishButton = screen.getByRole("button", { name: "发布已验证版本" }) as HTMLButtonElement;
    expect(publishButton.disabled).toBe(true);
    expect(await screen.findByText(/发布保持禁用/)).toBeTruthy();
  });

  it("runs local contract validation that reports PASS and unlocks publish", async () => {
    renderProfilesView();
    await screen.findByRole("button", { name: "保存为新 DRAFT" });
    fireEvent.click(screen.getByRole("button", { name: "保存为新 DRAFT" }));
    await screen.findByText("运行本地契约验证");
    fireEvent.click(screen.getByRole("button", { name: "运行本地契约验证" }));
    await waitFor(() => expect(api.validateProfileContractVersion).toHaveBeenCalled());
    expect(await screen.findByText(/本地契约验证 PASS/)).toBeTruthy();
    const publishButton = (await screen.findByRole("button", { name: "发布已验证版本" })) as HTMLButtonElement;
    await waitFor(() => expect(publishButton.disabled).toBe(false));
  });

  it("explains the real-evidence requirement when a contract publish is refused", async () => {
    renderProfilesView();
    await screen.findByRole("button", { name: "保存为新 DRAFT" });
    fireEvent.click(screen.getByRole("button", { name: "保存为新 DRAFT" }));
    await screen.findByText("运行本地契约验证");
    fireEvent.click(screen.getByRole("button", { name: "运行本地契约验证" }));
    await screen.findByText(/本地契约验证 PASS/);
    const publishButton = await screen.findByRole("button", { name: "发布已验证版本" });
    await waitFor(() => expect((publishButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(publishButton);
    await waitFor(() => expect(api.publishProfileContractVersion).toHaveBeenCalled());
    expect(await screen.findByText(/PROFILE_REAL_EVIDENCE_REQUIRED/)).toBeTruthy();
    expect(await screen.findByText(/必须转入真实媒体证据发布/)).toBeTruthy();
  });

  it("shows a failed validation and keeps publish disabled", async () => {
    vi.mocked(api.validateProfileContractVersion).mockImplementation(async () => {
      draftValidation = { id: "att-2", status: "FAIL", contract_hash: "x".repeat(64), checks: [{ code: "OUTPUT_CONTRACT", passed: false }] };
      validationResult = { id: "att-2", profile_version_id: "v-draft", contract_hash: "x".repeat(64), status: "FAIL", checks: [{ code: "OUTPUT_CONTRACT", passed: false }], runtime_contacted: false, network_contacted: false };
      return { validation: validationResult };
    });
    renderProfilesView();
    await screen.findByRole("button", { name: "保存为新 DRAFT" });
    fireEvent.click(screen.getByRole("button", { name: "保存为新 DRAFT" }));
    await screen.findByText("运行本地契约验证");
    fireEvent.click(screen.getByRole("button", { name: "运行本地契约验证" }));
    expect(await screen.findByText(/契约验证未通过/)).toBeTruthy();
    const publishButton = screen.getByRole("button", { name: "发布已验证版本" }) as HTMLButtonElement;
    expect(publishButton.disabled).toBe(true);
  });

  it("keeps workflow publish fail-closed until the current version passes validation", async () => {
    const draftWorkflow = { ...workflows[0], status: "DRAFT", published_at: null };
    vi.mocked(api.listWorkflowVersions).mockResolvedValue({ items: [draftWorkflow], runtime_contacted: false });
    vi.mocked(api.publishWorkflowVersion).mockResolvedValue({ workflow_version: { ...draftWorkflow, status: "PUBLISHED" } });

    renderProfilesView("/?view=workflows");
    expect(await screen.findByRole("heading", { name: "工作流版本、验证与发布证据" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "发布" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "本地验证" }));
    expect(await screen.findByText(/本地工作流验证：PASS/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "发布" }));

    await waitFor(() => {
      expect(api.publishWorkflowVersion).toHaveBeenCalledWith("wf-1", "wf-validation-1");
    });
  });

  it("does not expose workflow publish after a failed validation", async () => {
    const draftWorkflow = { ...workflows[0], status: "DRAFT", published_at: null };
    vi.mocked(api.listWorkflowVersions).mockResolvedValue({ items: [draftWorkflow], runtime_contacted: false });
    vi.mocked(api.validateWorkflowLocal).mockResolvedValue({
      validation: { id: "wf-validation-fail", workflow_version_id: "wf-1", status: "FAIL", checks: [] },
    });

    renderProfilesView("/?view=workflows");
    fireEvent.click(await screen.findByRole("button", { name: "本地验证" }));
    expect(await screen.findByText(/本地工作流验证：FAIL/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "发布" })).toBeNull();
    expect(api.publishWorkflowVersion).not.toHaveBeenCalled();
  });

  it("requires a written reason before revoking a published workflow", async () => {
    renderProfilesView("/?view=workflows");
    const revokeButton = await screen.findByRole("button", { name: "撤销" });
    expect((revokeButton as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByRole("textbox", { name: "撤销原因 fl2va-first-frame v2" }), {
      target: { value: "本机图定义已被替代" },
    });
    expect((revokeButton as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(revokeButton);

    await waitFor(() => {
      expect(api.revokeWorkflowVersion).toHaveBeenCalledWith("wf-1", "本机图定义已被替代");
    });
  });
});
