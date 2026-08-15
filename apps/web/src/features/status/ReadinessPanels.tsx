import { useState } from "react";
import { createDeliveryTarget, type AdapterRegistry, type G8Readiness, type G9Readiness, type ModelCompatibilitySnapshot, type ProjectConfiguration, type TimelineStatus } from "../../generated/api";
import { GateStatusIcon } from "../../components/icons";
import { ModelLicenseEvidenceForm } from "./ModelLicenseEvidenceForm";
import { LocalModelReferenceForm } from "./LocalModelReferenceForm";
import { LocalModelScanForm } from "./LocalModelScanForm";

export function ProjectConfigurationSnapshot({ configuration, projectId, onChanged }: { configuration: ProjectConfiguration; projectId?: string; onChanged?: () => void }) {
  const [code, setCode] = useState("local-files");
  const [title, setTitle] = useState("本地文件交付");
  const [pathRel, setPathRel] = useState("06_delivery");
  const [width, setWidth] = useState("1920");
  const [height, setHeight] = useState("1080");
  const [fps, setFps] = useState("24");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const createTarget = async () => {
    if (!projectId) return;
    setBusy(true); setMessage(null); setError(null);
    try {
      const result = await createDeliveryTarget(projectId, { code: code.trim(), title: title.trim(), transport: "LOCAL_FILESYSTEM", spec: { path_rel: pathRel.trim(), width: Number(width), height: Number(height), fps: Number(fps), audio: "AAC", subtitles: "SIDECAR" } });
      setMessage(`已创建交付目标版本：${String(result.target.version_id ?? result.target.id ?? "已完成").slice(0, 16)}`);
      onChanged?.();
    } catch (caught) { setError(String(caught)); }
    finally { setBusy(false); }
  };
  return <section className="panel configuration-snapshot" aria-labelledby="configuration-snapshot-title">
    <div className="panel-heading"><div><p className="eyebrow">G7 PROJECT CONFIGURATION</p><h3 id="configuration-snapshot-title">项目配置快照与切换影响</h3></div><span className="status-pill">只读 · LOCAL_ONLY</span></div>
    <p className="muted">当前绑定、版本和已冻结任务来自持久化状态。切换 Profile 不会改写历史 Job；交付目标必须显式选择，远程 transport 永不启用。</p>
    <div className="configuration-grid">
      <div className="configuration-card"><small>ProductionPlan</small><strong>{configuration.production_plan ? `${configuration.production_plan.code} · v${configuration.production_plan.version_no}` : "未绑定"}</strong><span>{configuration.production_plan?.status ?? "BLOCKED"}</span></div>
      <div className="configuration-card"><small>DeliveryTargetVersion</small><strong>{configuration.selected_delivery_target_version_id ? (configuration.delivery_targets.find((item) => item.version_id === configuration.selected_delivery_target_version_id)?.code ?? "已选择") : "需明确选择"}</strong><span>{configuration.impact.remote_transport_allowed ? "REMOTE 可用" : "REMOTE 已禁用"}</span></div>
      <div className="configuration-card"><small>Profile 矩阵</small><strong>{configuration.profile_bindings.length} 个绑定</strong><span>{configuration.profile_bindings.reduce((total, item) => total + item.frozen_job_count, 0)} 个历史 Job 快照</span></div>
    </div>
    <div className="configuration-table" role="table" aria-label="Profile 绑定矩阵">
      <div className="configuration-row configuration-header" role="row"><strong>能力</strong><strong>Profile 版本</strong><strong>状态</strong><strong>冻结 Job</strong></div>
      {configuration.profile_bindings.map((item) => <div className="configuration-row" role="row" key={`${item.capability}-${item.profile_version_id}`}><span>{item.capability}</span><span>{item.profile_code} · v{item.version_no}</span><span className="status-pill">{item.profile_status}</span><span>{item.frozen_job_count}</span></div>)}
      {configuration.profile_bindings.length === 0 && <p className="empty-state">尚未绑定 Profile。</p>}
    </div>
    {projectId && <div className="delivery-target-editor"><div className="workflow-history-heading"><div><p className="eyebrow">FR-DEL-003 · EXPLICIT TARGET</p><h3>创建本地交付目标版本</h3></div><span className="status-pill neutral">LOCAL_FILESYSTEM</span></div><p className="muted">交付规格必须由用户显式填写；此处不会启用远程 transport，也不会覆盖已有目标版本。</p><div className="field-grid"><label>代码<input value={code} onChange={(event) => setCode(event.target.value)} /></label><label>标题<input value={title} onChange={(event) => setTitle(event.target.value)} /></label><label>相对目录<input value={pathRel} onChange={(event) => setPathRel(event.target.value)} /></label><label>宽<input type="number" min="64" value={width} onChange={(event) => setWidth(event.target.value)} /></label><label>高<input type="number" min="64" value={height} onChange={(event) => setHeight(event.target.value)} /></label><label>FPS<input type="number" min="1" max="120" value={fps} onChange={(event) => setFps(event.target.value)} /></label></div><button className="primary-action" type="button" onClick={() => void createTarget()} disabled={busy}>{busy ? "创建中…" : "创建新目标版本"}</button>{message && <p className="review-success" role="status">{message}</p>}{error && <p className="inline-error" role="alert">目标创建失败：{error}</p>}</div>}
  </section>;
}

