import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  discoverOllamaModels,
  getLocalLLMStatus,
  getLocalLLMProbeResult,
  probeLocalLLM,
  publishLocalLLMProfile,
  syncLocalLLMProfile,
  submitLocalLLMProbe,
} from "../story-workspace-v2/breakdownClient";
import { LocalLLMConfigurationPanel } from "./LocalLLMConfigurationPanel";

vi.mock("../story-workspace-v2/breakdownClient", () => ({
  discoverOllamaModels: vi.fn(),
  getLocalLLMStatus: vi.fn(),
  getLocalLLMProbeResult: vi.fn(),
  probeLocalLLM: vi.fn(),
  syncLocalLLMProfile: vi.fn(),
  submitLocalLLMProbe: vi.fn(),
  publishLocalLLMProfile: vi.fn(),
}));

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("LocalLLMConfigurationPanel", () => {
  beforeEach(() => {
    vi.mocked(discoverOllamaModels).mockReset().mockResolvedValue({
      catalog: {
        provider: "OLLAMA_LOOPBACK",
        base_url: "http://127.0.0.1:11434",
        count: 4,
        scanned_at: "2026-08-29T00:00:00+08:00",
        read_only: true,
        runtime_contacted: true,
        mutated: false,
        items: [
          { name: "deepseek-r1:14b", model: "deepseek-r1:14b", modified_at: null, size_bytes: 9_000_000_000, digest: "a", format: "gguf", family: "qwen2", families: ["qwen2"], parameter_size: "14.8B", quantization_level: "Q4_K_M" },
          { name: "llava:7b", model: "llava:7b", modified_at: null, size_bytes: 4_500_000_000, digest: "b", format: "gguf", family: "llava", families: ["llava"], parameter_size: "7B", quantization_level: "Q4_0" },
          { name: "nomic-embed-text:latest", model: "nomic-embed-text:latest", modified_at: null, size_bytes: 280_000_000, digest: "c", format: "gguf", family: "nomic-bert", families: ["nomic-bert"], parameter_size: "137M", quantization_level: "F16" },
          { name: "gemma2:9b", model: "gemma2:9b", modified_at: null, size_bytes: 5_400_000_000, digest: "d", format: "gguf", family: "gemma2", families: ["gemma2"], parameter_size: "9B", quantization_level: "Q4_K_M" },
        ],
      },
    });
    vi.mocked(getLocalLLMStatus).mockReset().mockResolvedValue({
      status: {
        status: "PASS",
        provider: "OPENAI_COMPAT",
        base_url: "https://api.deepseek.com",
        model: "deepseek-v4-flash-vision-exp",
        has_api_key: true,
        masked_api_key: "••••••••1234",
      },
    });
    vi.mocked(probeLocalLLM).mockReset().mockResolvedValue({
      probe: {
        status: "PASS",
        provider: "OPENAI_COMPAT",
        base_url: "https://api.deepseek.com",
        model: "deepseek-v4-flash-vision-exp",
        probe_level_passed: 4,
        probe_levels: {
          level_1_network: { passed: true, name: "网络可达", duration_ms: 25 },
          level_2_auth: { passed: true, name: "认证成功", duration_ms: 30 },
          level_3_model: { passed: true, name: "模型可用", duration_ms: 15 },
          level_4_inference: { passed: true, name: "样例推理成功", duration_ms: 120 },
        },
      },
    });
    vi.mocked(submitLocalLLMProbe).mockReset().mockResolvedValue({
      job: { id: "probe-job-1", type: "LOCAL_LLM_PROBE", project_id: "project-1", state: "QUEUED", channel: "CPU", priority: 15, max_attempts: 1, revision: 1 },
    });
    vi.mocked(getLocalLLMProbeResult).mockReset().mockResolvedValue({
      job: { id: "probe-job-1", type: "LOCAL_LLM_PROBE", project_id: "project-1", state: "SUCCEEDED", channel: "CPU", priority: 15, max_attempts: 1, revision: 2 },
      probe: { status: "PASS", probe_level_passed: 4, probe_levels: {} },
    });
    vi.mocked(syncLocalLLMProfile).mockReset().mockResolvedValue({
      profile: {
        profile_version_id: "deepseek-prof-v1",
        profile_code: "llm-openai-deepseek-v4-flash-vision-exp-llm-story-parse",
        status: "CANDIDATE_UNVERIFIED",
      },
    });
    vi.mocked(publishLocalLLMProfile).mockReset().mockResolvedValue({
      profile: {
        profile_version_id: "deepseek-prof-v1",
        profile_code: "llm-openai-deepseek-v4-flash-vision-exp-llm-story-parse",
        version_no: 1,
        status: "PUBLISHED",
        publication: {
          destination: "GLOBAL_CAPABILITY_CATALOG",
          scope: "LOCAL_STUDIO",
          consumer_scope: "ALL_PROJECTS",
          capability: "LLM_STORY_PARSE",
          model: "deepseek-v4-flash-vision-exp",
          provider: "OPENAI_COMPAT",
          published_at: "2026-08-29T00:33:29+08:00",
        },
      },
    });
  });

  it("uses an unregistered remote runtime as editable custom configuration", async () => {
    renderWithClient(<LocalLLMConfigurationPanel />);
    expect(screen.getByText("智能理解模型配置")).toBeInTheDocument();
    expect(screen.getByLabelText("预设模板 (Preset)")).toBeInTheDocument();
    await waitFor(() => expect((screen.getByLabelText("预设模板 (Preset)") as HTMLSelectElement).value).toBe("custom"));
    expect(screen.getByLabelText("Base URL")).toHaveValue("https://api.deepseek.com");
    expect(screen.getByLabelText("Model 名称")).toHaveValue("deepseek-v4-flash-vision-exp");
  });

  it("starts from the registered loopback runtime instead of a remote preset", async () => {
    vi.mocked(getLocalLLMStatus).mockResolvedValueOnce({
      status: { status: "PASS", provider: "OLLAMA_LOOPBACK", base_url: "http://127.0.0.1:11434", model: "deepseek-r1:14b", has_api_key: false },
    });
    renderWithClient(<LocalLLMConfigurationPanel />);
    await waitFor(() => expect(screen.getByText("当前配置").parentElement).toHaveTextContent("deepseek-r1:14b本机 Ollama · http://127.0.0.1:11434"));
    expect((screen.getByLabelText("预设模板 (Preset)") as HTMLSelectElement).value).toBe("ollama-local");
    expect(screen.queryByLabelText("Provider 协议")).not.toBeInTheDocument();
    expect(screen.queryByText(/安全警示：数据将离开本机/)).not.toBeInTheDocument();
  });

  it("automatically lists, categorizes, searches, and selects Ollama models", async () => {
    vi.mocked(getLocalLLMStatus).mockResolvedValueOnce({
      status: { status: "CONFIGURED", provider: "OLLAMA_LOOPBACK", base_url: "http://127.0.0.1:11434", model: "gemma2:9b", has_api_key: false },
    });
    renderWithClient(<LocalLLMConfigurationPanel />);

    expect(await screen.findByRole("heading", { name: "本机 Ollama 模型" })).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector(".model-count-badge")).toHaveTextContent("4 个"));
    const categoryFilters = within(screen.getByRole("group", { name: "按模型用途筛选" }));
    expect(categoryFilters.getByRole("button", { name: /推理与策划\s*1/ })).toBeInTheDocument();
    expect(categoryFilters.getByRole("button", { name: /视觉理解\s*1/ })).toBeInTheDocument();
    expect(categoryFilters.getByRole("button", { name: /通用文本\s*1/ })).toBeInTheDocument();
    expect(categoryFilters.getByRole("button", { name: /向量模型\s*1/ })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("搜索模型"), { target: { value: "llava" } });
    const modelList = within(screen.getByRole("list", { name: "Ollama 模型列表" }));
    expect(modelList.getByText("llava:7b")).toBeInTheDocument();
    expect(modelList.queryByText("gemma2:9b")).not.toBeInTheDocument();

    fireEvent.click(modelList.getByText("llava:7b"));
    expect(screen.getByText("已选择")).toBeInTheDocument();
    expect(screen.getByText("当前配置").parentElement).toHaveTextContent("llava:7b本机 Ollama · http://127.0.0.1:11434");
  });

  it("shows a recoverable Ollama connection error without hiding existing configuration", async () => {
    vi.mocked(discoverOllamaModels).mockRejectedValueOnce(new Error("LOCAL_LLM_LOOPBACK_UNAVAILABLE"));
    renderWithClient(<LocalLLMConfigurationPanel />);
    expect(await screen.findByText("暂时无法读取本机 Ollama")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试连接" })).toBeInTheDocument();
    expect(await screen.findByLabelText("Model 名称")).toHaveValue("deepseek-v4-flash-vision-exp");
  });

  it("shows red outbound warning for remote endpoints and requires checkbox confirmation", async () => {
    renderWithClient(<LocalLLMConfigurationPanel />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/安全警示：数据将离开本机/)).toBeInTheDocument();

    const probeBtn = screen.getByRole("button", { name: /1. 测试 4 级连接/ });
    fireEvent.click(probeBtn);

    // Blocked before checkbox
    expect(await screen.findByText(/数据将离开本机：测试远程 LLM 连接必须勾选确认出境安全许可/)).toBeInTheDocument();
    expect(probeLocalLLM).not.toHaveBeenCalled();

    // Check allow outbound checkbox
    const checkbox = screen.getByLabelText(/我已知晓并允许数据离开本机出境调用/);
    fireEvent.click(checkbox);
    fireEvent.click(probeBtn);

    await waitFor(() => {
      expect(probeLocalLLM).toHaveBeenCalledWith(
        expect.objectContaining({
          provider: "OPENAI_COMPAT",
          base_url: "https://api.deepseek.com",
          model: "deepseek-v4-flash-vision-exp",
          allow_remote_outbound: true,
        })
      );
    });

    expect(await screen.findByText(/4 级测试连接全部通过/)).toBeInTheDocument();
  });

  it("uses a durable probe job when project scope exists and no inline secret is present", async () => {
    vi.mocked(getLocalLLMStatus).mockResolvedValueOnce({
      status: { status: "PASS", provider: "OLLAMA_LOOPBACK", base_url: "http://127.0.0.1:11434", model: "qwen3:8b", has_api_key: false },
    });
    renderWithClient(<LocalLLMConfigurationPanel projectId="project-1" />);
    await waitFor(() => expect(screen.getByText("当前配置").parentElement).toHaveTextContent("qwen3:8b本机 Ollama · http://127.0.0.1:11434"));
    const button = await screen.findByRole("button", { name: /1. 测试 4 级连接/ });
    fireEvent.click(button);
    await waitFor(() => expect(submitLocalLLMProbe).toHaveBeenCalledWith("project-1", expect.objectContaining({
      provider: "OLLAMA_LOOPBACK", model: "qwen3:8b", load_test: true,
    })));
    expect(probeLocalLLM).not.toHaveBeenCalled();
    expect(await screen.findByText(/后台 4 级连接测试全部通过/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "打开作业详情" }).getAttribute("href")).toContain("probe-job-1");

    fireEvent.click(screen.getByRole("button", { name: /2. 同步候选 Profile/ }));
    await waitFor(() => expect(syncLocalLLMProfile).toHaveBeenCalledWith(expect.objectContaining({
      probe_job_id: "probe-job-1",
    })));
    fireEvent.click(await screen.findByRole("button", { name: /3. 发布到全局能力目录/ }));
    await waitFor(() => expect(publishLocalLLMProfile).toHaveBeenCalledWith(expect.objectContaining({
      probeJobId: "probe-job-1",
    })));
  });

  it("verifies and securely persists an inline DeepSeek key in project scope", async () => {
    renderWithClient(<LocalLLMConfigurationPanel projectId="project-1" />);
    expect(await screen.findByText(/deepseek-v4-flash-vision-exp/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: "sk-one-shot-secret" } });
    expect(screen.getByLabelText(/四级验证通过后安全保存到当前 Windows 用户/)).toBeChecked();
    fireEvent.click(screen.getByLabelText(/我已知晓并允许数据离开本机出境调用/));
    fireEvent.click(screen.getByRole("button", { name: /1. 测试 4 级连接/ }));
    expect(await screen.findByText(/密钥已安全保存到 Windows 凭据管理器/)).toBeInTheDocument();
    expect(submitLocalLLMProbe).not.toHaveBeenCalled();
    expect(probeLocalLLM).toHaveBeenCalledWith(expect.objectContaining({
      api_key: "sk-one-shot-secret",
      remember_api_key: true,
      allow_remote_outbound: true,
    }));
  });

  it("completes sync and publish cycle", async () => {
    const onPublished = vi.fn();
    renderWithClient(<LocalLLMConfigurationPanel onPublished={onPublished} />);
    const checkbox = await screen.findByLabelText(/我已知晓并允许数据离开本机出境调用/);
    fireEvent.click(checkbox);

    fireEvent.click(screen.getByRole("button", { name: /1. 测试 4 级连接/ }));
    expect(await screen.findByText(/4 级测试连接全部通过/)).toBeInTheDocument();

    // Sync candidate
    const syncBtn = screen.getByRole("button", { name: /2. 同步候选 Profile/ });
    fireEvent.click(syncBtn);

    await waitFor(() => {
      expect(syncLocalLLMProfile).toHaveBeenCalledWith(
        expect.objectContaining({
          capability: "LLM_STORY_PARSE",
          allow_remote_outbound: true,
        })
      );
    });

    expect(await screen.findByText(/候选 Profile 已生成/)).toBeInTheDocument();

    // Publish
    const publishBtn = screen.getByRole("button", { name: /3. 发布到全局能力目录/ });
    expect(publishBtn).not.toBeDisabled();
    fireEvent.click(publishBtn);

    await waitFor(() => {
      expect(publishLocalLLMProfile).toHaveBeenCalledWith(
        expect.objectContaining({
          profileVersionId: "deepseek-prof-v1",
          allowRemoteOutbound: true,
        })
      );
    });

    expect(await screen.findByText("发布完成")).toBeInTheDocument();
    expect(screen.getByText("系统 / 能力与模型 / 能力目录")).toBeInTheDocument();
    expect(screen.getByText("本机全局 · 所有项目可选")).toBeInTheDocument();
    expect(screen.getAllByText("deepseek-v4-flash-vision-exp").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "在能力目录中定位" }));
    expect(onPublished).toHaveBeenCalledWith(expect.objectContaining({
      profileVersionId: "deepseek-prof-v1",
      capability: "LLM_STORY_PARSE",
      model: "deepseek-v4-flash-vision-exp",
    }));
    expect(publishBtn).toBeDisabled();
  });

  it("invalidates probe and candidate evidence when the provider configuration changes", async () => {
    vi.mocked(getLocalLLMStatus).mockResolvedValueOnce({
      status: { status: "PASS", provider: "OLLAMA_LOOPBACK", base_url: "http://127.0.0.1:11434", model: "deepseek-r1:14b", has_api_key: false },
    });
    renderWithClient(<LocalLLMConfigurationPanel />);
    await waitFor(() => expect(screen.getByText("当前配置").parentElement).toHaveTextContent("deepseek-r1:14b本机 Ollama · http://127.0.0.1:11434"));

    fireEvent.click(screen.getByRole("button", { name: /1. 测试 4 级连接/ }));
    expect(await screen.findByText(/4 级测试连接全部通过/)).toBeInTheDocument();
    expect(document.querySelectorAll(".probe-level-list .probe-badge.pass")).toHaveLength(4);

    fireEvent.change(screen.getByLabelText("预设模板 (Preset)"), { target: { value: "deepseek-chat" } });
    expect(screen.queryByText(/4 级测试连接全部通过/)).not.toBeInTheDocument();
    expect(document.querySelectorAll(".probe-level-list .probe-badge.pass")).toHaveLength(0);
    expect(document.querySelectorAll(".probe-level-list .probe-badge.pending")).toHaveLength(4);
    expect(screen.getByRole("button", { name: /3. 发布到全局能力目录/ })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("预设模板 (Preset)"), { target: { value: "ollama-local" } });
    expect(screen.queryByLabelText("Provider 协议")).not.toBeInTheDocument();
    expect(screen.getByText("当前配置").parentElement).toHaveTextContent("deepseek-r1:14b本机 Ollama · http://127.0.0.1:11434");
  });
});
