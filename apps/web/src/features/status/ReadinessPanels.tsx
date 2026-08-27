import {
  type AdapterRegistry,
  type G8Readiness,
  type G9Readiness,
  type ModelCompatibilitySnapshot,
  type ProjectConfiguration,
  type TimelineStatus,
} from "../../generated/api";
import { GateStatusIcon } from "../../components/icons";
import { ModelLicenseEvidenceForm } from "./ModelLicenseEvidenceForm";
import { LocalModelReferenceForm } from "./LocalModelReferenceForm";
import { DeliveryTargetSetup } from "./DeliveryTargetSetup";

export function ProjectConfigurationSnapshot({
  configuration,
  projectId,
  onChanged,
}: {
  configuration: ProjectConfiguration;
  projectId?: string;
  onChanged?: () => void;
}) {
  return (
    <section
      className="panel configuration-snapshot"
      aria-labelledby="configuration-snapshot-title"
    >
      {projectId && (
        <DeliveryTargetSetup
          configuration={configuration}
          projectId={projectId}
          onChanged={onChanged}
        />
      )}
      <details className="configuration-technical-details">
        <summary id="configuration-snapshot-title">
          高级：查看生效版本与历史任务影响
        </summary>
        <p className="muted">
          这里仅供排障与版本追溯。切换生产能力不会改写已冻结的历史任务，远程交付保持禁用。
        </p>
        <div className="configuration-grid">
          <div className="configuration-card">
            <small>生产计划</small>
            <strong>
              {configuration.production_plan
                ? `${configuration.production_plan.code} · v${configuration.production_plan.version_no}`
                : "未绑定"}
            </strong>
            <span>{configuration.production_plan?.status ?? "未配置"}</span>
          </div>
          <div className="configuration-card">
            <small>当前发布版本</small>
            <strong>
              {configuration.selected_delivery_target_version_id
                ? (configuration.delivery_targets.find(
                    (item) =>
                      item.version_id ===
                      configuration.selected_delivery_target_version_id,
                  )?.title ?? "已选择")
                : "尚未选择"}
            </strong>
            <span>
              {configuration.impact.remote_transport_allowed
                ? "远端可用"
                : "仅本机"}
            </span>
          </div>
          <div className="configuration-card">
            <small>生产能力绑定</small>
            <strong>
              <span className="tabular-nums">
                {configuration.profile_bindings.length}
              </span>{" "}
              项
            </strong>
            <span>
              <span className="tabular-nums">
                {configuration.profile_bindings.reduce(
                  (total, item) => total + item.frozen_job_count,
                  0,
                )}
              </span>{" "}
              个历史任务快照
            </span>
          </div>
        </div>
        <div
          className="configuration-table"
          role="table"
          aria-label="生产能力版本绑定"
        >
          <div className="configuration-row configuration-header" role="row">
            <strong>能力</strong>
            <strong>配置版本</strong>
            <strong>状态</strong>
            <strong>历史任务</strong>
          </div>
          {configuration.profile_bindings.map((item) => (
            <div
              className="configuration-row"
              role="row"
              key={`${item.capability}-${item.profile_version_id}`}
            >
              <span>{item.capability}</span>
              <span>
                {item.profile_code} · v{item.version_no}
              </span>
              <span className="status-pill neutral">{item.profile_status}</span>
              <span className="tabular-nums">{item.frozen_job_count}</span>
            </div>
          ))}
          {configuration.profile_bindings.length === 0 && (
            <p className="empty-state">当前没有额外的生产能力绑定。</p>
          )}
        </div>
      </details>
    </section>
  );
}

