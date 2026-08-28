import { Drawer, InspectorSection, StatusBadge } from "../../components/ui/primitives";
import type { ProfileExecutionComponent, ProfileExecutionDetail, ProfileVersionDetail } from "../../generated/api";
import { canonicalCapabilityLabel, creatorProfileTitle } from "../preferences-v2/canonicalCapabilities";
import { ACCELERATION_LABELS, PRODUCTION_TIER_LABELS, STATUS_LABELS, optionLabel } from "../shared/optionLabels";
import { profileStageDescription, profileStatusLabel } from "../profiles/profilePresentation";

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
      <div><dt>运行状态</dt><dd>{optionLabel(STATUS_LABELS, String(runtime?.status ?? "UNKNOWN"))}</dd></div>
      <div><dt>工作流</dt><dd>{String(workflow?.title ?? workflow?.code ?? "未绑定")}</dd></div>
      <div><dt>工作流版本</dt><dd>{workflow?.version_no ? `v${String(workflow.version_no)}` : "—"}</dd></div>
      {provider ? <div><dt>生成服务连接</dt><dd>{provider.title} · {provider.protocol}</dd></div> : null}
      {provider ? <div><dt>服务地址</dt><dd><code>{provider.base_url}</code></dd></div> : null}
      {provider || execution.provider ? <div><dt>远端模型</dt><dd>{provider?.model ?? execution.model ?? "—"}</dd></div> : null}
      <div><dt>生成时可调整参数</dt><dd>{Object.keys((execution.override_schema.fields as Record<string, unknown> | undefined) ?? {}).length} 个</dd></div>
      <div><dt>执行指纹</dt><dd><code title={execution.fingerprints.execution}>{shortHash(execution.fingerprints.execution)}</code></dd></div>
    </dl>
  );
}

type InspectorParameterField = {
  label?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  options?: unknown[];
  scopes?: string[];
  editable?: boolean;
  description?: string;
};

const PARAMETER_OPTION_LABELS = { ...PRODUCTION_TIER_LABELS, ...ACCELERATION_LABELS };
const PARAMETER_SCOPE_LABELS: Record<string, string> = { PROJECT: "项目默认", SHOT: "镜头设置", RUN: "本次生成" };

function parameterValueLabel(value: unknown) {
  if (typeof value === "boolean") return value ? "开启" : "关闭";
  if (value === undefined || value === null || value === "") return "由模型决定";
  return optionLabel(PARAMETER_OPTION_LABELS, String(value), String(value));
}

function ParameterSummary({ execution }: { execution: ProfileExecutionDetail }) {
  const rawFields = execution.override_schema.fields;
  const fields = rawFields && typeof rawFields === "object" && !Array.isArray(rawFields)
    ? Object.entries(rawFields as Record<string, unknown>).filter(([, value]) => value && typeof value === "object" && !Array.isArray(value)) as Array<[string, InspectorParameterField]>
    : [];
  if (!fields.length) return <p className="muted">该能力没有声明可调整的生成参数。</p>;
  return <div className="model-inspector-parameters">
    {fields.map(([key, field]) => {
      const effectiveDefault = execution.defaults[key] ?? field.default;
      const limits = field.options?.length
        ? `${field.options.length} 个允许值`
        : field.minimum !== undefined || field.maximum !== undefined
          ? `允许范围 ${field.minimum ?? "—"}–${field.maximum ?? "—"}`
          : "由能力契约校验";
      const scopes = (field.scopes ?? []).map((scope) => PARAMETER_SCOPE_LABELS[scope] ?? scope).join("、") || "仅版本默认";
      return <article key={key}>
        <div><strong>{field.label ?? key}</strong><code>{key}</code></div>
        <dl><div><dt>当前默认</dt><dd>{parameterValueLabel(effectiveDefault)}</dd></div><div><dt>可调整阶段</dt><dd>{scopes}</dd></div><div><dt>约束</dt><dd>{field.editable === false ? "已锁定" : limits}</dd></div></dl>
        {field.description ? <p>{field.description}</p> : null}
      </article>;
    })}
  </div>;
}

export function ModelInspectorDrawer({
  open,
  profile,
  onClose,
  onEditDefaults,
}: {
  open: boolean;
  profile: ProfileVersionDetail | null;
  onClose: () => void;
  onEditDefaults?: () => void;
}) {
  const execution = profile?.execution ?? null;
  return (
    <Drawer open={open} title={profile ? `${creatorProfileTitle(profile.title)} · 第 ${profile.version_no} 版执行详情` : "模型执行详情"} width="min(680px, 94vw)" onClose={onClose}>
      {!profile ? <p className="empty-state">尚未选择生成配置版本。</p> : !execution ? (
        <div className="inline-error" role="alert">服务器没有返回执行详情；请刷新生成配置版本后重试。</div>
      ) : (
        <div className="model-inspector-drawer">
          <div className="model-inspector-intro">
            <div>
              <p className="eyebrow">真实执行绑定 · 只读证据</p>
              <h3>{canonicalCapabilityLabel(profile.capability)}</h3>
              <p className="muted">{profileStageDescription(profile.capability)}。运行时、工作流和模型组件来自模型清单及发布记录，不能在详情抽屉里临时改写。</p>
            </div>
            <StatusBadge tone={profile.status === "PUBLISHED" ? "success" : "attention"}>{profileStatusLabel(profile.status)}</StatusBadge>
          </div>

          <InspectorSection title="概览" summary="运行时、工作流与指纹">
            <SummaryRows execution={execution} />
          </InspectorSection>

          <InspectorSection title="模型组件" summary={`${execution.components.length} 个组件`}>
            <div className="model-inspector-components">
              {execution.components.length ? execution.components.map((component, index) => <ComponentRow key={`${component.artifact_id ?? component.role}-${index}`} component={component} />) : <p className="muted">该生成配置尚未声明模型组件。</p>}
            </div>
          </InspectorSection>

          <InspectorSection title="生成参数边界" summary={`${Object.keys((execution.override_schema.fields as Record<string, unknown> | undefined) ?? {}).length} 个字段`}>
            <p className="muted">默认值属于能力版本，可以在主配置区修改并派生新草稿；允许范围和运行时绑定属于模型契约，只在这里核对。</p>
            {onEditDefaults ? <button type="button" className="secondary model-inspector-edit-defaults" onClick={onEditDefaults}>返回配置生成默认参数</button> : null}
            <ParameterSummary execution={execution} />
            <details className="model-inspector-path"><summary>高级：查看参数 Schema</summary><pre>{JSON.stringify(execution.override_schema, null, 2)}</pre></details>
          </InspectorSection>

          <InspectorSection title="证据（专家）" summary="本机路径、哈希与原始快照" defaultOpen={false}>
            <dl className="model-inspector-evidence">
              <div><dt>Model Bundle</dt><dd><code>{shortHash(execution.fingerprints.model_bundle)}</code></dd></div>
              <div><dt>本机工作流指纹</dt><dd><code>{shortHash(execution.fingerprints.workflow)}</code></dd></div>
              <div><dt>Manifest</dt><dd><code>{shortHash(execution.fingerprints.manifest)}</code></dd></div>
            </dl>
            <details className="model-inspector-path">
              <summary>高级：查看原始模型包数据</summary>
              <pre>{JSON.stringify(execution.model_bundle, null, 2)}</pre>
            </details>
          </InspectorSection>
        </div>
      )}
    </Drawer>
  );
}