export function AdapterContractsPanel({ registry }: { registry?: AdapterRegistry }) {
  return <section className="panel adapter-contracts" aria-labelledby="adapter-contracts-title">
    <div className="panel-heading"><div><p className="eyebrow">G7 ADAPTER SDK</p><h3 id="adapter-contracts-title">本地适配器契约</h3></div><span className="status-pill">静态检查 · 无运行时接触</span></div>
    <p className="muted">这里仅展示 transport 边界与能力声明；不会启动 ComfyUI、Ollama、CLI 或 FFmpeg，也不会打开网络连接。</p>
    <div className="configuration-table" role="table" aria-label="本地适配器契约">
      <div className="configuration-row configuration-header" role="row"><strong>适配器</strong><strong>Transport</strong><strong>状态</strong><strong>能力</strong></div>
      {(registry?.contracts ?? []).map((item) => <div className="configuration-row" role="row" key={item.code}><span>{item.title}</span><span>{item.transport}</span><span className="status-pill">{item.status}</span><span>{item.capabilities.slice(0, 3).join(" · ")}</span></div>)}
      {!registry && <p className="empty-state">正在读取本地契约…</p>}
    </div>
  </section>;
}

export function ModelCompatibilityPanel({ snapshot, projectId, onEvidenceImported }: { snapshot: ModelCompatibilitySnapshot; projectId?: string; onEvidenceImported?: () => void }) {
  return <section className="panel configuration-snapshot" aria-labelledby="model-compatibility-title">
    <div className="panel-heading"><div><p className="eyebrow">G7 LOCAL MODEL REFERENCES</p><h3 id="model-compatibility-title">用户自带模型路径与兼容性</h3></div><span className={`status-pill${snapshot.summary.pass_count === snapshot.summary.reported_count ? "" : " neutral"}`}>{snapshot.summary.pass_count}/{snapshot.summary.reported_count} COMPATIBLE</span></div>
    <div className="configuration-grid">
      <div className="configuration-card"><small>模型 Artifact</small><strong>{snapshot.summary.artifact_count}</strong><span>仅引用电脑里的路径，不复制权重</span></div>
      <div className="configuration-card"><small>用户授权记录</small><strong>{snapshot.summary.missing_license_evidence_count} 未填写</strong><span>风险提示，不阻塞平台验证</span></div>
      <div className="configuration-card"><small>兼容性报告</small><strong>{snapshot.summary.blocked_count} BLOCKED</strong><span>格式、hash、量化或能力不匹配均硬阻断</span></div>
    </div>
    <div className="configuration-table" role="table" aria-label="模型兼容性证据">
      <div className="configuration-row configuration-header" role="row"><strong>模型</strong><strong>Hash / 量化</strong><strong>许可证</strong><strong>状态</strong></div>
      {snapshot.reports.slice(0, 8).map((item) => <div className="configuration-row" role="row" key={item.artifact_id}><span>{item.code}<small>{item.kind}</small></span><span>{item.report_sha256 ? `${item.report_sha256.slice(0, 12)}…` : "未报告"} · {String(item.quantization.status ?? "UNKNOWN")}<small>能力：{String((item.quantization.capability as { status?: string } | undefined)?.status ?? "未请求")}</small></span><span>{item.has_license_evidence ? item.license_path_rel : "用户未声明 · 自行负责"}</span><span className={`status-pill${item.report_status === "PASS" ? "" : " neutral"}`}>{item.report_status ?? "未报告"}{item.blockers.length > 0 && <small>{item.blockers.join(" · ")}</small>}</span></div>)}
    </div>
    <p className="muted">平台只保存本机绝对路径、hash 和兼容性，不捆绑、上传或重新分发模型。授权信息由用户按实际情况自愿记录；未填写时明确提示风险，但不阻塞平台功能验证。</p>
    {projectId && onEvidenceImported && <><LocalModelScanForm /><LocalModelReferenceForm projectId={projectId} onRegistered={onEvidenceImported} /><ModelLicenseEvidenceForm projectId={projectId} reports={snapshot.reports} onImported={onEvidenceImported} /></>}
  </section>;
}

