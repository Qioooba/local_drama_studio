import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  getLocalLLMStatus,
  getLocalLLMProbeResult,
  probeLocalLLM,
  publishLocalLLMProfile,
  syncLocalLLMProfile,
  submitLocalLLMProbe,
  type LocalLLMProbeLevel,
  type LocalLLMStatus,
} from "../story-workspace-v2/breakdownClient";
import { listProviderConnections } from "../../generated/api";
import { routes } from "../../app/routeRegistry";
import "./profile-configuration.css";
import "./local-llm-configuration.css";

interface PresetOption {
  id: string;
  name: string;
  provider: "OPENAI_COMPAT" | "OLLAMA_LOOPBACK";
  baseUrl: string;
  model: string;
  description: string;
  connectionId?: string;
}

const BASE_PRESETS: PresetOption[] = [
  {
    id: "ollama-local",
    name: "Ollama 当前服务端模型（离线）",
    provider: "OLLAMA_LOOPBACK",
    baseUrl: "http://127.0.0.1:11434",
    model: "",
    description: "优先读取 Windows 服务端登记的 Ollama 模型；不会连接公网。",
  },
  {
    id: "custom",
    name: "自定义模型服务",
    provider: "OPENAI_COMPAT",
    baseUrl: "",
    model: "",
    description: "手动指定任意兼容 OpenAI 协议或 Ollama 的地址与模型。",
  },
];

const CAPABILITIES = [
  { id: "LLM_STORY_PARSE", name: "小说 / 剧本智能拆解", desc: "长文结构化为场次、角色与镜头草稿" },
  { id: "QC_VISUAL", name: "图像 / 视频视觉评审", desc: "画面清晰度、构图与镜头伪影质检" },
  { id: "QC_FACE", name: "人脸保真度质检", desc: "五官畸变与面部一致性质检" },
  { id: "QC_IDENTITY", name: "角色一致性质检", desc: "服装、发型与特征跨镜头一致性" },
];

function isRemoteEndpoint(url: string): boolean {
  try {
    const parsed = new URL(url);
    return !["127.0.0.1", "localhost", "::1"].includes(parsed.hostname.toLowerCase());
  } catch {
    return false;
  }
}

