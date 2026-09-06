import { requestJson, type ProfileVersionDetail } from "../../generated/api";

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

export function planI2VEvidenceProbe(projectId: string, profileVersionId: string, workflowVersionId: string) {
  const query = new URLSearchParams({
    profile_version_id: profileVersionId,
    workflow_version_id: workflowVersionId,
  });
  return requestJson<{ plan: I2VEvidenceProbePlan }>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/gates/g6/i2v-probe-plan?${query}`,
  );
}

export function prepareI2VEvidenceKeyframe(projectId: string, sourceMediaVersionId: string) {
  return requestJson<{
    approved_keyframe: {
      media_version_id: string;
      shot_id: string;
      approval_id: string;
      source_media_version_id: string;
      reused: boolean;
    };
  }>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/gates/g6/i2v-probe-keyframe:prepare`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_media_version_id: sourceMediaVersionId,
        confirm_review_checks: true,
      }),
    },
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
  return requestJson<{ media: { media_version_id: string }; profile_version: ProfileVersionDetail }>(
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
    source_reference: { media_version_id: string; asset_id?: string | null; asset_name?: string | null; sha256: string; byte_size: number } | null;
    reference_inputs?: Array<{ role: string; media_version_id: string; sha256: string }>;
    semantic_inputs: Record<string, unknown>;
  };
  confirmation_required: true;
};

export function planT2IEvidenceProbe(projectId: string, profileVersionId: string, workflowVersionId: string, sourceMediaVersionId?: string, references?: Record<string, string>) {
  if (references) return requestJson<{ plan: T2IEvidenceProbePlan }>(`/api/v1/projects/${encodeURIComponent(projectId)}/gates/g6/t2i-probe:plan`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile_version_id: profileVersionId, workflow_version_id: workflowVersionId, reference_media_version_ids: references }),
  });
  const query = new URLSearchParams({
    profile_version_id: profileVersionId,
    workflow_version_id: workflowVersionId,
  });
  if (sourceMediaVersionId) query.set("source_media_version_id", sourceMediaVersionId);
  return requestJson<{ plan: T2IEvidenceProbePlan }>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/gates/g6/t2i-probe-plan?${query}`,
  );
}

export function submitT2IEvidenceProbe(projectId: string, profileVersionId: string, workflowVersionId: string, planHash: string, sourceMediaVersionId?: string, references?: Record<string, string>) {
  return requestJson<{ job: { id: string; state: string }; plan: T2IEvidenceProbePlan }>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/gates/g6/t2i-probe:submit`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        profile_version_id: profileVersionId,
        workflow_version_id: workflowVersionId,
        plan_hash: planHash,
        source_media_version_id: sourceMediaVersionId || undefined,
        reference_media_version_ids: references,
        idempotency_key: globalThis.crypto?.randomUUID?.() ?? `t2i-evidence-${Date.now()}`,
      }),
    },
  );
}

export function finalizeT2IEvidenceProbe(projectId: string, jobId: string) {
  return requestJson<{ media: { media_version_id: string }; profile_version: ProfileVersionDetail }>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/gates/g6/t2i-probe:finalize`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId }),
    },
  );
}