export function AdapterContractsPanel({
  registry,
}: {
  registry?: AdapterRegistry;
}) {
  return (
    <section
      className="panel adapter-contracts"
      aria-labelledby="adapter-contracts-title"
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">适配器契约</p>
          <h3 id="adapter-contracts-title">本地适配器契约</h3>
        </div>
        <span className="status-pill">静态检查 · 无运行时接触</span>
      </div>
      <p className="muted">
        这里仅展示 transport 边界与能力声明；不会启动 ComfyUI、Ollama、CLI 或
        FFmpeg，也不会打开网络连接。
      </p>
      <div
        className="configuration-table"
        role="table"
        aria-label="本地适配器契约"
      >
        <div className="configuration-row configuration-header" role="row">
          <strong>适配器</strong>
          <strong>Transport</strong>
          <strong>状态</strong>
          <strong>能力</strong>
        </div>
        {(registry?.contracts ?? []).map((item) => (
          <div className="configuration-row" role="row" key={item.code}>
            <span title={item.title}>{item.title}</span>
            <span title={item.transport}>{item.transport}</span>
            <span className="status-pill" title={item.status}>
              {item.status}
            </span>
            <span title={(item.capabilities ?? []).join(" · ")}>
              {(item.capabilities ?? []).slice(0, 3).join(" · ")}
              {(item.capabilities ?? []).length > 3
                ? ` · +${(item.capabilities ?? []).length - 3}`
                : ""}
            </span>
          </div>
        ))}
        {!registry && <p className="empty-state">正在读取本地契约…</p>}
      </div>
    </section>
  );
}

export function ModelCompatibilityPanel({
  snapshot,
  projectId,
  onEvidenceImported,
}: {
  snapshot: ModelCompatibilitySnapshot;
  projectId?: string;
  onEvidenceImported?: () => void;
}) {
  return (
    <section
      className="panel configuration-snapshot"
      aria-labelledby="model-compatibility-title"
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">本机模型引用</p>
          <h3 id="model-compatibility-title">用户自带模型路径与兼容性</h3>
        </div>
        <span
          className={`status-pill${snapshot.summary.pass_count === snapshot.summary.reported_count ? "" : " neutral"}`}
        >
          {snapshot.summary.pass_count}/{snapshot.summary.reported_count} 兼容
        </span>
      </div>
      <div className="configuration-grid">
        <div className="configuration-card">
          <small>模型 Artifact</small>
          <strong>{snapshot.summary.artifact_count}</strong>
          <span>仅引用电脑里的路径，不复制权重</span>
        </div>
        <div className="configuration-card">
          <small>用户授权记录</small>
          <strong>
            {snapshot.summary.missing_license_evidence_count} 未填写
          </strong>
          <span>风险提示，不阻塞平台验证</span>
        </div>
        <div className="configuration-card">
          <small>兼容性报告</small>
          <strong>{snapshot.summary.blocked_count} BLOCKED</strong>
          <span>文件格式、校验指纹、模型精度或能力不匹配时都会阻止继续</span>
        </div>
      </div>
      <div
        className="configuration-table"
        role="table"
        aria-label="模型兼容性证据"
      >
        <div className="configuration-row configuration-header" role="row">
          <strong>模型</strong>
          <strong>Hash / 量化</strong>
          <strong>许可证</strong>
          <strong>状态</strong>
        </div>
        {snapshot.reports.slice(0, 8).map((item) => (
          <div className="configuration-row" role="row" key={item.artifact_id}>
            <span title={`${item.code} · ${item.kind}`}>
              {item.code}
              <small>{item.kind}</small>
            </span>
            <span
              title={`${item.report_sha256 ? `${item.report_sha256.slice(0, 12)}…` : "未报告"} · ${String(item.quantization.status ?? "UNKNOWN")} · 能力：${String((item.quantization.capability as { status?: string } | undefined)?.status ?? "未请求")}`}
            >
              {item.report_sha256
                ? `${item.report_sha256.slice(0, 12)}…`
                : "未报告"}{" "}
              · {String(item.quantization.status ?? "UNKNOWN")}
              <small>
                能力：
                {String(
                  (
                    item.quantization.capability as
                      | { status?: string }
                      | undefined
                  )?.status ?? "未请求",
                )}
              </small>
            </span>
            <span
              title={String(
                item.has_license_evidence
                  ? item.license_path_rel
                  : "用户未声明 · 自行负责",
              )}
            >
              {item.has_license_evidence
                ? item.license_path_rel
                : "用户未声明 · 自行负责"}
            </span>
            <span
              className={`status-pill${item.report_status === "PASS" ? "" : " neutral"}`}
              title={String(
                item.blockers.join(" · ") || item.report_status || "未报告",
              )}
            >
              {item.report_status ?? "未报告"}
              {item.blockers.length > 0 && (
                <small>{item.blockers.join(" · ")}</small>
              )}
            </span>
          </div>
        ))}
      </div>
      <p className="muted">
        平台只保存本机绝对路径、hash
        和兼容性，不捆绑、上传或重新分发模型。授权信息由用户按实际情况自愿记录；未填写时明确提示风险，但不阻塞平台功能验证。
      </p>
      {projectId && onEvidenceImported && (
        <>
          <LocalModelReferenceForm
            projectId={projectId}
            onRegistered={onEvidenceImported}
          />
          <ModelLicenseEvidenceForm
            projectId={projectId}
            reports={snapshot.reports}
            onImported={onEvidenceImported}
          />
        </>
      )}
    </section>
  );
}

