import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  getLocalLLMStatus,
  getLocalLLMProbeResult,
  probeLocalLLM,
  publishLocalLLMProfile,
  syncLocalLLMProfile,
  submitLocalLLMProbe,
} from "../story-workspace-v2/breakdownClient";
import { LocalLLMConfigurationPanel } from "./LocalLLMConfigurationPanel";

vi.mock("../story-workspace-v2/breakdownClient", () => ({
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
        status: "PUBLISHED",
      },
    });
  });

  it("uses an unregistered remote runtime as editable custom configuration", async () => {
    renderWithClient(<LocalLLMConfigurationPanel />);
    expect(screen.getByText("大语言模型（LLM）与服务连接管理")).toBeInTheDocument();
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
    expect(await screen.findByText(/http:\/\/127\.0\.0\.1:11434 · deepseek-r1:14b/)).toBeInTheDocument();
    expect((screen.getByLabelText("预设模板 (Preset)") as HTMLSelectElement).value).toBe("ollama-local");
    expect(screen.queryByLabelText("Provider 协议")).not.toBeInTheDocument();
    expect(screen.queryByText(/安全警示：数据将离开本机/)).not.toBeInTheDocument();
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
    expect(await screen.findByText(/http:\/\/127\.0\.0\.1:11434 · qwen3:8b/)).toBeInTheDocument();
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
    fireEvent.click(await screen.findByRole("button", { name: /3. 发布正式 Profile/ }));
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
    renderWithClient(<LocalLLMConfigurationPanel />);
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
    const publishBtn = screen.getByRole("button", { name: /3. 发布正式 Profile/ });
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

    expect(await screen.findByText(/已正式发布（PUBLISHED）/)).toBeInTheDocument();
    expect(publishBtn).toBeDisabled();
  });

  it("invalidates probe and candidate evidence when the provider configuration changes", async () => {
    vi.mocked(getLocalLLMStatus).mockResolvedValueOnce({
      status: { status: "PASS", provider: "OLLAMA_LOOPBACK", base_url: "http://127.0.0.1:11434", model: "deepseek-r1:14b", has_api_key: false },
    });
    renderWithClient(<LocalLLMConfigurationPanel />);
    expect(await screen.findByText(/http:\/\/127\.0\.0\.1:11434 · deepseek-r1:14b/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /1. 测试 4 级连接/ }));
    expect(await screen.findByText(/4 级测试连接全部通过/)).toBeInTheDocument();
    expect(document.querySelectorAll(".probe-level-list .probe-badge.pass")).toHaveLength(4);

    fireEvent.change(screen.getByLabelText("预设模板 (Preset)"), { target: { value: "deepseek-chat" } });
    expect(screen.queryByText(/4 级测试连接全部通过/)).not.toBeInTheDocument();
    expect(document.querySelectorAll(".probe-level-list .probe-badge.pass")).toHaveLength(0);
    expect(document.querySelectorAll(".probe-level-list .probe-badge.pending")).toHaveLength(4);
    expect(screen.getByRole("button", { name: /3. 发布正式 Profile/ })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("预设模板 (Preset)"), { target: { value: "ollama-local" } });
    expect(screen.queryByLabelText("Provider 协议")).not.toBeInTheDocument();
    expect(screen.getByText(/http:\/\/127\.0\.0\.1:11434 · deepseek-r1:14b/)).toBeInTheDocument();
  });
});