export function LocalLLMConfigurationPanel({ onChanged, projectId }: { onChanged?: () => void; projectId?: string }) {
  const [selectedPreset, setSelectedPreset] = useState<string>("ollama-local");
  const [provider, setProvider] = useState<"OPENAI_COMPAT" | "OLLAMA_LOOPBACK">("OLLAMA_LOOPBACK");
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:11434");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [rememberApiKey, setRememberApiKey] = useState(true);
  const [capability, setCapability] = useState("LLM_STORY_PARSE");
  const [allowRemoteOutbound, setAllowRemoteOutbound] = useState(false);

  const [probing, setProbing] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [publishing, setPublishing] = useState(false);

  const [probeResult, setProbeResult] = useState<LocalLLMStatus | null>(null);
  const [probeJobId, setProbeJobId] = useState<string | null>(null);
  const [lastSyncedProfileId, setLastSyncedProfileId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ kind: "success" | "error"; message: string } | null>(null);

  const statusQuery = useQuery({
    queryKey: ["local-llm-runtime-status"],
    queryFn: () => getLocalLLMStatus({ live_probe: false }),
  });
  const providerConnections = useQuery({ queryKey: ["provider-connections", "local-llm"], queryFn: () => listProviderConnections() });
  const presets = useMemo<PresetOption[]>(() => {
    const connections = (providerConnections.data?.items ?? []).map((connection) => ({
      id: `connection:${connection.id}`,
      name: `${connection.title}${connection.status === "ACTIVE" ? "（已启用）" : ""}`,
      provider: (connection.protocol.toUpperCase().includes("OLLAMA") ? "OLLAMA_LOOPBACK" : "OPENAI_COMPAT") as PresetOption["provider"],
      baseUrl: connection.base_url,
      model: connection.model ?? "",
      description: `来自 Provider 连接中心 · ${connection.provider_kind} · revision ${connection.revision}`,
      connectionId: connection.id,
    }));
    return [BASE_PRESETS[0], ...connections, BASE_PRESETS.at(-1)!];
  }, [providerConnections.data?.items]);
  const initializedFromRuntime = useRef(false);

  useEffect(() => {
    const runtime = statusQuery.data?.status;
    if (initializedFromRuntime.current || !runtime?.provider || !runtime.base_url) return;
    const runtimeProvider = runtime.provider === "OLLAMA_LOOPBACK" ? "OLLAMA_LOOPBACK" : "OPENAI_COMPAT";
    const runtimeModel = runtime.model ?? "";
    const exactPreset = presets.find((preset) => preset.id !== "ollama-local" && preset.id !== "custom"
      && preset.provider === runtimeProvider && preset.baseUrl === runtime.base_url && preset.model === runtimeModel);
    setProvider(runtimeProvider);
    setBaseUrl(runtime.base_url);
    setModel(runtimeModel);
    setSelectedPreset(exactPreset?.id ?? (runtimeProvider === "OLLAMA_LOOPBACK" ? "ollama-local" : "custom"));
    initializedFromRuntime.current = true;
  }, [presets, statusQuery.data]);

  const isRemote = isRemoteEndpoint(baseUrl);
  const providerConnectionId = presets.find((preset) => preset.id === selectedPreset)?.connectionId;

  const invalidateVerification = () => {
    setProbeResult(null);
    setProbeJobId(null);
    setLastSyncedProfileId(null);
    setFeedback(null);
  };

  const applyPreset = (presetId: string) => {
    invalidateVerification();
    setSelectedPreset(presetId);
    const preset = presets.find((p) => p.id === presetId);
    if (preset?.id === "ollama-local") {
      const runtime = statusQuery.data?.status;
      setProvider("OLLAMA_LOOPBACK");
      setBaseUrl(runtime?.provider === "OLLAMA_LOOPBACK" && runtime.base_url ? runtime.base_url : preset.baseUrl);
      setModel(runtime?.provider === "OLLAMA_LOOPBACK" ? runtime.model ?? "" : "");
      return;
    }
    if (preset && preset.id !== "custom") {
      setProvider(preset.provider);
      setBaseUrl(preset.baseUrl);
      setModel(preset.model);
    }
  };

  const handleTestConnection = async () => {
    if (isRemote && !allowRemoteOutbound) {
      setFeedback({ kind: "error", message: "数据将离开本机：测试远程 LLM 连接必须勾选确认出境安全许可。" });
      return;
    }
    if (projectId && apiKey.trim() && !rememberApiKey) {
      setFeedback({
        kind: "error",
        message: "项目内连接测试必须可离页恢复；请勾选安全保存到 Windows 凭据管理器，或清空此输入后使用已保存凭据。",
      });
      return;
    }
    setProbing(true);
    setFeedback(null);
    try {
      if (projectId && !apiKey.trim()) {
        const submission = await submitLocalLLMProbe(projectId, {
          provider,
          base_url: baseUrl.trim(),
          model: model.trim(),
          load_test: true,
          allow_remote_outbound: allowRemoteOutbound,
          provider_connection_id: providerConnectionId,
        });
        setProbeJobId(submission.job.id);
        setFeedback({ kind: "success", message: "4 级连接测试已提交到作业中心；离开本页也不会丢失结果。" });
        return;
      }
      const resp = await probeLocalLLM({
        provider,
        base_url: baseUrl.trim(),
        model: model.trim(),
        api_key: apiKey.trim() || undefined,
        remember_api_key: Boolean(apiKey.trim() && rememberApiKey),
        load_test: true,
        allow_remote_outbound: allowRemoteOutbound,
        provider_connection_id: providerConnectionId,
      });
      setProbeResult(resp.probe);
      if (resp.probe.status === "PASS" && resp.probe.probe_level_passed === 4) {
        if (apiKey.trim() && rememberApiKey) {
          setApiKey("");
          await statusQuery.refetch();
        }
        setFeedback({
          kind: "success",
          message: apiKey.trim() && rememberApiKey
            ? "4 级测试全部通过，DeepSeek 密钥已安全保存到 Windows 凭据管理器；可立即同步并发布 Profile。"
            : "4 级测试连接全部通过！可立即同步并发布 Profile。",
        });
      } else {
        setFeedback({
          kind: "error",
          message: `测试未完全通过（通过级别: ${resp.probe.probe_level_passed ?? 0}/4）。${resp.probe.message || resp.probe.error_code || "请检查配置。"}`,
        });
      }
    } catch (error) {
      setFeedback({ kind: "error", message: `测试连接异常：${String(error)}` });
    } finally {
      setProbing(false);
    }
  };

  const durableProbe = useQuery({
    queryKey: ["local-llm-probe", probeJobId],
    queryFn: () => getLocalLLMProbeResult(probeJobId!),
    enabled: Boolean(probeJobId),
    refetchInterval: (query) => {
      const state = (query.state.data as { job?: { state?: string } } | undefined)?.job?.state;
      return state && ["SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_ATTENTION"].includes(state) ? false : 1_500;
    },
  });

  useEffect(() => {
    if (!durableProbe.data) return;
    if (durableProbe.data.probe) {
      setProbeResult(durableProbe.data.probe);
      setFeedback(durableProbe.data.probe.status === "PASS" && durableProbe.data.probe.probe_level_passed === 4
        ? { kind: "success", message: "后台 4 级连接测试全部通过！可同步并发布 Profile。" }
        : { kind: "error", message: `后台测试未完全通过（${durableProbe.data.probe.probe_level_passed ?? 0}/4）。` });
    } else if (["FAILED", "CANCELLED", "NEEDS_ATTENTION"].includes(durableProbe.data.job.state)) {
      setFeedback({ kind: "error", message: `后台连接测试未完成：${durableProbe.data.job.last_error_detail_redacted ?? durableProbe.data.job.last_error_code ?? durableProbe.data.job.state}` });
    }
  }, [durableProbe.data]);
  const durableProbeState = durableProbe.data?.job.state;
  const durableProbeActive = Boolean(probeJobId && (!durableProbeState || !["SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_ATTENTION"].includes(durableProbeState)));

  const handleSyncCandidate = async () => {
    if (isRemote && !allowRemoteOutbound) {
      setFeedback({ kind: "error", message: "数据将离开本机：同步远程 LLM Profile 必须勾选确认出境安全许可。" });
      return;
    }
    setSyncing(true);
    setFeedback(null);
    try {
      const resp = await syncLocalLLMProfile({
        provider,
        base_url: baseUrl.trim(),
        model: model.trim(),
        api_key: apiKey.trim() || undefined,
        capability,
        allow_remote_outbound: allowRemoteOutbound,
        probe_job_id: probeJobId ?? undefined,
        provider_connection_id: providerConnectionId,
      });
      setLastSyncedProfileId(resp.profile.profile_version_id);
      setFeedback({
        kind: "success",
        message: `候选 Profile 已生成（ID: ${resp.profile.profile_version_id.slice(0, 12)}…）。请点击“发布正式 Profile”激活使用。`,
      });
      onChanged?.();
    } catch (error) {
      setFeedback({ kind: "error", message: `同步候选失败：${String(error)}` });
    } finally {
      setSyncing(false);
    }
  };

  const handlePublish = async () => {
    if (!lastSyncedProfileId) {
      setFeedback({ kind: "error", message: "请先同步生成候选 Profile 再发布。" });
      return;
    }
    if (isRemote && !allowRemoteOutbound) {
      setFeedback({ kind: "error", message: "数据将离开本机：发布远程 LLM Profile 必须勾选确认出境安全许可。" });
      return;
    }
    setPublishing(true);
    setFeedback(null);
    try {
      const resp = await publishLocalLLMProfile({
        profileVersionId: lastSyncedProfileId,
        apiKey: apiKey.trim() || undefined,
        allowRemoteOutbound,
        probeJobId: probeJobId ?? undefined,
      });
      setFeedback({
        kind: "success",
        message: `Profile ${resp.profile.profile_version_id.slice(0, 12)}… 已正式发布（PUBLISHED），已在剧本拆解与质检中可用！`,
      });
      setLastSyncedProfileId(null);
      await statusQuery.refetch();
      onChanged?.();
    } catch (error) {
      setFeedback({ kind: "error", message: `发布失败：${String(error)}` });
    } finally {
      setPublishing(false);
    }
  };

  const renderLevelBadge = (level?: LocalLLMProbeLevel) => {
    if (!level) {
      return <span className="probe-badge pending">待测试</span>;
    }
    if (level.passed) {
      return (
        <span className="probe-badge pass">
          通过 {level.duration_ms ? `(${level.duration_ms}ms)` : ""}
        </span>
      );
    }
    return (
      <span className="probe-badge fail">
        失败 {level.message ? `: ${level.message}` : ""}
      </span>
    );
  };

  const probePassed = probeResult?.status === "PASS" && probeResult.probe_level_passed === 4;

  return (
    <section className="local-llm-configuration-panel" aria-labelledby="llm-config-title">
      <div className="section-title">
        <span id="llm-config-title">智能理解模型配置</span>
        <small>故事与策划 · 提示词 · 视觉质检 · OpenAI 兼容 · Ollama</small>
      </div>

      <div className="llm-config-layout">
        {/* Left column: Form controls */}
        <div className="llm-form-container">
          <div className="form-group">
            <label htmlFor="llm-preset-select">
              <strong>预设模板 (Preset)</strong>
            </label>
            <select
              id="llm-preset-select"
              value={selectedPreset}
              onChange={(e) => applyPreset(e.target.value)}
            >
              {presets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.name}
                </option>
              ))}
            </select>
            <small className="muted">
              {presets.find((p) => p.id === selectedPreset)?.description}
            </small>
          </div>

          {selectedPreset !== "custom" && <div className="llm-preset-facts"><span>连接方式</span><strong>{provider === "OLLAMA_LOOPBACK" ? "Windows 服务端 Ollama" : "OpenAI 兼容远端服务"}</strong><small>{baseUrl} · {model || "由服务自动选择模型"}</small></div>}

          {selectedPreset === "custom" && <div className="form-group">
            <label htmlFor="llm-provider-select">
              <strong>服务连接协议</strong>
            </label>
            <select
              id="llm-provider-select"
              value={provider}
              onChange={(e) => { invalidateVerification(); setSelectedPreset("custom"); setProvider(e.target.value as "OPENAI_COMPAT" | "OLLAMA_LOOPBACK"); }}
            >
              <option value="OPENAI_COMPAT">OpenAI 兼容接口（含 DeepSeek 等远程服务）</option>
              <option value="OLLAMA_LOOPBACK">Windows 服务端 Ollama（回环访问）</option>
            </select>
          </div>}

          <div className="form-group">
            <label htmlFor="llm-capability-select">
              <strong>发布为哪种能力</strong>
            </label>
            <select
              id="llm-capability-select"
              value={capability}
              onChange={(e) => { invalidateVerification(); setCapability(e.target.value); }}
            >
              {CAPABILITIES.map((cap) => (
                <option key={cap.id} value={cap.id}>
                  {cap.name}
                </option>
              ))}
            </select>
          </div>

          {selectedPreset === "custom" && <div className="form-group">
            <label htmlFor="llm-base-url-input">
              <strong>Base URL</strong>
            </label>
            <input
              id="llm-base-url-input"
              type="text"
              value={baseUrl}
              onChange={(e) => { invalidateVerification(); setSelectedPreset("custom"); setBaseUrl(e.target.value); }}
              placeholder="https://api.deepseek.com"
            />
          </div>}

          {selectedPreset === "custom" && <div className="form-group">
            <label htmlFor="llm-model-input">
              <strong>Model 名称</strong>
            </label>
            <input
              id="llm-model-input"
              type="text"
              value={model}
              onChange={(e) => { invalidateVerification(); setSelectedPreset("custom"); setModel(e.target.value); }}
              placeholder="deepseek-v4-flash-vision-exp"
              list="local-llm-discovered-models"
            />
            <datalist id="local-llm-discovered-models">{statusQuery.data?.status.models?.map((item) => <option value={item} key={item} />)}</datalist>
            <small className="muted">优先从当前运行服务发现的模型中选择；也可填写服务实际支持的模型标识。</small>
          </div>}

          {isRemote && <div className="form-group">
            <label htmlFor="llm-api-key-input">
              <strong>API Key（脱敏保密，不写入项目、任务或日志）</strong>
            </label>
            <input
              id="llm-api-key-input"
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => { invalidateVerification(); setApiKey(e.target.value); }}
              placeholder={statusQuery.data?.status.has_api_key ? `已配置环境变量 (${statusQuery.data.status.masked_api_key})` : "sk-..."}
            />
            {statusQuery.data?.status.has_api_key && (
              <small className="key-hint">
                已检测到安全凭据或环境变量配置的 API Key: <code>{statusQuery.data.status.masked_api_key}</code>
              </small>
            )}
            {isRemote && apiKey.trim() && (
              <label className="one-sentence-video-consent" htmlFor="llm-remember-api-key">
                <input
                  id="llm-remember-api-key"
                  type="checkbox"
                  checked={rememberApiKey}
                  onChange={(event) => setRememberApiKey(event.target.checked)}
                />
                四级验证通过后安全保存到当前 Windows 用户的凭据管理器，应用重启后仍可使用
              </label>
            )}
          </div>}

          {/* Outbound Warning Guard */}
          {isRemote && (
            <div className="outbound-guard-card" role="alert">
              <div className="outbound-guard-header">
                <strong>安全警示：数据将离开本机</strong>
              </div>
              <p className="outbound-guard-body">
                当前配置为<strong>远程云端 Provider ({baseUrl})</strong>。剧本文字及视觉质检画面将被发送至云端接口。
                根据安全合规策略，请在操作前明确授权。
              </p>
              <label className="outbound-confirm-checkbox">
                <input
                  type="checkbox"
                  checked={allowRemoteOutbound}
                  onChange={(e) => { invalidateVerification(); setAllowRemoteOutbound(e.target.checked); }}
                />
                <span>我已知晓并允许数据离开本机出境调用</span>
              </label>
            </div>
          )}

          <div className="llm-action-buttons">
            <button
              type="button"
              className="secondary"
              onClick={handleTestConnection}
              disabled={probing || durableProbeActive || syncing || publishing || !model.trim()}
            >
              {probing ? "正在提交测试…" : durableProbeActive ? "后台 4 级测试中…" : "1. 测试 4 级连接 (Probe)"}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={handleSyncCandidate}
              disabled={probing || durableProbeActive || syncing || publishing || !model.trim() || !probePassed}
              title={!probePassed ? "请先完成并通过 4 级连接测试" : undefined}
            >
              {syncing ? "同步中…" : "2. 同步候选 Profile"}
            </button>
            <button
              type="button"
              className="primary-action"
              onClick={handlePublish}
              disabled={
                probing ||
                durableProbeActive ||
                syncing ||
                publishing ||
                !lastSyncedProfileId ||
                !probePassed
              }
            >
              {publishing ? "正在发布…" : "3. 发布正式 Profile (Publish)"}
            </button>
          </div>

          {feedback && (
            <div className={`frame-feedback ${feedback.kind}`} role="status">
              <p>{feedback.message}</p>
            </div>
          )}
          {probeJobId && projectId && (
            <p className="muted" role="status">
              后台 Job {probeJobId.slice(0, 12)}… · {durableProbeState ?? "读取中"} · <a href={`${routes.systemJobs(projectId)}&job=${encodeURIComponent(probeJobId)}`}>打开作业详情</a>
            </p>
          )}
        </div>

        {/* Right column: 4-Level Probe status & Inspection Card */}
        <div className="llm-status-container">
          <div className="probe-overview-card">
            <h4>4 级连通性探测结果 (4-Level Verification)</h4>
            <p className="muted">
              生产级 Profile 发布要求必须通过全部 4 级验证，杜绝虚假配置或不可用端点。
            </p>

            <div className="probe-level-list">
              <div className="probe-level-item">
                <div className="probe-level-title">
                  <strong>第 1 级：进程 / 网络可达性</strong>
                  <small>检查端点 HTTP/HTTPS 端口与连接建立</small>
                </div>
                <div>{renderLevelBadge(probeResult?.probe_levels?.level_1_network)}</div>
              </div>

              <div className="probe-level-item">
                <div className="probe-level-title">
                  <strong>第 2 级：鉴权与 API Key 验证</strong>
                  <small>验证 Bearer Token 是否合法（无 401/403）</small>
                </div>
                <div>{renderLevelBadge(probeResult?.probe_levels?.level_2_auth)}</div>
              </div>

              <div className="probe-level-item">
                <div className="probe-level-title">
                  <strong>第 3 级：模型可用性探测</strong>
                  <small>查询模型列表或确认模型名称就绪</small>
                </div>
                <div>{renderLevelBadge(probeResult?.probe_levels?.level_3_model)}</div>
              </div>

              <div className="probe-level-item">
                <div className="probe-level-title">
                  <strong>第 4 级：最小样例真实推理</strong>
                  <small>发送一小段测试数据，检查模型能否返回结构化结果</small>
                </div>
                <div>{renderLevelBadge(probeResult?.probe_levels?.level_4_inference)}</div>
              </div>
            </div>

            {probeResult?.sample_output && (
              <div className="sample-output-box">
                <small className="muted">最小样例输出预览：</small>
                <pre>{JSON.stringify(probeResult.sample_output, null, 2)}</pre>
              </div>
            )}
          </div>

          <div className="runtime-info-card">
            <h4>系统运行环境状态 (Runtime Environment)</h4>
            <dl className="runtime-facts">
              <div>
                <dt>当前默认模型服务</dt>
                <dd><code>{statusQuery.data?.status.provider || "OLLAMA_LOOPBACK"}</code></dd>
              </div>
              <div>
                <dt>当前默认 Base URL</dt>
                <dd><code>{statusQuery.data?.status.base_url || "http://127.0.0.1:11434"}</code></dd>
              </div>
              <div>
                <dt>当前默认 Model</dt>
                <dd><code>{statusQuery.data?.status.model || "未配置"}</code></dd>
              </div>
              <div>
                <dt>API Key 配置状态</dt>
                <dd>
                  {statusQuery.data?.status.has_api_key ? (
                    <span className="status-pill pass">已配置 ({statusQuery.data.status.masked_api_key})</span>
                  ) : (
                    <span className="status-pill neutral">未配置 (可按需填入)</span>
                  )}
                </dd>
              </div>
            </dl>
          </div>
        </div>
      </div>
    </section>
  );
}