export function TimelineStatusPanel({ status }: { status: TimelineStatus }) {
  const latestTimeline = status.timeline.latest;
  const latestSubtitle = status.subtitles.latest;
  const latestRender = status.renders.latest;
  const latestDelivery = status.delivery.latest;
  return (
    <section
      className="panel timeline-status-panel"
      aria-labelledby="timeline-status-title"
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">时间线 / 交付</p>
          <h3 id="timeline-status-title">时间线与交付状态</h3>
        </div>
        <span className="status-pill neutral">只读观测</span>
      </div>
      <p className="muted">
        仅汇总当前集已持久化的
        timeline、字幕、音频、渲染和交付记录；没有真实记录就明确显示为空，不会自动生成样片或交付包。
      </p>
      <div className="configuration-grid capacity-grid">
        <div className="configuration-card">
          <small>时间线修订</small>
          <strong>{status.timeline.revision_count}</strong>
          <span>
            {latestTimeline
              ? `最新 v${String(latestTimeline.revision_no)}`
              : "暂无真实 revision"}
          </span>
        </div>
        <div className="configuration-card">
          <small>字幕 / 音频</small>
          <strong>
            {status.subtitles.revision_count} / {status.audio.binding_count}
          </strong>
          <span>
            {latestSubtitle
              ? `${String(latestSubtitle.format)} · ${String(latestSubtitle.cue_count)} cues · ${latestSubtitle.authority_status === "VERIFIED_SCRIPT" ? "剧本权威已验证" : "遗留证据不完整"}`
              : "暂无字幕；音频授权记录按实际汇总"}
          </span>
        </div>
        <div className="configuration-card">
          <small>整集渲染</small>
          <strong>{status.renders.count}</strong>
          <span>
            {latestRender ? String(latestRender.status) : "暂无真实 render"}
          </span>
        </div>
        <div className="configuration-card">
          <small>交付包</small>
          <strong>{status.delivery.count}</strong>
          <span>
            {latestDelivery
              ? String(latestDelivery.status)
              : "暂无真实 delivery"}
          </span>
        </div>
      </div>
      <div className="canvas-status">
        <span>含用户授权记录：{status.audio.verified_local_count}</span>
        <span>
          字幕文本权威：
          {latestSubtitle?.authority_status === "VERIFIED_SCRIPT"
            ? "SCRIPT"
            : "未验证"}
        </span>
        <span>
          ASR：{latestSubtitle?.asr_alignment_only ? "仅时间对齐" : "未参与"}
        </span>
        <span>已验证渲染：{status.renders.verified_count}</span>
        <span>已验证交付：{status.delivery.verified_count}</span>
      </div>
    </section>
  );
}

