import { StudioIcon } from "../../components/icons";
import type { DiagnosticRun } from "../../generated/api";

const PASS_STATUSES = new Set(["PASS", "HEALTHY"]);
const STALE_AFTER_MS = 15 * 60 * 1000;

const DIAGNOSTIC_STATUS_LABELS: Record<string, string> = {
  PASS: "正常",
  HEALTHY: "全部正常",
  DEGRADED: "需要处理",
  BLOCKED: "暂不可用",
  WARN: "需要留意",
  FAIL: "检查失败",
};

const DIAGNOSTIC_CATEGORY_LABELS: Record<string, string> = {
  runtime: "生成与语言服务",
  gpu: "显卡与生成模型",
  media: "音视频工具",
  storage: "存储空间",
  models: "模型与清单",
  network: "网络与隐私",
};

const CATEGORY_ORDER = ["runtime", "gpu", "media", "storage", "models", "network"];

const DIAGNOSTIC_CHECKS: Record<string, { title: string; description: string }> = {
  MODE_LOCAL_ONLY: { title: "运行范围", description: "确认服务只在本机或明确允许的局域网范围内运行。" },
  CANONICAL_MODEL_ROOT: { title: "模型库目录", description: "确认生产使用的模型目录存在并且可以访问。" },
  MANIFEST_READ_ONLY: { title: "模型清单保护", description: "确认诊断只读取模型清单，不修改模型文件。" },
  DISABLED_MODEL_GUARD: { title: "停用模型隔离", description: "确认停用或已知无效的模型不会进入生产任务。" },
  COMFYUI_LOOPBACK: { title: "图像与视频生成服务", description: "检查 ComfyUI 是否可以从当前部署范围访问。" },
  H3_CANDIDATE_LAYOUT: { title: "H3 视频模型", description: "检查 H3 视频生成所需的模型组件是否齐全。" },
  LOCAL_LLM_LOOPBACK: { title: "本地语言模型", description: "检查当前语言模型能否完成一次实际推理。" },
  LOCAL_EXECUTOR: { title: "本机任务执行器", description: "确认有排队任务时，本机执行器已在服务内运行并能自动接管。" },
  FFMPEG: { title: "视频处理", description: "检查视频转码、合成和导出所需的 FFmpeg。" },
  FFPROBE: { title: "媒体读取", description: "检查读取视频时长、分辨率和编码所需的 FFprobe。" },
  DISK_SPACE: { title: "项目存储空间", description: "检查素材、缓存和生成结果是否有足够空间。" },
  GPU_MANIFEST: { title: "显卡与显存", description: "确认当前显卡和可用显存能够被系统识别。" },
  GPU_DRIVER_CUDA: { title: "驱动与计算支持", description: "检查显卡驱动、CUDA 和本地计算支持。" },
  COMFYUI_NODE_REGISTRY: { title: "生成工作流节点", description: "检查当前生产工作流需要的 ComfyUI 节点。" },
  MODEL_INVENTORY_HASH: { title: "模型完整性", description: "检查生产模型是否具有防止损坏或错配的校验指纹。" },
  NETWORK_POLICY: { title: "网络访问范围", description: "确认运行时不会越过当前部署允许的网络边界。" },
  REMOTE_PROVIDER: { title: "云服务回退", description: "确认本地服务失败时不会自动发送剧本或素材到云端。" },
};

const DIAGNOSTIC_REMEDIATIONS: Record<string, string> = {
  COMFYUI_LOOPBACK: "启动已配置的 ComfyUI 服务后重新检查。系统不会自动下载组件或切换到公网服务。",
  H3_CANDIDATE_LAYOUT: "补齐模型清单指定的 H3 配套文件后重新检查。系统不会下载或改写模型目录。",
  LOCAL_LLM_LOOPBACK: "在能力与模型中选择本机语言模型，并确认 llama-server 已启动。",
  LOCAL_EXECUTOR: "本机应用服务恢复后会自动接管已排队任务；若持续没有执行器，请重新启动应用服务后重新检查。页面不会要求输入命令。",
  FFMPEG: "安装 FFmpeg，或在系统配置中填写其程序路径。",
  FFPROBE: "安装 FFprobe，或在系统配置中填写其程序路径。",
  GPU_MANIFEST: "确认 NVIDIA 显卡可被系统识别后重新检查。",
  GPU_DRIVER_CUDA: "确认 NVIDIA 驱动和 CUDA 可用后重新检查；系统不会自动修改驱动。",
  COMFYUI_NODE_REGISTRY: "为当前工作流安装缺少的 ComfyUI 节点后重新检查。",
  MODEL_INVENTORY_HASH: "为生产模型补齐 SHA-256 校验指纹；系统不会移动、复制或上传模型文件。",
  NETWORK_POLICY: "检查部署模式和服务地址，确保它们位于本机或明确允许的局域网范围内。",
};

