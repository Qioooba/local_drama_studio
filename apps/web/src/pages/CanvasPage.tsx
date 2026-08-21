import { useEffect, useMemo } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ProductionCanvasPanel } from "../features/canvas/ProductionCanvasPanel";
import { listEpisodes, listSeasons } from "../generated/api";
import { queryKeys } from "../query/queryKeys";

/** V2 route owner for the advanced business DAG; no legacy App shell required. */
export function CanvasPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedEpisodeId = searchParams.get("episode") ?? "";
  const seasons = useQuery({ queryKey: queryKeys.seasons.list(projectId), queryFn: () => listSeasons(projectId), enabled: Boolean(projectId) });
  const episodeQueries = useQueries({
    queries: (seasons.data?.items ?? []).slice(0, 8).map((season) => ({
      queryKey: queryKeys.episodes.list(season.id),
      queryFn: () => listEpisodes(season.id),
    })),
  });
  const episodes = useMemo(() => episodeQueries.flatMap((query) => query.data?.items ?? []), [episodeQueries]);
  const episodeId = episodes.some((episode) => episode.id === requestedEpisodeId) ? requestedEpisodeId : episodes[0]?.id ?? "";

  useEffect(() => {
    if (!episodeId || episodeId === requestedEpisodeId) return;
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("episode", episodeId);
      return next;
    }, { replace: true });
  }, [episodeId, requestedEpisodeId, setSearchParams]);

  const pending = seasons.isPending || episodeQueries.some((query) => query.isPending);
  const error = seasons.error ?? episodeQueries.find((query) => query.error)?.error;
  return <div className="v2-page canvas-page">
    <header className="v2-page-header"><div><p className="eyebrow">Advanced Canvas</p><h2>高级生产画布</h2><p className="muted">业务依赖来自权威 read model；拖动只保存布局，不改变生成事实。</p></div>
      <label>当前分集<select aria-label="当前分集" value={episodeId} disabled={pending || episodes.length === 0} onChange={(event) => setSearchParams({ episode: event.target.value }, { replace: true })}><option value="">选择分集</option>{episodes.map((episode) => <option key={episode.id} value={episode.id}>{episode.code} · {episode.title}</option>)}</select></label>
    </header>
    {pending ? <p className="empty-state" role="status">正在读取分集画布…</p>
      : error ? <p className="inline-error" role="alert">画布上下文读取失败：{error instanceof Error ? error.message : String(error)}</p>
        : <ProductionCanvasPanel episodeId={episodeId || null} selectedShotId={null} onSelectShot={(shotId) => navigate(`/projects/${projectId}/episodes/${episodeId}/direct/${shotId}`)} />}
  </div>;
}
