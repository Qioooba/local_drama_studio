import { requestJson, type HealthCheck } from "../../generated/api";

export const runtimeHealthQueryKeys = {
  live: ["system-health", "live"] as const,
  ready: ["system-health", "ready"] as const,
  dependencies: ["system-health", "dependencies"] as const,
};

export async function loadRuntimeHealth(path: "live" | "ready" | "dependencies"): Promise<HealthCheck> {
  return requestJson<HealthCheck>(`/api/v1/health/${path}`);
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
