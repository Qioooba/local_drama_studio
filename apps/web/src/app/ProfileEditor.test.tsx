import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ModelsPage } from "../pages/ModelsPage";
import { SystemWorkflowsPage } from "../pages/SystemWorkflowsPage";
import type { Profile, ProfileVersionDetail, WorkflowVersionSummary } from "../generated/api";

vi.mock("../features/preferences-v2/GenerationPreferencePanel", () => ({ GenerationPreferencePanel: () => null }));
vi.mock("../features/model-config/RuntimeEnvironmentsPanel", () => ({ RuntimeEnvironmentsPanel: () => null }));
vi.mock("../features/shared/ComfyLabPanel", () => ({ ComfyLabPanel: () => null }));
vi.mock("../features/profiles/profileEvidenceClient", () => ({
  planI2VEvidenceProbe: vi.fn(),
  validateProfileEvidenceCompatibility: vi.fn(),
  submitI2VEvidenceProbe: vi.fn(),
  finalizeI2VEvidenceProbe: vi.fn(),
}));

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
  execution: {
    schema_version: "localdrama.profile-execution-detail.v1",
    runtime: null,
    workflow: null,
    components: [],
    defaults: {},
    override_schema: { fields: {
      production_tier: { type: "enum", label: "生产档位", default: "DRAFT", options: ["DRAFT", "PRODUCTION"], scopes: ["PROJECT", "SHOT", "RUN"] },
      frame_count: { type: "integer", label: "帧数", minimum: 9, maximum: 1001, step: 1, scopes: ["RUN"] },
    } },
    worker_policy: "ONE_H3_WORKER_ONE_GPU_TASK",
    model_bundle: {},
    fingerprints: { execution: "sha256:" + "a".repeat(64), model_bundle: "sha256:" + "b".repeat(64), workflow: null, manifest: null },
    read_only: true,
    local_only: true,
  },
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
  execution: publishedDetail.execution,
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
  listWorkflowDefinitions: vi.fn().mockResolvedValue({ items: [] }),
  instantiateWorkflowDefinition: vi.fn(),
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
  h3CandidateRuntime: vi.fn().mockResolvedValue({ runtime: { status: "BLOCKED" } }),
  reviewInbox: vi.fn().mockResolvedValue({ items: [] }),
  listReviewTemplates: vi.fn().mockResolvedValue({ items: [] }),
  listJobs: vi.fn().mockResolvedValue({ items: [] }),
  getG6Readiness: vi.fn(),
  planG6I2VProbe: vi.fn(),
  getJob: vi.fn(),
}));

import * as api from "../generated/api";
import * as evidence from "../features/profiles/profileEvidenceClient";

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

