import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getEpisodeProductionOverviewV2,
  getEpisodeProductionReplanV2,
  listEpisodeProductionShotsV2,
  type ProductionState,
} from "../../generated/api";
import { useProjectEventInvalidation } from "../events/useProjectEventInvalidation";
import { episodeProductionKeys, invalidateEpisodeProduction } from "./episodeProductionKeys";

export const EPISODE_ATTENTION_STATES: ProductionState[] = [
  "BLOCKED", "FAILED", "NEEDS_REVIEW", "STALE",
];

/** Own the bounded read model and every path that invalidates it. */
export function useEpisodeProductionQueries(projectId: string, episodeId: string) {
  const queryClient = useQueryClient();
  const overviewKey = episodeProductionKeys.overview(episodeId);
  const shotsKey = episodeProductionKeys.allShots(episodeId);
  const replanKey = episodeProductionKeys.replan(episodeId);
  const overview = useQuery({
    queryKey: overviewKey,
    queryFn: () => getEpisodeProductionOverviewV2(episodeId),
    refetchInterval: (query) => (query.state.data?.overview.active_job_count ?? 0) > 0 ? 3000 : false,
  });
  const shots = useQuery({
    queryKey: shotsKey,
    queryFn: () => listEpisodeProductionShotsV2(episodeId, { cursor: 0, limit: 100 }),
  });
  const attentionShots = useInfiniteQuery({
    queryKey: episodeProductionKeys.attentionShots(episodeId),
    initialPageParam: 0,
    queryFn: ({ pageParam }) => listEpisodeProductionShotsV2(episodeId, {
      cursor: pageParam,
      limit: 100,
      states: EPISODE_ATTENTION_STATES,
    }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
  const replan = useQuery({
    queryKey: replanKey,
    queryFn: () => getEpisodeProductionReplanV2(episodeId),
    enabled: Boolean(overview.data?.overview.replan_required),
    refetchInterval: (query) => {
      const result = query.state.data?.replan;
      return result?.status === "NOT_READY" && result.job ? 3000 : false;
    },
  });

  useProjectEventInvalidation(
    projectId,
    ["EpisodeProductionRunChanged", "JOB_QUEUED", "JOB_FINISHED", "SHOT_REVISION_CREATED", "AudioWorkingCandidateChanged", "FrameBridgeChanged"],
    [overviewKey, episodeProductionKeys.shots(episodeId), replanKey],
  );

  return {
    overview,
    shots,
    attentionShots,
    replan,
    refresh: () => invalidateEpisodeProduction(queryClient, episodeId),
  };
}
