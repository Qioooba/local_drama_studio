import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError } from "../../generated/api";import {
  classifySessionStartRecovery,
  createProductionSession,
  getProductionSession,
  resolveSessionStart,
  startProductionSession,
  type PlanCommand,
  type ProductionSession,
} from "./client";

const requestJson = vi.fn();
vi.mock("../../generated/api", async () => {
  const actual = await vi.importActual<typeof import("../../generated/api")>("../../generated/api");
  return { ...actual, requestJson: (...args: unknown[]) => requestJson(...args) };
});

const apiError = (status: number, code: string, message: string) =>
  new ApiRequestError(message, status, code, null, false, null, null);

const command: PlanCommand = {
  scope_type: "WHOLE_DRAMA", episode_ids: [], production_mode: "BALANCED", checkpoint_policy: "ON_EXCEPTION",
  tts_enabled: true, max_parallel_episodes: 1, min_free_disk_bytes: 1, max_duration_seconds: 1,
  max_new_jobs: 1, max_attempts_total: 1, max_output_bytes: 1, max_queued_gpu_jobs: 1, dispatch_shots_per_tick: 1,
};

const sessionFor = (overrides: Partial<ProductionSession>): ProductionSession => ({
  id: "session-1", project_id: "project-1", scope_type: "WHOLE_DRAMA", production_mode: "BALANCED",
  checkpoint_policy: "ON_EXCEPTION", status: "READY", current_stage: "STAGE", plan_hash: "a".repeat(64),
  configuration: {}, counters: {}, item_count: 0, last_error_code: null, last_error_message: null,
  started_at: null, finished_at: null, created_at: "now", updated_at: "now", created_by: "local-user",
  revision: 1, allowed_actions: ["START", "PAUSE", "CANCEL"],
  budget: { limits: {}, usage: {}, remaining: {}, hard_blockers: [], resource_wait: null, can_dispatch: true, observed_at: "now" },
  ...overrides,
} as ProductionSession);

const keyOf = (call: unknown[] | undefined) => new Headers((call?.[1] as RequestInit | undefined)?.headers).get("Idempotency-Key");

beforeEach(() => requestJson.mockReset());

describe("production session idempotency keys", () => {
  it("keeps one key across create retries and rotates it for a different payload", async () => {
    requestJson.mockResolvedValue({ session: sessionFor({}) });
    await createProductionSession("project-1", { ...command, expected_plan_hash: "p1" }, "create-key-1");
    await createProductionSession("project-1", { ...command, expected_plan_hash: "p1" }, "create-key-1");
    await createProductionSession("project-1", { ...command, expected_plan_hash: "p2" }, "create-key-2");
    expect(requestJson.mock.calls.map((call) => keyOf(call as unknown[]))).toEqual(["create-key-1", "create-key-1", "create-key-2"]);
  });

  it("sends the explicit start key so a retry replays the original operation", async () => {
    requestJson.mockResolvedValue({ session: sessionFor({ status: "RUNNING", revision: 2 }) });
    await startProductionSession(sessionFor({}), "start-key-1");
    expect(keyOf(requestJson.mock.calls[0] as unknown[])).toBe("start-key-1");
    expect(JSON.parse(String((requestJson.mock.calls[0]?.[1] as RequestInit).body))).toEqual({ expected_revision: 1 });
  });
});

describe("session start recovery", () => {
  it("classifies the three recovery cases without starting anything by itself", () => {
    expect(classifySessionStartRecovery(sessionFor({ status: "READY" })).state).toBe("READY");
    expect(classifySessionStartRecovery(sessionFor({ status: "RUNNING", allowed_actions: ["PAUSE"] })).state).toBe("NOT_STARTABLE");
    expect(classifySessionStartRecovery(sessionFor({ status: "COMPLETED", allowed_actions: [] })).state).toBe("TERMINAL");
    expect(classifySessionStartRecovery(sessionFor({ status: "FAILED", allowed_actions: ["CANCEL"] })).state).toBe("TERMINAL");
  });

  it("starts the original READY session with the caller's key", async () => {
    requestJson
      .mockResolvedValueOnce({ session: sessionFor({ status: "READY" }) })
      .mockResolvedValueOnce({ session: sessionFor({ status: "RUNNING", revision: 2 }) });
    const outcome = await resolveSessionStart("session-1", "start-key-1", 1);
    expect(outcome.state).toBe("STARTED");
    expect(requestJson.mock.calls.map((call) => (call as unknown[])[0])).toEqual([
      "/api/v2/production-sessions/session-1",
      "/api/v2/production-sessions/session-1:start",
    ]);
    expect(keyOf(requestJson.mock.calls[1] as unknown[])).toBe("start-key-1");
  });

  it("reports an already running session instead of starting it twice", async () => {
    requestJson.mockResolvedValueOnce({ session: sessionFor({ status: "RUNNING", revision: 5, allowed_actions: ["PAUSE"] }) });
    const outcome = await resolveSessionStart("session-1", "start-key-1", 1);
    expect(outcome.state).toBe("NOT_STARTABLE");
    expect(requestJson).toHaveBeenCalledTimes(1);
  });

  it("reports a revision change so the operator re-confirms", async () => {
    requestJson.mockResolvedValueOnce({ session: sessionFor({ status: "READY", revision: 7 }) });
    const outcome = await resolveSessionStart("session-1", "start-key-1", 1);
    expect(outcome).toEqual({ state: "REVISION_CHANGED", session: expect.objectContaining({ revision: 7 }) });
    expect(requestJson).toHaveBeenCalledTimes(1);
  });

  it("reports a deleted session as MISSING instead of silently creating another", async () => {
    requestJson.mockRejectedValueOnce(apiError(404, "PRODUCTION_SESSION_NOT_FOUND", "生产会话不存在"));
    await expect(getProductionSession("session-1")).resolves.toBeNull();
  });

  it("fails closed on any other read error", async () => {
    requestJson.mockRejectedValueOnce(apiError(500, "INTERNAL", "boom"));
    await expect(getProductionSession("session-1")).rejects.toThrow("boom");
  });
});
