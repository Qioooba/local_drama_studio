import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { listCapabilityOptions, type CapabilityOption, type CapabilityOptions } from "../../generated/api";
import { getModelPlatformBusinessSelectionFacadeEvaluation, getModelPlatformBusinessSelectionShadow } from "../model-platform-v2/api";
import { CapabilityPicker, useCapabilityOptions } from "./CapabilityPicker";

vi.mock("../../generated/api", () => ({
  listCapabilityOptions: vi.fn(),
  getProfileVersion: vi.fn(),
}));

vi.mock("../model-platform-v2/api", () => ({
  getModelPlatformBusinessSelectionFacadeEvaluation: vi.fn(),
  getModelPlatformBusinessSelectionShadow: vi.fn(),
}));

const readyOption: CapabilityOption = {
  profile_version_id: "deepseek-profile-v1",
  profile: { id: "deepseek-profile", code: "deepseek", title: "DeepSeek 故事拆解", version_no: 1, status: "PUBLISHED" },
  model: { name: "deepseek-r1:14b", provider: "OLLAMA_LOOPBACK" },
  runtime: { id: "ollama", title: "本机 Ollama", status: "AVAILABLE", transport: "LOOPBACK_HTTP" },
  workflow: { id: null, title: null, status: null },
  selectable: true,
  availability: "READY",
  blockers: [],
  warnings: [],
  execution_fingerprint: "sha256:ready",
};

const candidateOption: CapabilityOption = {
  ...readyOption,
  profile_version_id: "qwen-profile-v1",
  profile: { id: "qwen-profile", code: "qwen", title: "Qwen 故事拆解", version_no: 1, status: "CANDIDATE_UNVERIFIED" },
  model: { name: "qwen3.8:27b", provider: "OLLAMA_LOOPBACK" },
  selectable: false,
  availability: "BLOCKED",
  blockers: [{ code: "PROFILE_NOT_PUBLISHED", message: "该执行配置尚未发布到全局能力目录" }],
  execution_fingerprint: "sha256:candidate",
};

const response: CapabilityOptions = {
  capability: "LLM_STORY_PARSE",
  scope: { project_id: "project-1", episode_id: null, shot_id: null },
  selection: { mode: "AUTO", source: "AUTO", profile_version_id: readyOption.profile_version_id, ready: true, option: readyOption, blockers: [] },
  options: [readyOption, candidateOption],
  configured_runtime: {
    provider: "OLLAMA_LOOPBACK",
    base_url: "http://127.0.0.1:11434",
    model: "qwen3.8:27b",
    publication_status: "CANDIDATE",
    matching_profile_version_id: candidateOption.profile_version_id,
    message: "当前默认运行模型已配置，但尚未发布为这项能力",
  },
  summary: { total_count: 2, selectable_count: 1, blocked_count: 1 },
  repair_href: "/system/capabilities?view=resources",
  read_only: true,
  runtime_contacted: false,
  network_contacted: false,
  mutated: false,
};

function Harness({ onChange = vi.fn() }: { onChange?: (value: string) => void }) {
  const query = useCapabilityOptions("LLM_STORY_PARSE", { projectId: "project-1" });
  return <CapabilityPicker capability="LLM_STORY_PARSE" label="拆解模型" value="" onChange={onChange} query={query} migrationBusinessSurface="story" />;
}

function mount(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<MemoryRouter><QueryClientProvider client={client}>{ui}</QueryClientProvider></MemoryRouter>);
}

