import { requestJson, type Job, type LocalArtifactReference } from "../../generated/api";

export type QuickGenerationMode = "TEXT_TO_IMAGE" | "TEXT_TO_VIDEO" | "TEXT_TO_IMAGE_TO_VIDEO";
export type QuickGenerationPromptTarget = "KEYFRAME" | "VIDEO";
export type QuickGenerationParameters = Record<string, string | number | boolean>;
export type QuickGenerationPreset = { id: string; name: string; capability: string; execution_profile_version_id: string; parameters: QuickGenerationParameters; favorite: boolean; model_title: string; model_version_no: number; model_status: string; revision: number };
export type QuickOutputSpec = { width: number; height: number; frame_count: number; fps: number; duration_seconds: number; target_duration_ms: number; aspect_ratio: string; source: "PUBLISHED_WORKFLOW" | "RUN_PARAMETERS"; editable: true };
export type QuickImageSpec = { width: number; height: number; aspect_ratio: string; source: "PUBLISHED_WORKFLOW" | "RUN_PARAMETERS"; editable: true };
export type QuickGenerationOutput = { id: string; run_id: string; candidate_id?: string | null; media_kind: "IMAGE" | "VIDEO"; mime_type: string; byte_size: number; sha256: string; content_url: string; thumbnail_url: string; artifact: LocalArtifactReference };
export type QuickGenerationPlan = {
  schema_version: string; mode: QuickGenerationMode; story: string; language: "zh-CN" | "en-US";
  video_plan: { title: string; video_prompt: string; keyframe_prompt: string; director_intent: Record<string, unknown>; camera_movement: string; provider: string; model: string; remote: boolean };
  result_kind: "IMAGE" | "VIDEO"; output_spec: QuickOutputSpec | QuickImageSpec; image_spec: QuickImageSpec | null; image_candidate_count: number;
  llm: { title: string; provider: string; model: string; remote: boolean; profile_version_id: string };
  image: { title: string; capability: string; profile_version_id: string; workflow_version_id: string; workflow_title: string } | null;
  video: { title: string; capability: "VIDEO_T2V" | "VIDEO_I2V"; profile_version_id: string; workflow_version_id: string; workflow_title: string } | null;
  runtime: { status: string; endpoint?: string | null }; mutations: string[]; confirmation_required: true;
  model_parameters: { llm: QuickGenerationParameters; image: QuickGenerationParameters; video: QuickGenerationParameters };
};
export type QuickGenerationCandidate = { id: string; batch_no: number; ordinal: number; state: string; seed: number; job_id?: string | null; output_id?: string | null; output?: QuickGenerationOutput | null; parent_candidate_id?: string | null; selected: boolean; error: { code?: string; message?: string }; job?: Job | null };
export type QuickGenerationRun = {
  id: string; mode: QuickGenerationMode; state: "PLANNING" | "PLANNED" | "COMMITTING" | "GENERATING" | "AWAITING_SELECTION" | "CANCELLING" | "SUCCEEDED" | "FAILED" | "CANCELLED"; stage: string;
  story: { text: string }; story_sha256: string; language: "zh-CN" | "en-US"; llm_profile_version_id: string; image_profile_version_id?: string | null; video_profile_version_id?: string | null; image_candidate_count: number; remote_outbound_confirmed: boolean;
  model_parameters: { llm: QuickGenerationParameters; image: QuickGenerationParameters; video: QuickGenerationParameters };
  plan: QuickGenerationPlan | Record<string, never>; plan_hash?: string | null; job_id?: string | null; output_id?: string | null; output?: QuickGenerationOutput | null; selected_candidate_id?: string | null; selected_image_output_id?: string | null; selected_image_output?: QuickGenerationOutput | null; seed?: number | null; retry_count: number;
  error: { code?: string; message?: string; details?: Record<string, unknown>; retryable?: boolean }; links: { workspace?: string }; job?: Job | null; candidates: QuickGenerationCandidate[]; updated_at: string;
};
export type QuickGenerationPlanInput = { story: string; mode: QuickGenerationMode; language: "zh-CN" | "en-US"; llm_profile_version_id: string; image_profile_version_id: string | null; video_profile_version_id: string | null; image_candidate_count: number; allow_remote_outbound: boolean; llm_parameters: QuickGenerationParameters; image_parameters: QuickGenerationParameters; video_parameters: QuickGenerationParameters };

