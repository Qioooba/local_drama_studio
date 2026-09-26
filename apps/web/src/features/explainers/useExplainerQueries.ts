/**
 * Explainer workspace data hooks.
 *
 * All explainer queries go through this module so the query keys, the enabled
 * conditions and the "no fabricated fallback" behaviour stay in one place.  A
 * failed request surfaces an error; it never silently renders an empty success.
 *
 * Two rules from the design are load-bearing here (spec §B12.4, §B11):
 *
 * * A failed GET keeps the last known payload — react-query v5 keeps `data` on a
 *   refetch error, and the pages read `data` before `error` so a broken refresh
 *   shows “更新失败” next to real content instead of an empty state.
 * * Every object-scoped query keys by its real owner (project + owner kind +
 *   owner id + purpose), so adopting one entity reference cannot invalidate
 *   another object's candidate list.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { queryKeys } from "../../query/queryKeys";
import {
  getExplainerAssets,
  getExplainerNarration,
  getExplainerOverview,
  getExplainerQc,
  getExplainerRun,
  getExplainerSchedule,
  getExplainerScript,
  getExplainerSubtitles,
  getExplainerWorkspaceReadiness,
  listExplainerBeatCandidates,
  listExplainerBeats,
  listExplainerEditions,
  listExplainerEntityCandidates,
  listExplainerSchedules,
  listExplainerSegments,
  listExplainers,
  type ExplainerAssetView,
  type ExplainerBeat,
  type ExplainerCandidatePage,
  type ExplainerOverview,
  type ExplainerQcCoverage,
  type ExplainerRun,
  type ExplainerScriptView,
  type ExplainerSegment,
  type ExplainerWorkspaceReadiness,
} from "../../generated/api";

/** Milliseconds before a workspace payload is considered stale. */
const WORKSPACE_STALE_MS = 10_000;

export function useExplainerList(filter: { project_kind?: string; limit?: number } = {}): UseQueryResult<Awaited<ReturnType<typeof listExplainers>>> {
  return useQuery({
    queryKey: queryKeys.explainers.list(filter),
    queryFn: () => listExplainers(filter),
    staleTime: WORKSPACE_STALE_MS,
  });
}

export function useExplainerOverview(projectId: string): UseQueryResult<ExplainerOverview> {
  return useQuery({
    queryKey: queryKeys.explainers.workspace(projectId),
    queryFn: () => getExplainerOverview(projectId),
    enabled: Boolean(projectId),
    staleTime: WORKSPACE_STALE_MS,
  });
}

export function useExplainerRun(runId: string | null | undefined): UseQueryResult<{ run: ExplainerRun }> {
  return useQuery({
    queryKey: queryKeys.explainers.run(runId ?? ""),
    queryFn: () => getExplainerRun(runId!),
    enabled: Boolean(runId),
    // A run in flight changes often enough that a short poll is honest; a
    // terminal run stops polling entirely.
    refetchInterval: (query) => {
      const status = query.state.data?.run?.projected_status;
      return status && ["COMPLETED", "FAILED", "CANCELLED"].includes(status) ? false : 4_000;
    },
  });
}

export function useExplainerScript(projectId: string, revisionId?: string | null): UseQueryResult<ExplainerScriptView> {
  return useQuery({
    queryKey: queryKeys.explainers.script(projectId, revisionId ?? null),
    queryFn: () => getExplainerScript(projectId, revisionId ? { revision_id: revisionId } : {}),
    enabled: Boolean(projectId),
    staleTime: WORKSPACE_STALE_MS,
  });
}

export function useExplainerSegments(projectId: string, revisionId?: string | null): UseQueryResult<{
  video_id: string;
  script_revision_id: string | null;
  segments: ExplainerSegment[];
  selected_takes: Array<Record<string, unknown>>;
  measured_total_ms: number | null;
  timing_status: string;
}> {
  return useQuery({
    queryKey: queryKeys.explainers.segments(projectId, revisionId ?? null),
    queryFn: () => listExplainerSegments(projectId, revisionId ?? undefined),
    enabled: Boolean(projectId),
    staleTime: WORKSPACE_STALE_MS,
  });
}

/**
 * The step-2 asset read model (`getExplainerAssets`, spec §B3.1).
 *
 * Entities carry `appearance_beat_count` (how many *shots* really reference the
 * object) and `reference_binding_status` (whether a reference image is bound) as
 * two separate facts; the retired `beat_reference_count` identity-binding count
 * is never used as an appearance count.
 */