export function TimelineStatusPanel({ status }: { status: TimelineStatus }) {
  const latestTimeline = status.timeline.latest;
  const latestSubtitle = status.subtitles.latest;
  const latestRender = status.renders.latest;
  const latestDelivery = status.delivery.latest;
  return <section className="panel timeline-status-panel" aria-labelledby="timeline-status-title">
    <div className="panel-heading"><div><p className="eyebrow">G8 TIMELINE / DELIVERY</p><h3 id="timeline-status-title">时间线与交付状态</h3></div><span className="status-pill neutral">只读观测</span></div>
    <p className="muted">仅汇总当前集已持久化的 timeline、字幕、音频、渲染和交付记录；没有真实记录就明确显示为空，不会自动生成样片或交付包。</p>
    <div className="configuration-grid capacity-grid">
      <div className="configuration-card"><small>Timeline revision</small><strong>{status.timeline.revision_count}</strong><span>{latestTimeline ? `最新 v${String(latestTimeline.revision_no)}` : "暂无真实 revision"}</span></div>
      <div className="configuration-card"><small>字幕 / 音频</small><strong>{status.subtitles.revision_count} / {status.audio.binding_count}</strong><span>{latestSubtitle ? `${String(latestSubtitle.format)} · ${String(latestSubtitle.cue_count)} cues · ${latestSubtitle.authority_status === "VERIFIED_SCRIPT" ? "剧本权威已验证" : "遗留证据不完整"}` : "暂无字幕；音频授权记录按实际汇总"}</span></div>
      <div className="configuration-card"><small>整集渲染</small><strong>{status.renders.count}</strong><span>{latestRender ? String(latestRender.status) : "暂无真实 render"}</span></div>
      <div className="configuration-card"><small>交付包</small><strong>{status.delivery.count}</strong><span>{latestDelivery ? String(latestDelivery.status) : "暂无真实 delivery"}</span></div>
    </div>
    <div className="canvas-status"><span>含用户授权记录：{status.audio.verified_local_count}</span><span>字幕文本权威：{latestSubtitle?.authority_status === "VERIFIED_SCRIPT" ? "SCRIPT" : "未验证"}</span><span>ASR：{latestSubtitle?.asr_alignment_only ? "仅时间对齐" : "未参与"}</span><span>已验证渲染：{status.renders.verified_count}</span><span>已验证交付：{status.delivery.verified_count}</span><span>runtime_contacted=false</span><span>network_contacted=false</span><span>mutated=false</span></div>
  </section>;
}

const g8CheckLabels: Record<string, string> = {
  THREE_REAL_SHOTS: "3+ 个真实镜头", DIALOGUE_ENVIRONMENT_SFX_MUSIC: "对白 / 环境 / SFX / 音乐", SUBTITLES: "字幕 revision", TIMELINE_INPUT_LOCKED: "时间线输入锁定", APPROVED_EPISODE_RENDER: "整集批准渲染", VERIFIED_DELIVERY: "交付包校验", TAMPER_DETECTION: "篡改检测",
};

