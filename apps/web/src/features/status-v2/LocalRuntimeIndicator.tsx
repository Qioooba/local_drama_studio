import { useEffect, useRef, useState } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
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

function pollIsHealthy(ready: UseQueryResult<HealthCheck, Error>, dependencies: UseQueryResult<HealthCheck, Error>): boolean {
  if (ready.isError || dependencies.isError) return false;
  const dependencyChecks = dependencies.data?.checks ?? {};
  return ready.data?.status === "HEALTHY"
    && dependencies.data?.status === "HEALTHY"
    && dependencyChecks.ffmpeg === "discovered"
    && dependencyChecks.database === "ok"
    && dependencyChecks.comfy_designer === "ready"
    && (dependencyChecks.worker_supervisor ?? "").startsWith("ready:")
    && dependencyChecks.production_profiles !== "not_synced";
}

const DEGRADE_AFTER_CONSECUTIVE_FAILURES = 2;

export function LocalRuntimeIndicator() {
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const summaryRef = useRef<HTMLElement>(null);
  const ready = useQuery({ queryKey: ["system-health", "ready"], queryFn: () => loadHealth("ready"), refetchInterval: 30_000 });
  const dependencies = useQuery({ queryKey: ["system-health", "dependencies"], queryFn: () => loadHealth("dependencies"), refetchInterval: 30_000 });
  const [failureStreak, setFailureStreak] = useState(0);
  const [hasEverBeenHealthy, setHasEverBeenHealthy] = useState(false);
  const loading = ready.isPending || dependencies.isPending;
  const pollHealthy = pollIsHealthy(ready, dependencies);
  // Identify each completed poll round so the streak counts polls, not renders.
  const pollStamp = `${ready.dataUpdatedAt}:${dependencies.dataUpdatedAt}:${ready.errorUpdateCount}:${dependencies.errorUpdateCount}`;
  useEffect(() => {
    if (loading) return;
    if (pollHealthy) {
      setHasEverBeenHealthy(true);
      setFailureStreak(0);
      return;
    }
    setFailureStreak((current) => current + 1);
  }, [loading, pollHealthy, pollStamp]);
  // A busy runtime (long H3 decodes block its HTTP loop) makes a single probe
  // time out; degrading the pill on that first blip read as "需处理" while the
  // GPU was working. Suppress one-off failures after an established healthy
  // baseline, but never on cold start where the first impression must be true.
  const degraded = !loading && failureStreak > 0 && !(hasEverBeenHealthy && failureStreak < DEGRADE_AFTER_CONSECUTIVE_FAILURES);
  const failed = degraded && (ready.isError || dependencies.isError);
  const state = loading ? "checking" : !degraded ? "healthy" : "degraded";
  const label = loading ? "正在检查本机环境" : !degraded ? "本机生产环境正常" : failed ? "本机状态读取失败" : "本机生产环境需处理";
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
      <div><strong>本机生产环境</strong><span>{!degraded ? "所有核心依赖可用" : "生产前请处理异常项"}</span></div>
      {failed && <p role="alert">{ready.error instanceof Error ? ready.error.message : dependencies.error instanceof Error ? dependencies.error.message : "无法读取状态"}</p>}
      <dl>{Object.entries(checks).map(([key, value]) => <div key={key}><dt>{CHECK_LABELS[key] ?? key}</dt><dd className={["ok", "ready", "discovered", "HEALTHY", "LOCAL_ONLY", "synced_candidates"].includes(value) || value.startsWith("ready:") ? "ok" : "warning"}>{value}</dd></div>)}</dl>
      <Link to={routes.systemDiagnostics()} onClick={() => closeDetails()}>打开诊断与处置</Link>
    </div>
  </details>;
}