function renderProjectProfilesView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/projects/project-1/models?view=profile-contracts"]}>
        <Routes>
          <Route path="/projects/:projectId/models" element={<ModelsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderWorkflowsView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><SystemWorkflowsPage /></MemoryRouter></QueryClientProvider>);
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
    vi.mocked(api.getJob).mockResolvedValue({ job: { id: "job-evidence-1", state: "QUEUED" } as never });
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

  it("stores human-readable generation defaults in the derived profile contract", async () => {
    renderProfilesView();
    const tier = await screen.findByRole("combobox", { name: /生产档位/ });
    fireEvent.change(tier, { target: { value: "PRODUCTION" } });
    fireEvent.click(screen.getByRole("button", { name: "保存为新 DRAFT" }));
    await waitFor(() => expect(api.deriveProfileContractVersion).toHaveBeenCalled());
    const [, payload] = vi.mocked(api.deriveProfileContractVersion).mock.calls[0];
    expect(payload.parameter_schema).toMatchObject({ defaults: { production_tier: "PRODUCTION" } });
  });

  it("keeps publish disabled until a PASS validation exists", async () => {
    renderProfilesView();
    await screen.findByRole("button", { name: "保存为新 DRAFT" });
    fireEvent.click(screen.getByRole("button", { name: "保存为新 DRAFT" }));
    await screen.findByText("运行本地契约验证");
    const publishButton = screen.getByRole("button", { name: "发布到全局能力目录" }) as HTMLButtonElement;
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
    const publishButton = (await screen.findByRole("button", { name: "发布到全局能力目录" })) as HTMLButtonElement;
    await waitFor(() => expect(publishButton.disabled).toBe(false));
  });

  it("refreshes the selected existing DRAFT after validation without querying an empty temporary draft id", async () => {
    renderProfilesView();
    fireEvent.click(await screen.findByRole("button", { name: /h3-native-i2v I2V · v5 DRAFT/ }));
    await screen.findByText("运行本地契约验证");
    fireEvent.click(screen.getByRole("button", { name: "运行本地契约验证" }));

    expect(await screen.findByText(/本地契约验证 PASS/)).toBeTruthy();
    await waitFor(() => expect(api.getProfileVersion).toHaveBeenCalledWith("v-draft"));
    expect(vi.mocked(api.getProfileVersion).mock.calls.some(([id]) => !id)).toBe(false);
    expect(screen.queryByText(/Profile 契约读取失败/)).toBeNull();
  });

  it("does not validate or publish stale DRAFT data after the form changes", async () => {
    vi.mocked(api.getProfileVersion).mockImplementation((id) => Promise.resolve({
      profile_version: id === "v-draft"
        ? { ...draftDetail, validation: { id: "att-existing", status: "PASS", contract_hash: draftDetail.contract_hash, checks: [] } }
        : publishedDetail,
    }));
    renderProfilesView();
    fireEvent.click(await screen.findByRole("button", { name: /h3-native-i2v I2V · v5 DRAFT/ }));
    await screen.findByRole("button", { name: "保存修改为新 DRAFT" });
    fireEvent.change(screen.getByRole("combobox", { name: /^输出媒体类型/ }), { target: { value: "IMAGE" } });

    expect((screen.getByRole("button", { name: "保存修改为新 DRAFT" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "运行本地契约验证" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "发布到全局能力目录" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/当前表单有未保存修改/)).toBeTruthy();
  });

  it("explains the real-evidence requirement when a contract publish is refused", async () => {
    renderProfilesView();
    await screen.findByRole("button", { name: "保存为新 DRAFT" });
    fireEvent.click(screen.getByRole("button", { name: "保存为新 DRAFT" }));
    await screen.findByText("运行本地契约验证");
    fireEvent.click(screen.getByRole("button", { name: "运行本地契约验证" }));
    await screen.findByText(/本地契约验证 PASS/);
    const publishButton = await screen.findByRole("button", { name: "发布到全局能力目录" });
    await waitFor(() => expect((publishButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(publishButton);
    await waitFor(() => expect(api.publishProfileContractVersion).toHaveBeenCalled());
    expect(await screen.findByText(/PROFILE_REAL_EVIDENCE_REQUIRED/)).toBeTruthy();
    expect(await screen.findByText(/必须转入真实媒体证据发布/)).toBeTruthy();
  });

  it("shows the global catalog destination after a contract version is published", async () => {
    const validatedDraft = {
      ...draftDetail,
      validation: { id: "att-publish", status: "PASS" as const, contract_hash: draftDetail.contract_hash, checks: [{ code: "CONTRACT", passed: true }] },
    };
    vi.mocked(api.listProfiles).mockResolvedValue({ items: [draft] });
    vi.mocked(api.getProfileVersion).mockResolvedValue({ profile_version: validatedDraft });
    vi.mocked(api.publishProfileContractVersion).mockResolvedValue({ profile_version: { ...validatedDraft, status: "PUBLISHED" } });

    renderProfilesView();
    const publishButton = await screen.findByRole("button", { name: "发布到全局能力目录" });
    expect((publishButton as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(publishButton);

    expect(await screen.findByRole("heading", { name: "发布完成" })).toBeTruthy();
    expect(screen.getByText("系统 / 能力与模型 / 能力目录")).toBeTruthy();
    expect(screen.getByText("本机全局 · 所有项目可选")).toBeTruthy();
  });

  it("uses an accessible confirmation dialog before queuing one real I2V evidence job", async () => {
    const videoDraft = { ...draft, capability: "VIDEO_I2V" };
    const videoDraftDetail = {
      ...draftDetail,
      capability: "VIDEO_I2V",
      validation: { id: "att-video", status: "PASS" as const, contract_hash: draftDetail.contract_hash, checks: [{ code: "CONTRACT", passed: true }] },
    };
    vi.mocked(api.listProfiles).mockResolvedValue({ items: [videoDraft] });
    vi.mocked(api.getProfileVersion).mockResolvedValue({ profile_version: videoDraftDetail });
    vi.mocked(evidence.planI2VEvidenceProbe).mockResolvedValue({
      plan: {
        status: "READY",
        blockers: [],
        plan_hash: "plan-hash",
        snapshot: {
          approved_keyframe: { media_version_id: "media-first-frame" },
          workflow: { id: "workflow-v2", content_hash: "workflow-hash" },
          candidate_profile: { id: "v-draft", execution_fingerprint: "fingerprint" },
          semantic_inputs: {},
        },
        confirmation_required: true,
      },
    });
    vi.mocked(evidence.submitI2VEvidenceProbe).mockResolvedValue({
      job: { id: "job-evidence-1", state: "QUEUED" },
      plan: await vi.mocked(evidence.planI2VEvidenceProbe)("project-1", "v-draft", "wf-1").then((item) => item.plan),
    });

    renderProjectProfilesView();
    expect((await screen.findByRole("combobox", { name: "验证工作流版本" }) as HTMLSelectElement).value).toBe("wf-1");
    fireEvent.click(await screen.findByRole("button", { name: "预检真实证据探测" }));
    await waitFor(() => expect(evidence.planI2VEvidenceProbe).toHaveBeenCalledWith("project-1", "v-draft", "wf-1"));
    expect(await screen.findByText(/证据探测预检 READY/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "确认并排队单次 Job" }));
    const dialog = await screen.findByRole("dialog", { name: "确认创建真实媒体证据 Job" });
    expect(dialog).toBeTruthy();
    expect(evidence.submitI2VEvidenceProbe).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认并排队" }));
    await waitFor(() => expect(evidence.submitI2VEvidenceProbe).toHaveBeenCalledWith("project-1", "v-draft", "wf-1", "plan-hash"));
    expect(await screen.findByText(/证据 Job job-evidence/)).toBeTruthy();
    expect((screen.getByRole("combobox", { name: /验证任务（恢复）/ }) as HTMLSelectElement).value).toBe("job-evidence-1");
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
    const publishButton = screen.getByRole("button", { name: "发布到全局能力目录" }) as HTMLButtonElement;
    expect(publishButton.disabled).toBe(true);
  });

  it("keeps workflow publish fail-closed until the current version passes validation", async () => {
    const draftWorkflow = { ...workflows[0], status: "DRAFT", published_at: null };
    vi.mocked(api.listWorkflowVersions).mockResolvedValue({ items: [draftWorkflow], runtime_contacted: false });
    vi.mocked(api.publishWorkflowVersion).mockResolvedValue({ workflow_version: { ...draftWorkflow, status: "PUBLISHED" } });

    renderWorkflowsView();
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

    renderWorkflowsView();
    fireEvent.click(await screen.findByRole("button", { name: "本地验证" }));
    expect(await screen.findByText(/本地工作流验证：FAIL/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "发布" })).toBeNull();
    expect(api.publishWorkflowVersion).not.toHaveBeenCalled();
  });

  it("requires a written reason before revoking a published workflow", async () => {
    renderWorkflowsView();
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