const g8CheckLabels: Record<string, string> = {
  THREE_REAL_SHOTS: "至少三个成片镜头",
  DIALOGUE_BGM_SFX: "对白、配乐与音效",
  SUBTITLES: "字幕版本",
  TIMELINE_INPUT_LOCKED: "时间线输入锁定",
  APPROVED_EPISODE_RENDER: "整集成片已批准",
  VERIFIED_DELIVERY: "交付包校验",
  TAMPER_DETECTION: "自动完整性自检",
};
const g8CheckDetails: Record<string, string> = {
  THREE_REAL_SHOTS: "时间线中至少包含三个已经采用的视频镜头",
  DIALOGUE_BGM_SFX: "时间线中已经绑定对白、背景配乐和动作音效",
  SUBTITLES: "本集已经保存并绑定字幕",
  TIMELINE_INPUT_LOCKED: "时间线及其所用媒体已经固定，后续改动会创建新版本",
  APPROVED_EPISODE_RENDER: "整集成片通过文件检查，并已由创作者明确批准",
  VERIFIED_DELIVERY: "系统已核对交付包中的每个文件与清单",
  TAMPER_DETECTION: "交付校验后，系统会在隔离临时副本上自动测试破坏检测并立即清理；正式文件不会被修改",
};

export function G8ReadinessPanel({ readiness }: { readiness: G8Readiness }) {
  return (
    <section
      className="panel gate-readiness"
      aria-labelledby="g8-readiness-title"
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">正式交付就绪</p>
          <h3 id="g8-readiness-title">整集音频、字幕、时间线与交付门禁</h3>
        </div>
        <span
          className={`status-pill${readiness.status === "PASS" ? "" : " neutral"}`}
        >
          {readiness.status === "PASS" ? "通过" : "进行中"}
        </span>
      </div>
      <p className="muted">
        系统只检查当前集的真实制作证据，不会创建素材或替代整集人工批准。当前集：{readiness.episode.title}
      </p>
      <ol className="gate-checks">
        {readiness.checks.map((check) => (
          <li className={check.passed ? "passed" : "blocked"} key={check.code}>
            <GateStatusIcon passed={check.passed} />
            <div className="gate-check-body">
              <strong>{g8CheckLabels[check.code] ?? check.code}</strong>
              {check.count !== undefined && (
                <small>{check.count} 项真实证据</small>
              )}
              <small>{g8CheckDetails[check.code] ?? "由系统核对当前制作证据"}</small>
            </div>
          </li>
        ))}
      </ol>
      {readiness.next_required_action && (
        <p className="gate-next">
          <strong>下一项真实动作：</strong>
          {g8CheckLabels[readiness.next_required_action] ??
            readiness.next_required_action}
          。完成前系统会保持阻塞，不会用空记录或推测冒充通过。
        </p>
      )}
      <div className="canvas-status" aria-label="交付证据摘要">
        <span>
          时间线：{readiness.evidence.timeline_revision_id ? "已锁定" : "缺失"}
        </span>
        <span>
          整集渲染：{readiness.evidence.render_id ? "已记录" : "缺失"}
        </span>
        <span>
          交付包：{readiness.evidence.delivery_id ? "已记录" : "缺失"}
        </span>
      </div>
    </section>
  );
}

const g9CheckLabels: Record<string, string> = {
  LAZY_GRAPH_READ_MODEL: "懒加载画布 read model",
  LAYOUT_DEPENDENCY_ISOLATION: "布局与业务依赖隔离",
  PREFLIGHT_PERSISTENCE: "执行先行 preflight",
  VISIBLE_NODE_PERFORMANCE_UAT: "100—300 节点性能 UAT",
  ACCESSIBILITY_ROUTE_UAT: "三视图键盘 / 可访问性 UAT",
};