beforeEach(() => {
  vi.mocked(listCapabilityOptions).mockReset().mockResolvedValue(response);
  vi.mocked(getModelPlatformBusinessSelectionShadow).mockReset().mockResolvedValue({
    comparison: {
      capability_code: "LLM_STORY_PARSE",
      scope: { project_id: "project-1", episode_id: null, shot_id: null, character_id: null },
      legacy_resolution: { execution_profile_version_id: "legacy-v1", source: "PROJECT", resolution_mode: "EXPLICIT", blocked_reason: null, ready: true },
      legacy_project_binding: { execution_profile_version_id: "legacy-v1", binding_status: "ACTIVE", profile_status: "PUBLISHED" },
      v2_resolution: { execution_profile_version_id: "v2-v1", resolution_reason: "EXPLICIT_ASSIGNMENT", assignment_chain: [], blocked_reason: null, ready: true },
      profile_version_crosswalk: { id: "crosswalk-1", legacy_execution_profile_version_id: "legacy-v1", v2_execution_profile_version_id: "v2-v1", capability_code: "LLM_STORY_PARSE", status: "APPROVED", approval_reason: "已审阅", approved_by: "operator", approved_at: "2026-08-29T00:00:00Z" },
      parameter_contract_comparison: { status: "SHAPE_MATCH", matches: true, legacy_field_count: 1, v2_field_count: 1, common_fields: ["max_length"], legacy_only_fields: [], v2_only_fields: [], differences: { type: [], required: [], scope: [], constraint: [], default: [] }, unsafe_field_names: [], values_exposed: false },
      comparison: { status: "MAPPED_EQUIVALENT", comparable: true, reason: "已批准映射，仍须验证参数预览。", profile_version_crosswalk_available: true },
    },
    read_only: true,
    mutated: false,
  });
  vi.mocked(getModelPlatformBusinessSelectionFacadeEvaluation).mockReset().mockResolvedValue({
    evaluation: {
      business_surface: "story", capability_code: "LLM_STORY_PARSE", scope_type: "PROJECT",
      legacy_execution_profile_version_id: "legacy-v1", v2_execution_profile_version_id: "v2-v1",
      rollout_state: "CUTOVER_APPROVED", decision: "CUTOVER_CANDIDATE", blockers: [],
      execution_owner: "LEGACY_V1", execution_switched: false,
    }, read_only: true, execution_switched: false,
  });
});

it("shows the effective published route and explains a configured-but-unpublished model", async () => {
  mount(<Harness />);

  await screen.findByRole("option", { name: /自动：deepseek-r1:14b/ });
  const select = screen.getByLabelText("拆解模型");
  expect(select).toHaveValue("");
  expect(screen.getByRole("option", { name: /自动：deepseek-r1:14b/ })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: /qwen3.8:27b.*尚未发布/ })).toBeDisabled();
  expect(screen.getByText("已配置的默认模型还不能用于这项能力")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "验证并发布" })).toHaveAttribute("href", "/system/capabilities?view=resources");
  expect(screen.getByText(/自动解析：deepseek-r1:14b/)).toBeInTheDocument();
});

it("reports an explicit selectable Profile without changing the server-owned option list", async () => {
  const onChange = vi.fn();
  mount(<Harness onChange={onChange} />);

  await screen.findByRole("option", { name: /deepseek-r1:14b.*DeepSeek 故事拆解/ });
  fireEvent.change(screen.getByLabelText("拆解模型"), { target: { value: readyOption.profile_version_id } });
  expect(onChange).toHaveBeenCalledWith(readyOption.profile_version_id);
});

it("opens V2 migration audit only on demand and keeps the legacy selector unchanged", async () => {
  mount(<Harness />);

  await screen.findByRole("option", { name: /自动：deepseek-r1:14b/ });
  fireEvent.click(screen.getByText("V2 迁移双读对账（只读，不改变本次提交）"));

  expect(await screen.findByText("已批准映射：双读可比较，当前页面仍使用旧链提交。")).toBeInTheDocument();
  expect(screen.getByText(/参数合同：字段、约束和有效默认值已一致/)).toBeInTheDocument();
  expect(getModelPlatformBusinessSelectionShadow).toHaveBeenCalledWith("LLM_STORY_PARSE", { projectId: "project-1", episodeId: undefined, shotId: undefined, characterId: undefined });
  expect(getModelPlatformBusinessSelectionFacadeEvaluation).toHaveBeenCalledWith("story", "LLM_STORY_PARSE", { projectId: "project-1", episodeId: undefined, shotId: undefined, characterId: undefined });
  expect(screen.getByText("Facade：可进入切换候选，但当前提交仍固定走旧链")).toBeInTheDocument();
  expect(screen.getByLabelText("拆解模型")).toHaveValue("");
});
