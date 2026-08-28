import type { HealthCheck } from "../../generated/api";

export const runtimeHealthQueryKeys = {
  ready: ["system-health", "ready"] as const,
  dependencies: ["system-health", "dependencies"] as const,
};

export async function loadRuntimeHealth(path: "ready" | "dependencies"): Promise<HealthCheck> {
  const response = await fetch(`/api/v1/health/${path}`);
  if (!response.ok) throw new Error(`健康检查失败（HTTP ${response.status}）`);
  return response.json() as Promise<HealthCheck>;
}

export function isProductionRuntimeHealthy(ready?: HealthCheck, dependencies?: HealthCheck): boolean {
  const dependencyChecks = dependencies?.checks ?? {};
  return ready?.status === "HEALTHY"
    && dependencies?.status === "HEALTHY"
    && dependencyChecks.ffmpeg === "discovered"
    && dependencyChecks.database === "ok"
    && dependencyChecks.comfy_designer === "ready"
    && (dependencyChecks.worker_supervisor ?? "").startsWith("ready:")
    && dependencyChecks.production_profiles !== "not_synced";
}
