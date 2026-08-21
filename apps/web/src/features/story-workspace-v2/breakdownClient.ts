async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const payload = (await response.json()) as {
        detail?: string | { message?: string };
        message?: string;
        error?: { message?: string };
      };
      message =
        payload.error?.message ??
        (typeof payload.detail === "string" ? payload.detail : payload.detail?.message) ??
        payload.message ??
        message;
    } catch {
      // Preserve the HTTP status when an upstream proxy returns a non-JSON page.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export interface LocalLLMStatus {
  status: "PASS" | "FAIL" | "BLOCKED" | "NOT_CONFIGURED";
  base_url?: string;
  model?: string | null;
  models?: string[];
  error_code?: string;
  message?: string;
}

export interface BreakdownSubmission {
  job: Job;
  automatic_apply: false;
  requires_human_action: true;
}

export async function getLocalLLMStatus(): Promise<{ status: LocalLLMStatus }> {
  return requestJson("/api/v1/local-llm/status");
}

export async function syncLocalLLMProfile(
  model?: string
): Promise<{ profile: { profile_version_id: string; profile_code: string; status: string } }> {
  return requestJson("/api/v1/local-llm/profile:sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(model ? { model } : {}),
  });
}

export async function publishLocalLLMProfile(
  profileVersionId: string
): Promise<{ profile: { profile_version_id: string; status: string } }> {
  return requestJson("/api/v1/local-llm/profile:publish", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile_version_id: profileVersionId }),
  });
}

export async function requestScriptBreakdown(
  sessionId: string,
  profileVersionId: string,
  idempotencyKey: string
): Promise<BreakdownSubmission> {
  return requestJson(`/api/v1/import-sessions/${encodeURIComponent(sessionId)}:request-breakdown`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ profile_version_id: profileVersionId }),
  });
}
import type { Job } from "../../generated/api";