export function useExplainerAssets(projectId: string): UseQueryResult<ExplainerAssetView> {
  return useQuery({
    queryKey: queryKeys.explainers.assets(projectId),
    queryFn: () => getExplainerAssets(projectId),
    enabled: Boolean(projectId),
    staleTime: WORKSPACE_STALE_MS,
  });
}

/**
 * One object's reference candidates (`listExplainerEntityCandidates`).
 *
 * The list is scoped to the object, so a read failure here cannot look like
 * "this project has no candidates" and a failed refresh keeps the known
 * selection (the page renders last-known candidates plus “更新失败”).
 */
export function useExplainerEntityCandidates(
  projectId: string,
  entityId: string | null | undefined,
  enabled = true,
): UseQueryResult<ExplainerCandidatePage> {
  return useQuery({
    queryKey: queryKeys.explainers.entityCandidates(projectId, entityId ?? ""),
    queryFn: () => listExplainerEntityCandidates(projectId, entityId!),
    enabled: Boolean(projectId && entityId) && enabled,
    staleTime: WORKSPACE_STALE_MS,
  });
}

/** The six-step readiness projection behind the step bar and next-action hints (spec §F2.1). */
export function useExplainerReadiness(projectId: string): UseQueryResult<ExplainerWorkspaceReadiness> {
  return useQuery({
    queryKey: queryKeys.explainers.readiness(projectId),
    queryFn: () => getExplainerWorkspaceReadiness(projectId),
    enabled: Boolean(projectId),
    staleTime: WORKSPACE_STALE_MS,
  });
}

export function useExplainerBeats(projectId: string, editionId?: string | null): UseQueryResult<{
  video_id: string;
  beats: ExplainerBeat[];
  render_type_counts: Record<string, number>;
  actual_render_type_counts: Record<string, number>;
  planned_and_actual_reported_separately: true;
}> {
  return useQuery({
    queryKey: queryKeys.explainers.beats(projectId, editionId ?? null),
    queryFn: () => listExplainerBeats(projectId, editionId ?? undefined),
    enabled: Boolean(projectId),
    staleTime: WORKSPACE_STALE_MS,
  });
}

export function useExplainerBeatCandidates(projectId: string, beatId: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.explainers.beatCandidates(projectId, beatId ?? ""),
    queryFn: () => listExplainerBeatCandidates(projectId, beatId!),
    enabled: Boolean(projectId && beatId),
  });
}

export function useExplainerEditions(projectId: string) {
  return useQuery({
    queryKey: queryKeys.explainers.editions(projectId),
    queryFn: () => listExplainerEditions(projectId),
    enabled: Boolean(projectId),
    staleTime: WORKSPACE_STALE_MS,
  });
}

export function useExplainerNarration(editionId: string | null | undefined, locale?: string | null) {
  return useQuery({
    queryKey: queryKeys.explainers.narration(editionId ?? "", locale ?? null),
    queryFn: () => getExplainerNarration(editionId!, locale ?? undefined),
    enabled: Boolean(editionId),
    staleTime: WORKSPACE_STALE_MS,
  });
}

export function useExplainerSubtitles(editionId: string | null | undefined, locale?: string | null, format: "JSON" | "SRT" | "VTT" | "ASS" = "JSON") {
  return useQuery({
    queryKey: queryKeys.explainers.subtitles(editionId ?? "", locale ?? null, format),
    queryFn: () => getExplainerSubtitles(editionId!, { locale: locale ?? undefined, format }),
    enabled: Boolean(editionId),
    staleTime: WORKSPACE_STALE_MS,
  });
}

export function useExplainerQc(editionId: string | null | undefined, renderId?: string | null): UseQueryResult<ExplainerQcCoverage> {
  return useQuery({
    queryKey: queryKeys.explainers.qc(editionId ?? "", renderId ?? null),
    queryFn: () => getExplainerQc(editionId!, renderId ?? undefined),
    enabled: Boolean(editionId),
    staleTime: WORKSPACE_STALE_MS,
  });
}

export function useExplainerSchedules(projectId?: string | null) {
  return useQuery({
    queryKey: queryKeys.explainers.schedules({ projectId: projectId ?? null }),
    queryFn: () => listExplainerSchedules(projectId ? { project_id: projectId } : {}),
    staleTime: WORKSPACE_STALE_MS,
  });
}

export function useExplainerSchedule(scheduleId: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.explainers.schedule(scheduleId ?? ""),
    queryFn: () => getExplainerSchedule(scheduleId!),
    enabled: Boolean(scheduleId),
  });
}