export function G8ReadinessPanel({ readiness }: { readiness: G8Readiness }) {
  return <section className="panel gate-readiness" aria-labelledby="g8-readiness-title">
    <div className="panel-heading"><div><p className="eyebrow">G8 FORMAL EXIT READINESS</p><h3 id="g8-readiness-title">整集音频、字幕、时间线与交付门禁</h3></div><span className={`status-pill${readiness.status === "PASS" ? "" : " neutral"}`}>{readiness.status}</span></div>
    <p className="muted">只读检查蓝图 09 的正式退出条件；不会创建素材、启动 ComfyUI 或自动替代整集人工批准。当前集：{readiness.episode.code} · {readiness.episode.title}</p>
    <ol className="gate-checks">{readiness.checks.map((check) => <li className={check.passed ? "passed" : "blocked"} key={check.code}><GateStatusIcon passed={check.passed} /><strong>{g8CheckLabels[check.code] ?? check.code}</strong>{check.count !== undefined && <small>{check.count} 项真实证据</small>}<small>{check.detail}</small></li>)}</ol>
    {readiness.next_required_action && <p className="gate-next"><strong>下一项真实动作：</strong>{g8CheckLabels[readiness.next_required_action] ?? readiness.next_required_action}。系统保持阻塞，不以空记录或机器推测冒充 PASS。</p>}
    <div className="canvas-status"><span>timeline {readiness.evidence.timeline_revision_id ? "已锁定" : "缺失"}</span><span>render {readiness.evidence.render_id ? "已记录" : "缺失"}</span><span>delivery {readiness.evidence.delivery_id ? "已记录" : "缺失"}</span><span>runtime_contacted=false</span><span>network_contacted=false</span><span>mutated=false</span></div>
  </section>;
}

const g9CheckLabels: Record<string, string> = {
  LAZY_GRAPH_READ_MODEL: "懒加载画布 read model", LAYOUT_DEPENDENCY_ISOLATION: "布局与业务依赖隔离", PREFLIGHT_PERSISTENCE: "执行先行 preflight", VISIBLE_NODE_PERFORMANCE_UAT: "100—300 节点性能 UAT", ACCESSIBILITY_ROUTE_UAT: "三视图键盘 / 可访问性 UAT",
};

export function G9ReadinessPanel({ readiness }: { readiness: G9Readiness }) {
  return <section className="panel gate-readiness" aria-labelledby="g9-readiness-title">
    <div className="panel-heading"><div><p className="eyebrow">G9 FORMAL EXIT READINESS</p><h3 id="g9-readiness-title">业务画布与生产效率门禁</h3></div><span className={`status-pill${readiness.status === "PASS" ? "" : " neutral"}`}>{readiness.status}</span></div>
    <p className="muted">只读区分生产图事实与规模 fixture 证据；不会创建镜头、布局、执行计划或 Job。当前集：{readiness.episode.code} · {readiness.episode.title}</p>
    <ol className="gate-checks">{readiness.checks.map((check) => <li className={check.passed ? "passed" : "blocked"} key={check.code}><GateStatusIcon passed={check.passed} /><strong>{g9CheckLabels[check.code] ?? check.code}</strong>{check.count !== undefined && <small>{check.count} 项真实观测</small>}<small>{check.detail}</small></li>)}</ol>
    {readiness.next_required_action && <p className="gate-next"><strong>下一项真实动作：</strong>{g9CheckLabels[readiness.next_required_action] ?? readiness.next_required_action}。fixture 不会被当作生产退出证据。</p>}
    <div className="canvas-status"><span>生产镜头：{readiness.evidence.production_total_shots}</span><span>可见节点：{readiness.evidence.production_visible_nodes}</span><span>layout：{readiness.evidence.persisted_layout_count}</span><span>preflight：{readiness.evidence.persisted_preflight_count}</span><span>runtime_contacted=false</span><span>network_contacted=false</span><span>mutated=false</span></div>
  </section>;
}

export function ProjectList({ projects, selectedProjectId, onSelect }: { projects: Array<{ id: string; code: string; title: string; status: string }>; selectedProjectId: string | null; onSelect: (id: string) => void }) {
  if (!projects.length) return <p className="empty-state">暂无项目。通过真实项目 API 创建后，项目会出现在这里。</p>;
  return <div className="project-list" role="list" aria-label="项目列表">{projects.map((project) => <button type="button" className={`project-row${selectedProjectId === project.id ? " selected" : ""}`} aria-current={selectedProjectId === project.id ? "true" : undefined} key={project.id} onClick={() => onSelect(project.id)}><span><strong>{project.title}</strong><small>{project.code}</small></span><span className="status-pill">{project.status}</span></button>)}</div>;
}

export function DiagnosticPanel({ run }: { run: { status: string; checks: Array<{ code: string; status: string; observed: Record<string, unknown> }> } | null }) {
  if (!run) return <p className="empty-state">还没有诊断记录；点击“运行诊断”执行本机只读检查。</p>;
  return <div className="diagnostic-grid"><div className="diagnostic-status"><span>整体状态</span><strong>{run.status}</strong></div>{run.checks.map((check, index) => <div className="diagnostic-row" key={`${check.code}-${index}`}><span>{check.code}</span><strong>{check.status}</strong></div>)}</div>;
}