function normalizeDiagnosticDate(value?: string | null): Date | null {
  if (!value) return null;
  const trimmed = value.trim();
  const withTimeSeparator = trimmed.includes("T") ? trimmed : trimmed.replace(" ", "T");
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(withTimeSeparator) ? withTimeSeparator : `${withTimeSeparator}Z`;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.valueOf()) ? null : parsed;
}

export function isDiagnosticRunStale(createdAt?: string | null, now = Date.now()): boolean {
  const parsed = normalizeDiagnosticDate(createdAt);
  return parsed ? now - parsed.valueOf() > STALE_AFTER_MS : true;
}

export function formatDiagnosticRunTime(value?: string | null): string {
  const parsed = normalizeDiagnosticDate(value);
  if (!parsed) return "检查时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

export function diagnosticStatusLabel(status: string): string {
  return DIAGNOSTIC_STATUS_LABELS[String(status).toUpperCase()] ?? "状态未知";
}

export function diagnosticCategoryLabel(category?: string): string {
  return DIAGNOSTIC_CATEGORY_LABELS[String(category ?? "").toLowerCase()] ?? "其他检查";
}

export function diagnosticCheckTitle(code: string): string {
  return DIAGNOSTIC_CHECKS[code]?.title ?? "其他环境检查";
}

export function diagnosticCheckDescription(code: string): string {
  return DIAGNOSTIC_CHECKS[code]?.description ?? "检查本机运行环境是否满足当前生产需要。";
}

export function diagnosticRemediationText(code: string, fallback: string): string {
  return DIAGNOSTIC_REMEDIATIONS[code] ?? fallback;
}

function diagnosticModeLabel(value: unknown): string {
  if (value === "LOCAL_ONLY") return "仅本机";
  if (value === "LAN_SERVICE") return "可信局域网";
  return String(value ?? "未知");
}

function diagnosticReasonLabel(value: unknown): string {
  const labels: Record<string, string> = {
    not_found: "没有找到程序或文件",
    access_disabled: "访问已关闭",
    base_url_missing: "尚未配置服务地址",
    runtime_endpoint_rejected: "服务地址不在允许范围内",
    ConnectionRefusedError: "服务没有启动或拒绝连接",
    TimeoutError: "连接超时",
    nvidia_smi_not_found: "无法读取实时显卡信息，已使用本机清单",
  };
  return labels[String(value)] ?? String(value ?? "原因未知");
}

function formatDiagnosticBytes(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未知";
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return "未知";
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function diagnosticSourceLabel(value: unknown): string {
  if (value === "NVIDIA_SMI") return "实时驱动";
  if (value === "MODEL_INVENTORY") return "本机清单";
  return "";
}

export function diagnosticObservedSummary(code: string, observed: Record<string, unknown>): string {
  switch (code) {
    case "MODE_LOCAL_ONLY":
      return `当前模式：${diagnosticModeLabel(observed.mode)}`;
    case "CANONICAL_MODEL_ROOT":
      return observed.path ? `模型目录：${String(observed.path)}` : "尚未找到模型目录";
    case "MANIFEST_READ_ONLY":
      return `清单版本：${String(observed.version ?? "未知")}`;
    case "DISABLED_MODEL_GUARD":
      return typeof observed.count === "number" ? `已隔离 ${observed.count} 个停用模型文件` : "停用模型数量未知";
    case "COMFYUI_LOOPBACK":
      return observed.status_code ? `ComfyUI 已响应（HTTP ${String(observed.status_code)}）` : `连接结果：${diagnosticReasonLabel(observed.reason)}`;
    case "H3_CANDIDATE_LAYOUT": {
      if (!Array.isArray(observed.missing_model_files)) return "H3 模型文件状态未知";
      const missing = observed.missing_model_files.length;
      return missing > 0 ? `缺少 ${missing} 个 H3 模型文件` : "H3 所需模型文件齐全";
    }
    case "LOCAL_LLM_LOOPBACK":
      return observed.model ? `当前模型：${String(observed.model)}；实际推理${Number(observed.probe_level_passed ?? 0) >= 4 ? "成功" : "尚未通过"}` : `检查结果：${diagnosticReasonLabel(observed.error_code ?? observed.reason)}`;
    case "LOCAL_EXECUTOR": {
      const queued = Number(observed.queued_count ?? 0);
      const workers = Number(observed.active_worker_count ?? 0);
      const active = Number(observed.active_attempt_count ?? 0);
      return queued > 0 && workers === 0 ? `有 ${queued} 个任务排队，但没有可用执行器` : `执行器 ${workers} 个；排队 ${queued} 个；正在处理 ${active} 个`;
    }
    case "FFMPEG":
    case "FFPROBE": {
      if (!observed.version) return `检查结果：${diagnosticReasonLabel(observed.reason)}`;
      const version = String(observed.version).match(/version\s+([^\s]+)/i)?.[1] ?? "版本未知";
      return `已安装 ${code === "FFMPEG" ? "FFmpeg" : "FFprobe"} ${version}`;
    }
    case "DISK_SPACE":
      return `剩余 ${formatDiagnosticBytes(observed.free_bytes)}，总容量 ${formatDiagnosticBytes(observed.total_bytes)}`;
    case "GPU_MANIFEST": {
      const source = diagnosticSourceLabel(observed.source);
      return `${String(observed.name ?? "未识别显卡")}；显存 ${formatDiagnosticBytes(observed.total_bytes)}${source ? `；来源：${source}` : ""}`;
    }
    case "GPU_DRIVER_CUDA":
      return `驱动 ${String(observed.driver ?? "未记录")}；CUDA ${String(observed.cuda ?? "未识别")}；计算支持${observed.cuda_available === true ? "可用" : "不可用"}`;
    case "COMFYUI_NODE_REGISTRY": {
      if (!Array.isArray(observed.missing_capability_nodes)) return "生成工作流节点状态未知";
      const count = typeof observed.capability_count === "number" ? `已登记 ${observed.capability_count} 类能力` : "能力登记数量未知";
      return observed.missing_capability_nodes.length > 0 ? `${count}；仍缺少：${observed.missing_capability_nodes.join("、")}` : `${count}，节点齐全`;
    }
    case "MODEL_INVENTORY_HASH": {
      if (!Array.isArray(observed.missing_hashes)) return "模型完整性状态未知";
      const count = typeof observed.entry_count === "number" ? `共 ${observed.entry_count} 个模型组件` : "模型组件数量未知";
      const missing = observed.missing_hashes.length;
      return missing > 0 ? `${count}，其中 ${missing} 个缺少校验指纹` : `${count}，校验指纹齐全`;
    }
    case "NETWORK_POLICY":
      return `当前范围：${diagnosticModeLabel(observed.network_mode ?? observed.release_mode)}；公网访问${observed.public_network === true ? "已开启" : "未开启"}`;
    case "REMOTE_PROVIDER":
      return observed.automatic_remote_fallback === false ? "不会自动切换到远程或云端服务" : "已经允许自动切换到远程服务";
    default:
      return "已完成本项环境检查；可展开查看技术数据。";
  }
}

function isPassed(status: string): boolean {
  return PASS_STATUSES.has(String(status).toUpperCase());
}

function statusClass(status: string): string {
  return `status-${String(status).toLowerCase()}`;
}

function TechnicalDetails({ check }: { check: DiagnosticRun["checks"][number] }) {
  return (
    <details className="diagnostic-technical">
      <summary>技术详情</summary>
      <div className="diagnostic-technical__code"><span>检查代码</span><code>{check.code}</code></div>
      <pre>{JSON.stringify(check.observed, null, 2)}</pre>
    </details>
  );
}

function AttentionCheck({ check }: { check: DiagnosticRun["checks"][number] }) {
  const fallback = typeof check.remediation?.action === "string" ? check.remediation.action : "处理后重新运行本机检查。";
  return (
    <article className={`diagnostic-issue ${statusClass(check.status)}`}>
      <header>
        <div>
          <span>{diagnosticCategoryLabel(check.category)}</span>
          <h4>{diagnosticCheckTitle(check.code)}</h4>
        </div>
        <strong>{diagnosticStatusLabel(check.status)}</strong>
      </header>
      <p>{diagnosticCheckDescription(check.code)}</p>
      <div className="diagnostic-issue__fact"><span>当前结果</span><strong>{diagnosticObservedSummary(check.code, check.observed)}</strong></div>
      <p className="diagnostic-issue__action"><strong>下一步：</strong>{diagnosticRemediationText(check.code, fallback)}</p>
      <TechnicalDetails check={check} />
    </article>
  );
}

export function DiagnosticsOverview({
  run,
  running,
  onRun,
}: {
  run: DiagnosticRun | null;
  running: boolean;
  onRun: () => void;
}) {
  if (!run) {
    return (
      <section className="panel diagnostics-empty" aria-labelledby="diagnostics-empty-title">
        <StudioIcon name="activity" />
        <div>
          <h3 id="diagnostics-empty-title">还没有环境检查记录</h3>
          <p>运行一次只读检查，确认生成服务、语言模型、显卡、媒体工具、存储和网络边界。</p>
        </div>
        <button type="button" className="primary-action" onClick={onRun} disabled={running}>{running ? "检查中…" : "运行环境检查"}</button>
      </section>
    );
  }

  const attention = run.checks.filter((check) => !isPassed(check.status));
  const passed = run.checks.filter((check) => isPassed(check.status));
  const stale = isDiagnosticRunStale(run.created_at);
  const failed = attention.some((check) => ["FAIL", "BLOCKED"].includes(String(check.status).toUpperCase()));
  const heading = attention.length === 0 ? "生产环境可用" : failed ? `有 ${attention.length} 项影响生产` : `有 ${attention.length} 项需要留意`;
  const groups = CATEGORY_ORDER.map((category) => ({
    category,
    items: passed.filter((check) => String(check.category ?? "").toLowerCase() === category),
  })).filter((group) => group.items.length > 0);

  return (
    <div className="diagnostics-overview">
      <section className={`panel diagnostics-summary ${attention.length === 0 ? "is-healthy" : failed ? "is-blocked" : "is-attention"}`} aria-labelledby="diagnostics-summary-title">
        <div className="diagnostics-summary__icon" aria-hidden="true"><StudioIcon name={attention.length === 0 ? "check-circle" : "activity"} /></div>
        <div className="diagnostics-summary__copy">
          <p className="eyebrow">最近一次只读检查</p>
          <h3 id="diagnostics-summary-title">{heading}</h3>
          <p>{attention.length === 0 ? `${passed.length} 项生产依赖均已通过。` : `${attention.length} 项需要处理，另有 ${passed.length} 项已通过。`}</p>
          <time dateTime={run.created_at}>{formatDiagnosticRunTime(run.created_at)}</time>
        </div>
        <button type="button" className="primary-action" onClick={onRun} disabled={running}>{running ? "检查中…" : "重新检查"}</button>
        {stale ? <p className="diagnostics-summary__stale">这份结果已超过 15 分钟，服务或存储状态可能已经变化。</p> : null}
      </section>

      {running ? <p className="diagnostic-run-progress" role="status">正在检查本机服务与生产依赖，通常需要 10–60 秒。页面会保留上一次结果。</p> : null}

      {attention.length > 0 ? (
        <section className="diagnostics-attention" aria-labelledby="diagnostics-attention-title">
          <div className="diagnostics-section-heading">
            <div><p className="eyebrow">优先处理</p><h3 id="diagnostics-attention-title">需要处理的事项</h3></div>
            <span>{attention.length} 项</span>
          </div>
          <div className="diagnostics-attention__grid">{attention.map((check) => <AttentionCheck key={check.code} check={check} />)}</div>
        </section>
      ) : null}

      <details className="panel diagnostics-evidence">
        <summary>
          <span><strong>已通过的检查</strong><small>按生产环节归类，仅在需要核对证据时展开</small></span>
          <span>{passed.length} 项</span>
        </summary>
        <div className="diagnostics-evidence__groups">
          {groups.map((group) => (
            <section key={group.category} aria-label={diagnosticCategoryLabel(group.category)}>
              <h4>{diagnosticCategoryLabel(group.category)}</h4>
              <div className="diagnostics-evidence__list">
                {group.items.map((check) => (
                  <article key={check.code}>
                    <StudioIcon name="check-circle" />
                    <div><strong>{diagnosticCheckTitle(check.code)}</strong><p>{diagnosticObservedSummary(check.code, check.observed)}</p></div>
                    <TechnicalDetails check={check} />
                  </article>
                ))}
              </div>
            </section>
          ))}
        </div>
      </details>
    </div>
  );
}
