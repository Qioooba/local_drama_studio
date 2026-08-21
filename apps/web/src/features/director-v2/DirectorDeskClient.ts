/** Stable hand-written Director Desk facade; kept outside generated OpenAPI output. */
import type { DirectorDeskResponse, RerollReasonCode, RerollResult } from "./types";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json() as { detail?: string | { message?: string }; message?: string; error?: { message?: string } };
      message = payload.error?.message ?? (typeof payload.detail === "string" ? payload.detail : payload.detail?.message) ?? payload.message ?? message;
    } catch {
      // Preserve the HTTP status when an upstream proxy returns a non-JSON page.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export function loadDirectorDesk(projectId: string, episodeId: string, shotId?: string) {
  const query = new URLSearchParams({ nav_radius: "25" });
  if (shotId) query.set("shot_id", shotId);
  return requestJson<DirectorDeskResponse>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/episodes/${encodeURIComponent(episodeId)}/director-desk?${query}`,
  );
}

export function selectDirectorCandidate(mediaVersionId: string, selectionType: "KEYFRAME" | "PROXY_WINNER" | "FORMAL_SELECTION") {
  return requestJson<{ selection: Record<string, unknown> }>(`/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}:select`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selection_type: selectionType }),
  });
}

export async function approveFormalCandidate(projectId: string, mediaVersionId: string) {
  const preflight = await requestJson<{ plan: { status: "READY" | "BLOCKED"; plan_hash: string; items: Array<{ blockers: string[] }> } }>(
    "/api/v1/reviews/formal-selection:preflight",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId, media_version_ids: [mediaVersionId] }),
    },
  );
  if (preflight.plan.status !== "READY") {
    const blockers = preflight.plan.items.flatMap((item) => item.blockers).join("、");
    throw new Error(blockers || "正式候选尚未通过批准与完整性检查");
  }
  return requestJson<{ result: Record<string, unknown> }>("/api/v1/reviews/formal-selection:commit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId, media_version_ids: [mediaVersionId], plan_hash: preflight.plan.plan_hash }),
  });
}

export function rerollDirectorCandidate(parentVariantId: string, reasonCode: RerollReasonCode, reasonNote?: string) {
  const idempotencyKey = globalThis.crypto?.randomUUID?.() ?? `director-reroll-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return requestJson<RerollResult>(`/api/v1/generation/variants/${encodeURIComponent(parentVariantId)}/reroll`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason_code: reasonCode, reason_note: reasonNote || undefined, idempotency_key: idempotencyKey }),
  });
}