const base = "/api/v1/quick-generations";
export function planQuickGeneration(input: QuickGenerationPlanInput, idempotencyKey: string, signal?: AbortSignal) { return requestJson<{ run: QuickGenerationRun }>(`${base}:plan`, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey }, body: JSON.stringify(input), signal }); }
export function getQuickGeneration(runId: string, signal?: AbortSignal) { return requestJson<{ run: QuickGenerationRun }>(`${base}/${encodeURIComponent(runId)}`, { signal }); }
export function listQuickGenerations(limit = 12, signal?: AbortSignal) { return requestJson<{ items: QuickGenerationRun[] }>(`${base}?limit=${limit}`, { signal }); }
function command(runId: string, action: "commit" | "resume" | "cancel", signal?: AbortSignal) { return requestJson<{ run: QuickGenerationRun }>(`${base}/${encodeURIComponent(runId)}:${action}`, { method: "POST", signal }); }
export const commitQuickGeneration = (runId: string, signal?: AbortSignal) => command(runId, "commit", signal);
export const resumeQuickGeneration = (runId: string, signal?: AbortSignal) => command(runId, "resume", signal);
export const cancelQuickGeneration = (runId: string, signal?: AbortSignal) => command(runId, "cancel", signal);
export function retryQuickGeneration(runId: string, mode: "SAME_INPUT" | "NEW_SEED", signal?: AbortSignal) { return requestJson<{ run: QuickGenerationRun }>(`${base}/${encodeURIComponent(runId)}:retry`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode }), signal }); }
export function regenerateQuickGenerationPrompt(runId: string, target: QuickGenerationPromptTarget, idempotencyKey: string, signal?: AbortSignal) { return requestJson<{ run: QuickGenerationRun }>(`${base}/${encodeURIComponent(runId)}:regenerate-prompt`, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey }, body: JSON.stringify({ target }), signal }); }
export function rerollQuickGenerationImages(runId: string, input: { count: number; parent_candidate_id?: string | null }, signal?: AbortSignal) { return requestJson<{ run: QuickGenerationRun }>(`${base}/${encodeURIComponent(runId)}:reroll-images`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input), signal }); }
export function selectQuickGenerationCandidate(runId: string, candidateId: string, signal?: AbortSignal) { return requestJson<{ run: QuickGenerationRun }>(`${base}/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(candidateId)}:select`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm_review_checks: true }), signal }); }

const presetBase = "/api/v1/quick-generation-presets";
export function listQuickGenerationPresets(capability?: string, signal?: AbortSignal) { const query = capability ? `?capability=${encodeURIComponent(capability)}` : ""; return requestJson<{ items: QuickGenerationPreset[] }>(`${presetBase}${query}`, { signal }); }
export function createQuickGenerationPreset(input: { name: string; capability: string; execution_profile_version_id: string; parameters: QuickGenerationParameters; favorite: boolean }, signal?: AbortSignal) { return requestJson<{ preset: QuickGenerationPreset }>(presetBase, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input), signal }); }
export function updateQuickGenerationPreset(presetId: string, input: { name: string; execution_profile_version_id: string; parameters: QuickGenerationParameters; favorite: boolean; expected_revision: number }, signal?: AbortSignal) { return requestJson<{ preset: QuickGenerationPreset }>(`${presetBase}/${encodeURIComponent(presetId)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input), signal }); }
export function deleteQuickGenerationPreset(presetId: string, signal?: AbortSignal) { return requestJson<{ deleted: true; preset_id: string }>(`${presetBase}/${encodeURIComponent(presetId)}`, { method: "DELETE", signal }); }
