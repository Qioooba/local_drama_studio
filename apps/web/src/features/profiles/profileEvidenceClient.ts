import { bootstrapLocalSession } from "../../generated/api";

export type I2VEvidenceProbePlan = {
  status: "READY" | "BLOCKED";
  blockers: string[];
  plan_hash: string;
  snapshot: {
    approved_keyframe: { media_version_id: string } | null;
    workflow: { id: string; content_hash: string } | null;
    candidate_profile: { id: string; execution_fingerprint: string } | null;
    semantic_inputs: Record<string, unknown>;
  };
  confirmation_required: true;
};

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  let secured = init;
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
    const session = await bootstrapLocalSession();
    const headers = new Headers(init?.headers);
    headers.set("X-Local-Instance-Token", session.token);
    secured = { ...init, headers };
  }
  const response = await fetch(path, secured);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json() as { error?: { message?: string }; detail?: string | { message?: string } };
      message = payload.error?.message
        ?? (typeof payload.detail === "string" ? payload.detail : payload.detail?.message)
        ?? message;
    } catch {
      // Keep the HTTP status when a proxy returns non-JSON.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export function planI2VEvidenceProbe(projectId: string, profileVersionId: string, workflowVersionId: string) {
  const query = new URLSearchParams({
    profile_version_id: profileVersionId,
    workflow_version_id: workflowVersionId,
  });
  return requestJson<{ plan: I2VEvidenceProbePlan }>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/gates/g6/i2v-probe-plan?${query}`,
  );
}

export function validateProfileEvidenceCompatibility(profileVersionId: string) {
  return requestJson<{ compatibility: { id: string; status: "PASS" | "FAIL"; checks: Array<{ code: string; passed: boolean }> } }>(
    `/api/v1/profile-versions/${encodeURIComponent(profileVersionId)}:validate-compatibility`,
    { method: "POST" },
  );
}

export function submitI2VEvidenceProbe(projectId: string, profileVersionId: string, workflowVersionId: string, planHash: string) {
  return requestJson<{ job: { id: string; state: string }; plan: I2VEvidenceProbePlan }>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/gates/g6/i2v-probe:submit`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        profile_version_id: profileVersionId,
        workflow_version_id: workflowVersionId,
        plan_hash: planHash,
        idempotency_key: globalThis.crypto?.randomUUID?.() ?? `i2v-evidence-${Date.now()}`,
      }),
    },
  );
}

export function finalizeI2VEvidenceProbe(projectId: string, jobId: string) {
  return requestJson<{ media: { media_version_id: string }; profile_version: { id: string; version_no: number; status: string } }>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/gates/g6/i2v-probe:finalize`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId }),
    },
  );
}

export type T2IEvidenceProbePlan = {
  status: "READY" | "BLOCKED";
  blockers: string[];
  plan_hash: string;
  snapshot: {
    workflow: { id: string; content_hash: string } | null;
    candidate_profile: { id: string; execution_fingerprint: string } | null;
    semantic_inputs: Record<string, unknown>;
  };
  confirmation_required: true;
};

export function planT2IEvidenceProbe(projectId: string, profileVersionId: string, workflowVersionId: string) {
  const query = new URLSearchParams({
    profile_version_id: profileVersionId,
    workflow_version_id: workflowVersionId,
  });
  return requestJson<{ plan: T2IEvidenceProbePlan }>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/gates/g6/t2i-probe-plan?${query}`,
  );
}

export function submitT2IEvidenceProbe(projectId: string, profileVersionId: string, workflowVersionId: string, planHash: string) {
  return requestJson<{ job: { id: string; state: string }; plan: T2IEvidenceProbePlan }>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/gates/g6/t2i-probe:submit`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        profile_version_id: profileVersionId,
        workflow_version_id: workflowVersionId,
        plan_hash: planHash,
        idempotency_key: globalThis.crypto?.randomUUID?.() ?? `t2i-evidence-${Date.now()}`,
      }),
    },
  );
}

export function finalizeT2IEvidenceProbe(projectId: string, jobId: string) {
  return requestJson<{ media: { media_version_id: string }; profile_version: { id: string; version_no: number; status: string } }>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/gates/g6/t2i-probe:finalize`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId }),
    },
  );
}
