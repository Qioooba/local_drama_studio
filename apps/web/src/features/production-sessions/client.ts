import {
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

export async function createProductionSession(projectId: string, payload: PlanCommand & { expected_plan_hash: string }) {
  return requestJson<{ session: ProductionSession }>(`/api/v2/projects/${encodeURIComponent(projectId)}/production-sessions`, {
    method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": commandKey("create-production") }, body: JSON.stringify(payload),
  });
}

export async function startProductionSession(session: ProductionSession) {
  return requestJson<{ session: ProductionSession }>(`/api/v2/production-sessions/${encodeURIComponent(session.id)}:start`, {
    method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": commandKey("start-production") }, body: JSON.stringify({ expected_revision: session.revision }),
  });
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

export async function listProductionSessions(projectId: string) {
  return requestJson<{ items: ProductionSession[]; total: number }>(`/api/v2/projects/${encodeURIComponent(projectId)}/production-sessions?limit=50`);
}

export async function getProductionSessionReview(sessionId: string) {
  return requestJson<ProductionReviewPage>(`/api/v2/production-sessions/${encodeURIComponent(sessionId)}/review?limit=100`);
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
