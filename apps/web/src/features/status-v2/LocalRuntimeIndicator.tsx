import { useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { StatusDotIcon } from "../../components/icons";
import type { HealthCheck } from "../../generated/api";
import "./local-runtime-indicator.css";

export async function loadHealth(path: "ready" | "dependencies"): Promise<HealthCheck> {
  const response = await fetch(`/api/v1/health/${path}`);
  if (!response.ok) throw new Error(`健康检查失败（HTTP ${response.status}）`);
  return response.json() as Promise<HealthCheck>;
}

const CHECK_LABELS: Record<string, string> = {
  mode: "运行模式",
  data_root: "数据目录",
  projects_root: "项目目录",
  work_root: "工作目录",
  cache_root: "缓存目录",
  database: "数据库",
  ffmpeg: "FFmpeg",
  comfy_designer: "ComfyUI",
  production_profiles: "生产 Profile",
  worker_supervisor: "本机 Worker",
  network_scope: "网络范围",
};

export function LocalRuntimeIndicator() {
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const summaryRef = useRef<HTMLElement>(null);
  const ready = useQuery({ queryKey: ["system-health", "ready"], queryFn: () => loadHealth("ready"), refetchInterval: 30_000 });
  const dependencies = useQuery({ queryKey: ["system-health", "dependencies"], queryFn: () => loadHealth("dependencies"), refetchInterval: 30_000 });
  const loading = ready.isPending || dependencies.isPending;
  const dependencyChecks = dependencies.data?.checks ?? {};
  const healthy = ready.data?.status === "HEALTHY"
    && dependencies.data?.status === "HEALTHY"
    && dependencyChecks.ffmpeg === "discovered"
    && dependencyChecks.database === "ok"
    && dependencyChecks.comfy_designer === "ready"
    && dependencyChecks.worker_supervisor?.startsWith("ready:")
    && dependencyChecks.production_profiles !== "not_synced";
  const failed = ready.isError || dependencies.isError;
  const state = loading ? "checking" : healthy ? "healthy" : "degraded";
  const label = loading ? "正在检查本机环境" : healthy ? "本机生产环境正常" : failed ? "本机状态读取失败" : "本机生产环境需处理";
  const checks = { ...(ready.data?.checks ?? {}), ...(dependencies.data?.checks ?? {}) };

  const closeDetails = () => {
    if (detailsRef.current) detailsRef.current.open = false;
    summaryRef.current?.focus();
  };

  return <details ref={detailsRef} className={`local-runtime-indicator ${state}`}>
    <summary ref={summaryRef} aria-label={`${label}，展开查看详情`} onKeyDown={(event) => {
      if (event.key !== "Escape" || !detailsRef.current?.open) return;
      event.preventDefault();
      closeDetails();
    }}><StatusDotIcon />{label}</summary>
    <div className="local-runtime-popover">
      <div><strong>本机生产环境</strong><span>{healthy ? "所有核心依赖可用" : "生产前请处理异常项"}</span></div>
      {failed && <p role="alert">{ready.error instanceof Error ? ready.error.message : dependencies.error instanceof Error ? dependencies.error.message : "无法读取状态"}</p>}
      {!failed && <dl>{Object.entries(checks).map(([key, value]) => <div key={key}><dt>{CHECK_LABELS[key] ?? key}</dt><dd className={["ok", "ready", "discovered", "HEALTHY", "LOCAL_ONLY", "synced_candidates"].includes(value) || value.startsWith("ready:") ? "ok" : "warning"}>{value}</dd></div>)}</dl>}
      <Link to="/diagnostics" onClick={() => closeDetails()}>打开诊断与处置</Link>
    </div>
  </details>;
}
