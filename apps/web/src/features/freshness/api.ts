export type FreshnessScopeType = "PROJECT" | "EPISODE" | "SHOT";
export type FreshnessFactType = "VARIANT" | "FRAME_BRIDGE" | "TIMELINE";

export type FreshnessVersion = {
  entity_type: string;
  entity_id: string;
  revision: number | string | null;
};

export type FreshnessReason = {
  code: string;
  message: string;
  source_revision: number | string | null;
  current_revision: number | string | null;
  propagated_from?: string | null;
};

export type FreshnessRemediationLink = {
  rel: string;
  href: string;
  method: "GET" | "POST";
  label: string;
};

export type FreshnessItem = {
  id: string;
  fact_type: FreshnessFactType;
  status: "CURRENT" | "STALE";
  project_id: string;
  episode_id: string | null;
  shot_id: string | null;
  source: FreshnessVersion | null;
  current: FreshnessVersion | null;
  reasons: FreshnessReason[];
  remediation_links: FreshnessRemediationLink[];
};

export type ProductionFreshnessReport = {
  scope: { type: FreshnessScopeType; id: string; project_id: string; episode_id?: string | null; shot_id?: string | null };
  summary: { returned: number; stale: number; current: number; truncated: boolean };
  items: FreshnessItem[];
  audit: { read_only: true; writes_performed: 0; query_count: number; query_limit: number };
  local_only: true;
  network_contacted: false;
};

type ApiErrorBody = { error?: { message?: string }; detail?: string };

export async function getProductionFreshness(scopeType: FreshnessScopeType, scopeId: string, limit: number): Promise<ProductionFreshnessReport> {
  const segment = scopeType === "PROJECT" ? "projects" : scopeType === "EPISODE" ? "episodes" : "shots";
  const response = await fetch(`/api/v1/${segment}/${encodeURIComponent(scopeId)}/production-freshness?limit=${limit}`);
  const body = await response.json().catch(() => null) as ProductionFreshnessReport | ApiErrorBody | null;
  if (!response.ok) {
    const error = body as ApiErrorBody | null;
    throw new Error(error?.error?.message ?? error?.detail ?? `Freshness 读取失败（HTTP ${response.status}）`);
  }
  return body as ProductionFreshnessReport;
}
