import type { AdapterRegistry, G8Readiness, G9Readiness, ModelCompatibilitySnapshot, ProjectConfiguration, TimelineStatus } from "../../generated/api";
import { GateStatusIcon } from "../../components/icons";

export function ProjectConfigurationSnapshot({ configuration }: { configuration: ProjectConfiguration }) {
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

export function ModelCompatibilityPanel({ snapshot }: { snapshot: ModelCompatibilitySnapshot }) {
  return <section className="panel configuration-snapshot" aria-labelledby="model-compatibility-title">
    <div className="panel-heading"><div><p className="eyebrow">G7 MODEL EVIDENCE</p><h3 id="model-compatibility-title">离线模型兼容性与许可证证据</h3></div><span className={`status-pill${snapshot.summary.pass_count === snapshot.summary.reported_count && snapshot.summary.missing_license_evidence_count === 0 ? "" : " neutral"}`}>{snapshot.summary.pass_count}/{snapshot.summary.reported_count} PASS</span></div>
    <div className="configuration-grid">
      <div className="configuration-card"><small>模型 Artifact</small><strong>{snapshot.summary.artifact_count}</strong><span>只读磁盘证据索引</span></div>
      <div className="configuration-card"><small>许可证证据</small><strong>{snapshot.summary.missing_license_evidence_count} 缺失</strong><span>仅接受项目 00_admin/licenses 内真实记录</span></div>
      <div className="configuration-card"><small>报告状态</small><strong>{snapshot.summary.blocked_count} BLOCKED</strong><span>不自动改变 G7 门禁</span></div>
    </div>
    <div className="configuration-table" role="table" aria-label="模型兼容性证据">
      <div className="configuration-row configuration-header" role="row"><strong>模型</strong><strong>Hash / 量化</strong><strong>许可证</strong><strong>状态</strong></div>
      {snapshot.reports.slice(0, 8).map((item) => <div className="configuration-row" role="row" key={item.artifact_id}><span>{item.code}<small>{item.kind}</small></span><span>{item.report_sha256 ? `${item.report_sha256.slice(0, 12)}…` : "未报告"} · {String(item.quantization.status ?? "UNKNOWN")}</span><span>{item.has_license_evidence ? item.license_path_rel : "缺失真实证据"}</span><span className={`status-pill${item.report_status === "PASS" ? "" : " neutral"}`}>{item.report_status ?? "未报告"}</span></div>)}
    </div>
    <p className="muted">只读 projection：runtime_contacted=false · network_contacted=false · mutated=false。模型许可证不可由插件 LICENSE、下载 URL 或推测替代。</p>
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
      <div className="configuration-card"><small>字幕 / 音频</small><strong>{status.subtitles.revision_count} / {status.audio.binding_count}</strong><span>{latestSubtitle ? `${String(latestSubtitle.format)} · ${String(latestSubtitle.cue_count)} cues` : "暂无字幕；音频授权记录按实际汇总"}</span></div>
      <div className="configuration-card"><small>整集渲染</small><strong>{status.renders.count}</strong><span>{latestRender ? String(latestRender.status) : "暂无真实 render"}</span></div>
      <div className="configuration-card"><small>交付包</small><strong>{status.delivery.count}</strong><span>{latestDelivery ? String(latestDelivery.status) : "暂无真实 delivery"}</span></div>
    </div>
    <div className="canvas-status"><span>本地授权音频：{status.audio.verified_local_count}</span><span>已验证渲染：{status.renders.verified_count}</span><span>已验证交付：{status.delivery.verified_count}</span><span>runtime_contacted=false</span><span>network_contacted=false</span><span>mutated=false</span></div>
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
  return <div className="project-list">{projects.map((project) => <button className={`project-row${selectedProjectId === project.id ? " selected" : ""}`} key={project.id} onClick={() => onSelect(project.id)}><span><strong>{project.title}</strong><small>{project.code}</small></span><span className="status-pill">{project.status}</span></button>)}</div>;
}

export function DiagnosticPanel({ run }: { run: { status: string; checks: Array<{ code: string; status: string; observed: Record<string, unknown> }> } | null }) {
  if (!run) return <p className="empty-state">还没有诊断记录；点击“运行诊断”执行本机只读检查。</p>;
  return <div className="diagnostic-grid"><div className="diagnostic-status"><span>整体状态</span><strong>{run.status}</strong></div>{run.checks.map((check, index) => <div className="diagnostic-row" key={`${check.code}-${index}`}><span>{check.code}</span><strong>{check.status}</strong></div>)}</div>;
}
