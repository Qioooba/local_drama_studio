import {
  ApiRequestError,
  requestJson,
  type ProductionSession as GeneratedProductionSession,
  type ProductionSessionChoice as GeneratedProductionSessionChoice,
  type ProductionSessionPlan as GeneratedProductionSessionPlan,
  type ProductionSessionPlanCommand as GeneratedProductionSessionPlanCommand,
  type ProductionSessionReviewItem as GeneratedProductionSessionReviewItem,
  type ProductionSessionReviewPage as GeneratedProductionSessionReviewPage,
  type ProductionSessionStatus as GeneratedProductionSessionStatus,
} from "../../generated/api";

export type ProductionSessionStatus = GeneratedProductionSessionStatus;
export type ProductionSession = GeneratedProductionSession;
export type ProductionPlan = GeneratedProductionSessionPlan;
export type ProductionChoice = GeneratedProductionSessionChoice;
export type ProductionReviewItem = GeneratedProductionSessionReviewItem;
export type ProductionReviewPage = GeneratedProductionSessionReviewPage;
export type PlanCommand = GeneratedProductionSessionPlanCommand;
const commandKey = (prefix: string) => `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;

export async function planProductionSession(projectId: string, payload: PlanCommand) {
  return requestJson<{ plan: ProductionPlan }>(`/api/v2/projects/${encodeURIComponent(projectId)}/production-sessions:plan`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
}

export async function createProductionSession(projectId: string, payload: PlanCommand & { expected_plan_hash: string }, idempotencyKey?: string) {
  return requestJson<{ session: ProductionSession }>(`/api/v2/projects/${encodeURIComponent(projectId)}/production-sessions`, {
    method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey ?? commandKey("create-production") }, body: JSON.stringify(payload),
  });
}

export async function startProductionSession(session: ProductionSession, idempotencyKey?: string) {
  return requestJson<{ session: ProductionSession }>(`/api/v2/production-sessions/${encodeURIComponent(session.id)}:start`, {
    method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey ?? commandKey("start-production") }, body: JSON.stringify({ expected_revision: session.revision }),
  });
}

/**
 * FE-12: a created session whose `start` failed must be recoverable through its own
 * id, not by creating another session. The backend lists START among the legal
 * actions for READY and `:start` handles READY explicitly, so a retry first reads
 * the original session and reports which of the three recovery cases applies.
 */
export type SessionStartRecovery =
  | { state: "STARTED"; session: ProductionSession }
  | { state: "MISSING"; sessionId: string }
  | { state: "TERMINAL"; session: ProductionSession }
  | { state: "NOT_STARTABLE"; session: ProductionSession }
  | { state: "READY"; session: ProductionSession }
  | { state: "REVISION_CHANGED"; session: ProductionSession };

const TERMINAL_SESSION_STATUSES = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

export function classifySessionStartRecovery(session: ProductionSession): SessionStartRecovery {
  const status = String(session.status);
  if (TERMINAL_SESSION_STATUSES.has(status)) return { state: "TERMINAL", session };
  if (status === "READY" || (session.allowed_actions ?? []).includes("START")) return { state: "READY", session };
  return { state: "NOT_STARTABLE", session };
}

/**
 * Reads the original session before deciding anything. A session that is already
 * running is reported as STARTED, a terminal one is reported as TERMINAL, and only
 * a session that still allows START is started again — reusing the caller's original
 * operation key so a retry of the same user action cannot fork a second production.
 */
export async function resolveSessionStart(sessionId: string, idempotencyKey: string, expectedRevision?: number): Promise<SessionStartRecovery> {
  const current = await getProductionSession(sessionId);
  if (!current) return { state: "MISSING", sessionId };
  const classified = classifySessionStartRecovery(current);
  if (classified.state !== "READY") return classified;
  if (expectedRevision !== undefined && current.revision !== expectedRevision) return { state: "REVISION_CHANGED", session: current };
  const started = await startProductionSession(current, idempotencyKey);
  return { state: "STARTED", session: started.session };
}

export async function controlProductionSession(session: ProductionSession, action: "pause" | "resume" | "cancel") {
  return requestJson<{ session: ProductionSession }>(`/api/v2/production-sessions/${encodeURIComponent(session.id)}:${action}`, {
    method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": commandKey(`${action}-production`) }, body: JSON.stringify({ expected_revision: session.revision }),
  });
}

export async function extendProductionSessionBudget(
  session: ProductionSession,
  limits: Partial<Pick<PlanCommand, "max_duration_seconds" | "max_new_jobs" | "max_attempts_total" | "max_output_bytes" | "max_queued_gpu_jobs" | "dispatch_shots_per_tick">>,
) {
  return requestJson<{ session: ProductionSession }>(`/api/v2/production-sessions/${encodeURIComponent(session.id)}:extend-budget`, {
    method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": commandKey("extend-production-budget") },
    body: JSON.stringify({ expected_revision: session.revision, ...limits }),
  });
}

export async function listProductionSessions(projectId: string, page: { cursor?: number; limit?: number; status?: string } = {}) {
  const query = new URLSearchParams({ cursor: String(page.cursor ?? 0), limit: String(page.limit ?? 50) });
  if (page.status) query.set("status", page.status);
  return requestJson<ProductionSessionPage>(`/api/v2/projects/${encodeURIComponent(projectId)}/production-sessions?${query.toString()}`);
}

/** Bounded session page with the server metadata preserved for paging and totals. */
export type ProductionSessionPage = {
  items: ProductionSession[];
  cursor: number;
  limit: number;
  total: number;
  next_cursor: number | null;
};

/** Read one session by id. Returns null when the backend no longer has it. */
export async function getProductionSession(sessionId: string): Promise<ProductionSession | null> {
  try {
    const { session } = await requestJson<{ session: ProductionSession }>(`/api/v2/production-sessions/${encodeURIComponent(sessionId)}`);
    return session;
  } catch (error) {
    if (error instanceof ApiRequestError && error.status === 404) return null;
    throw error;
  }
}

export async function getProductionSessionReview(sessionId: string, page: { cursor?: number; limit?: number } = {}) {
  const query = new URLSearchParams({ cursor: String(page.cursor ?? 0), limit: String(page.limit ?? 100) });
  return requestJson<ProductionReviewPage>(`/api/v2/production-sessions/${encodeURIComponent(sessionId)}/review?${query.toString()}`);
}

export async function rerollProductionChoice(session: ProductionSession, choice: ProductionChoice) {
  return requestJson<{ session: ProductionSession }>(`/api/v2/production-sessions/${encodeURIComponent(session.id)}/choices/${encodeURIComponent(choice.id)}:reroll`, {
    method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": commandKey("reroll-production") },
    body: JSON.stringify({ expected_session_revision: session.revision, expected_choice_revision: choice.revision }),
  });
}

export async function confirmProductionEpisode(session: ProductionSession, item: ProductionReviewItem) {
  const approvals = item.choices.map((choice) => ({ production_choice_id: choice.id, review_decision_id: choice.available_human_approval_id }));
  return requestJson<{ session: ProductionSession }>(`/api/v2/production-sessions/${encodeURIComponent(session.id)}/episodes/${encodeURIComponent(item.episode_id)}:confirm`, {
    method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": commandKey("confirm-production") },
    body: JSON.stringify({ expected_revision: session.revision, approvals }),
  });
}

export async function retryProductionSessionItem(
  session: ProductionSession,
  item: ProductionReviewItem,
  strategy = item.repair_plan.recommended_strategy,
) {
  if (!strategy || !item.repair_plan.can_retry_now) throw new Error("当前返工计划仍有前置处理，不能直接重试");
  return requestJson<{ session: ProductionSession }>(`/api/v2/production-sessions/${encodeURIComponent(session.id)}/items/${encodeURIComponent(item.session_item_id)}:retry`, {
    method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": commandKey("retry-production-item") },
    body: JSON.stringify({ expected_session_revision: session.revision, expected_item_revision: item.item_revision, strategy }),
  });
}
