import type { QueryClient } from "@tanstack/react-query";

export const episodeProductionKeys = {
  root: (episodeId: string) => ["episode-production-v2", episodeId] as const,
  overview: (episodeId: string) => [...episodeProductionKeys.root(episodeId), "overview"] as const,
  shots: (episodeId: string) => [...episodeProductionKeys.root(episodeId), "shots"] as const,
  allShots: (episodeId: string) => [...episodeProductionKeys.shots(episodeId), "all"] as const,
  attentionShots: (episodeId: string) => [...episodeProductionKeys.shots(episodeId), "attention"] as const,
  replan: (episodeId: string) => [...episodeProductionKeys.root(episodeId), "replan"] as const,
};

export function invalidateEpisodeProduction(queryClient: QueryClient, episodeId: string) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: episodeProductionKeys.overview(episodeId) }),
    queryClient.invalidateQueries({ queryKey: episodeProductionKeys.shots(episodeId) }),
    queryClient.invalidateQueries({ queryKey: episodeProductionKeys.replan(episodeId) }),
  ]);
}