export function G9ReadinessPanel({ readiness }: { readiness: G9Readiness }) {
  return (
    <section
      className="panel gate-readiness"
      aria-labelledby="g9-readiness-title"
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">生产规模就绪</p>
          <h3 id="g9-readiness-title">业务画布与生产效率门禁</h3>
        </div>
        <span
          className={`status-pill${readiness.status === "PASS" ? "" : " neutral"}`}
        >
          {readiness.status === "PASS" ? "通过" : "进行中"}
        </span>
      </div>
      <p className="muted">
        只读区分生产图事实与规模 fixture 证据；不会创建镜头、布局、执行计划或
        Job。当前集：{readiness.episode.code} · {readiness.episode.title}
      </p>
      <ol className="gate-checks">
        {readiness.checks.map((check) => (
          <li className={check.passed ? "passed" : "blocked"} key={check.code}>
            <GateStatusIcon passed={check.passed} />
            <div className="gate-check-body">
              <strong>{g9CheckLabels[check.code] ?? check.code}</strong>
              {check.count !== undefined && (
                <small>{check.count} 项真实观测</small>
              )}
              <small>{check.detail}</small>
            </div>
          </li>
        ))}
      </ol>
      {readiness.next_required_action && (
        <p className="gate-next">
          <strong>下一项真实动作：</strong>
          {g9CheckLabels[readiness.next_required_action] ??
            readiness.next_required_action}
          。fixture 不会被当作生产退出证据。
        </p>
      )}
      <div className="canvas-status">
        <span>生产镜头：{readiness.evidence.production_total_shots}</span>
        <span>可见行：{readiness.evidence.production_visible_rows}</span>
        <span>实验节点：{readiness.evidence.visual_lab_node_count}</span>
        <span>实验快照：{readiness.evidence.visual_lab_snapshot_count}</span>
      </div>
    </section>
  );
}

export function ProjectList({
  projects,
  selectedProjectId,
  onSelect,
}: {
  projects: Array<{ id: string; code: string; title: string; status: string }>;
  selectedProjectId: string | null;
  onSelect: (id: string) => void;
}) {
  if (!projects.length)
    return (
      <p className="empty-state">
        暂无项目。通过真实项目 API 创建后，项目会出现在这里。
      </p>
    );
  return (
    <div className="project-list" role="list" aria-label="项目列表">
      {projects.map((project) => (
        <button
          type="button"
          className={`project-row${selectedProjectId === project.id ? " selected" : ""}`}
          aria-current={selectedProjectId === project.id ? "true" : undefined}
          key={project.id}
          onClick={() => onSelect(project.id)}
        >
          <span>
            <strong>{project.title}</strong>
            <small>{project.code}</small>
          </span>
          <span
            className={`status-pill state-${String(project.status).toLowerCase()}`}
          >
            {project.status}
          </span>
        </button>
      ))}
    </div>
  );
}

export function DiagnosticPanel({
  run,
}: {
  run: {
    status: string;
    checks: Array<{
      code: string;
      status: string;
      observed: Record<string, unknown>;
    }>;
  } | null;
}) {
  if (!run)
    return (
      <p className="empty-state">
        还没有诊断记录；点击“运行诊断”执行本机只读检查。
      </p>
    );
  const statusClass = (status: string) =>
    `status-${String(status).toLowerCase()}`;
  return (
    <div className="diagnostic-grid">
      <div className="diagnostic-status">
        <span>整体状态</span>
        <strong className={statusClass(run.status)}>{run.status}</strong>
      </div>
      {run.checks.map((check, index) => (
        <div className="diagnostic-row" key={`${check.code}-${index}`}>
          <span>{check.code}</span>
          <strong className={statusClass(check.status)}>{check.status}</strong>
        </div>
      ))}
    </div>
  );
}
