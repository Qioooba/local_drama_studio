import { Drawer, InspectorSection, StatusBadge } from "../../components/ui/primitives";
import type { ProfileExecutionComponent, ProfileExecutionDetail, ProfileVersionDetail } from "../../generated/api";

const ROLE_LABELS: Record<string, string> = {
  PRIMARY_MODEL: "主模型",
  TEXT_ENCODER: "文本编码器",
  VIDEO_VAE: "Video VAE",
  AUDIO_VAE: "Audio VAE",
  LORA: "LoRA",
  CONTROLNET: "ControlNet",
  UPSCALER: "放大模型",
  TTS_ENGINE: "TTS 引擎",
  VOICE_MODEL: "声音模型",
  REMOTE_MODEL: "远端模型",
};

function roleLabel(role: string) {
  return ROLE_LABELS[role] ?? role.replaceAll("_", " ");
}

function formatBytes(value?: number | null) {
  if (!value || value <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = value;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount.toFixed(amount >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

function shortHash(value?: string | null) {
  if (!value) return "—";
  return value.startsWith("sha256:") ? value.slice(0, 19) : value.slice(0, 16);
}

function availabilityTone(component: ProfileExecutionComponent) {
  if (component.status === "MISSING") return "danger" as const;
  if (component.available === false) return "attention" as const;
  if (component.available === true || component.status === "CONFIGURED") return "success" as const;
  return "neutral" as const;
}

function ComponentRow({ component }: { component: ProfileExecutionComponent }) {
  return (
    <article className="model-inspector-component">
      <div className="model-inspector-component__heading">
        <div>
          <strong>{roleLabel(component.role)}</strong>
          <small>{component.title}</small>
        </div>
        <StatusBadge tone={availabilityTone(component)}>
          {component.status === "MISSING" ? "缺失" : component.available === false ? "不可用" : component.status}
        </StatusBadge>
      </div>
      <dl className="model-inspector-facts">
        {component.kind ? <div><dt>类型</dt><dd>{component.kind}</dd></div> : null}
        {component.purpose ? <div><dt>用途</dt><dd>{component.purpose}</dd></div> : null}
        {component.size_bytes !== undefined ? <div><dt>大小</dt><dd>{formatBytes(component.size_bytes)}</dd></div> : null}
        {component.sha256 ? <div><dt>SHA-256</dt><dd><code title={component.sha256}>{shortHash(component.sha256)}</code></dd></div> : null}
      </dl>
      {component.machine_path ? (
        <details className="model-inspector-path">
          <summary>查看本机路径</summary>
          <code>{component.machine_path}</code>
        </details>
      ) : null}
    </article>
  );
}

function SummaryRows({ execution }: { execution: ProfileExecutionDetail }) {
  const runtime = execution.runtime;
  const workflow = execution.workflow;
  const provider = execution.provider_connection;
  return (
    <dl className="model-inspector-summary">
      <div><dt>运行时</dt><dd>{String(runtime?.title ?? runtime?.code ?? "未绑定")}</dd></div>
      <div><dt>运行状态</dt><dd>{String(runtime?.status ?? "未知")}</dd></div>
      <div><dt>工作流</dt><dd>{String(workflow?.title ?? workflow?.code ?? "未绑定")}</dd></div>
      <div><dt>工作流版本</dt><dd>{workflow?.version_no ? `v${String(workflow.version_no)}` : "—"}</dd></div>
      {provider ? <div><dt>Provider Connection</dt><dd>{provider.title} · {provider.protocol}</dd></div> : null}
      {provider ? <div><dt>服务地址</dt><dd><code>{provider.base_url}</code></dd></div> : null}
      {provider || execution.provider ? <div><dt>远端模型</dt><dd>{provider?.model ?? execution.model ?? "—"}</dd></div> : null}
      <div><dt>可配置字段</dt><dd>{Object.keys((execution.override_schema.fields as Record<string, unknown> | undefined) ?? {}).length} 个</dd></div>
      <div><dt>执行指纹</dt><dd><code title={execution.fingerprints.execution}>{shortHash(execution.fingerprints.execution)}</code></dd></div>
    </dl>
  );
}

export function ModelInspectorDrawer({
  open,
  profile,
  onClose,
}: {
  open: boolean;
  profile: ProfileVersionDetail | null;
  onClose: () => void;
}) {
  const execution = profile?.execution ?? null;
  return (
    <Drawer open={open} title={profile ? `${profile.title} · v${profile.version_no} 执行详情` : "模型执行详情"} width="min(620px, 92vw)" onClose={onClose}>
      {!profile ? <p className="empty-state">尚未选择 Profile 版本。</p> : !execution ? (
        <div className="inline-error" role="alert">当前 API 没有返回执行详情；请刷新 Profile 版本后重试。</div>
      ) : (
        <div className="model-inspector-drawer">
          <div className="model-inspector-intro">
            <div>
              <p className="eyebrow">真实执行内容</p>
              <h3>{profile.capability}</h3>
              <p className="muted">以下内容来自 Profile Version、模型清单和工作流版本，不是前端静态说明。</p>
            </div>
            <StatusBadge tone={profile.status === "PUBLISHED" ? "success" : "attention"}>{profile.status}</StatusBadge>
          </div>

          <InspectorSection title="概览" summary="运行时、工作流与指纹">
            <SummaryRows execution={execution} />
          </InspectorSection>

          <InspectorSection title="模型组件" summary={`${execution.components.length} 个组件`}>
            <div className="model-inspector-components">
              {execution.components.length ? execution.components.map((component, index) => <ComponentRow key={`${component.artifact_id ?? component.role}-${index}`} component={component} />) : <p className="muted">该 Profile 尚未声明模型组件。</p>}
            </div>
          </InspectorSection>

          <InspectorSection title="默认参数与可覆盖字段" summary="只读契约">
            <div className="model-inspector-json-grid">
              <div><span>默认参数</span><pre>{JSON.stringify(execution.defaults, null, 2)}</pre></div>
              <div><span>可覆盖 Schema</span><pre>{JSON.stringify(execution.override_schema, null, 2)}</pre></div>
            </div>
          </InspectorSection>

          <InspectorSection title="证据（专家）" summary="本机路径、哈希与原始快照" defaultOpen={false}>
            <dl className="model-inspector-evidence">
              <div><dt>Model Bundle</dt><dd><code>{shortHash(execution.fingerprints.model_bundle)}</code></dd></div>
              <div><dt>Workflow</dt><dd><code>{shortHash(execution.fingerprints.workflow)}</code></dd></div>
              <div><dt>Manifest</dt><dd><code>{shortHash(execution.fingerprints.manifest)}</code></dd></div>
            </dl>
            <details className="model-inspector-path">
              <summary>查看原始 Model Bundle JSON</summary>
              <pre>{JSON.stringify(execution.model_bundle, null, 2)}</pre>
            </details>
          </InspectorSection>
        </div>
      )}
    </Drawer>
  );
}
