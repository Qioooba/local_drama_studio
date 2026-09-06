import { requestJson as generatedRequestJson } from "../../generated/api";

export type AssetImageKind = "CHARACTER" | "SCENE" | "PROP" | "COSTUME";
export type AssetImagePlanItem = {
  asset_id: string;
  name: string;
  revision: number | null;
  has_hero: boolean;
  prompt: string;
  status: "READY" | "SKIPPED" | "BLOCKED";
  blockers: Array<{ code: string; message: string }>;
};

export type AssetImageBatchPlan = {
  project_id: string;
  asset_kind: AssetImageKind;
  capability: string;
  mode: "MISSING_ONLY";
  profile_version_id: string | null;
  plan_hash: string;
  valid: boolean;
  issues: Array<{ code: string; message: string; asset_id?: string; asset_name?: string }>;
  items: AssetImagePlanItem[];
  summary: { selected: number; ready: number; skipped: number; blocked: number; jobs: number };
};

export type AssetImageBatchItem = {
  id: string;
  asset_id: string;
  asset_name: string;
  asset_kind: AssetImageKind;
  status: "PLANNED" | "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED" | "SUPERSEDED";
  job_id: string | null;
  job_state: string | null;
  progress: { percent?: number; phase?: string; [key: string]: unknown };
  media_version_id: string | null;
  reference_id: string | null;
  error: { code: string | null; message: string | null } | null;
};

export type AssetImageBatch = {
  id: string;
  project_id: string;
  asset_kind: AssetImageKind;
  capability: string;
  profile_version_id: string;
  mode: "MISSING_ONLY";
  status: "QUEUED" | "RUNNING" | "PARTIAL_RUNNING" | "SUCCEEDED" | "PARTIAL_FAILED" | "FAILED" | "CANCELLED";
  plan_hash: string;
  created_at: string;
  updated_at: string;
  summary: { total: number; succeeded: number; superseded: number; failed: number; active: number };
  items: AssetImageBatchItem[];
  idempotent_replay?: boolean;
};

type BatchRequest = {
  asset_kind: AssetImageKind;
  asset_ids: string[];
  profile_version_id: string | null;
  mode: "MISSING_ONLY";
};

function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  return generatedRequestJson<T>(`/api/v1${path}`, init);
}

export function planAssetImageBatch(projectId: string, request: BatchRequest): Promise<{ plan: AssetImageBatchPlan }> {
  return requestJson(`/projects/${encodeURIComponent(projectId)}/asset-image-batches:plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export function submitAssetImageBatch(projectId: string, request: BatchRequest, expectedPlanHash: string, idempotencyKey: string): Promise<{ batch: AssetImageBatch }> {
  return requestJson(`/projects/${encodeURIComponent(projectId)}/asset-image-batches:submit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...request, expected_plan_hash: expectedPlanHash, idempotency_key: idempotencyKey }),
  });
}

export async function listAssetImageBatches(projectId: string, kind: AssetImageKind): Promise<AssetImageBatch[]> {
  const query = new URLSearchParams({ asset_kind: kind, limit: "5" });
  const result = await requestJson<{ items: AssetImageBatch[] }>(`/projects/${encodeURIComponent(projectId)}/asset-image-batches?${query}`, { method: "GET" });
  return result.items;
}

export function isAssetImageBatchActive(batch: AssetImageBatch | null | undefined): boolean {
  return Boolean(batch && ["QUEUED", "RUNNING", "PARTIAL_RUNNING"].includes(batch.status));
}
