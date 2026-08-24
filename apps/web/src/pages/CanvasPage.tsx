import { useEffect, useMemo } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ProductionCanvasPanel } from "../features/canvas/ProductionCanvasPanel";
import { listEpisodes, listSeasons } from "../generated/api";
import { queryKeys } from "../query/queryKeys";

const CANVAS_CONTEXT_TIMEOUT_MS = 8_000;

function withCanvasContextTimeout<T>(request: Promise<T>, label: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error(`${label}超时，请重试`)), CANVAS_CONTEXT_TIMEOUT_MS);
    request.then(
      (value) => { window.clearTimeout(timer); resolve(value); },
      (error) => { window.clearTimeout(timer); reject(error); },
    );
  });
}

/** V2 route owner for the advanced business DAG; no legacy App shell required. */
export function CanvasPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedEpisodeId = searchParams.get("episode") ?? "";
  const seasons = useQuery({ queryKey: queryKeys.seasons.list(projectId), queryFn: () => withCanvasContextTimeout(listSeasons(projectId), "读取季度"), enabled: Boolean(projectId), retry: false });
  const episodeQueries = useQueries({
    queries: (seasons.data?.items ?? []).slice(0, 8).map((season) => ({
      queryKey: queryKeys.episodes.list(season.id),
      queryFn: () => withCanvasContextTimeout(listEpisodes(season.id), `读取季度 ${season.code} 的分集`),
      retry: false,
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
    <header className="v2-page-header"><div><p className="eyebrow">高级生产画布</p><h2>高级生产画布</h2><p className="muted">业务依赖来自权威 read model；拖动只保存布局，不改变生成事实。</p></div>
      <label>当前分集<select aria-label="当前分集" value={episodeId} disabled={pending || episodes.length === 0} onChange={(event) => setSearchParams({ episode: event.target.value }, { replace: true })}><option value="">选择分集</option>{episodes.map((episode) => <option key={episode.id} value={episode.id}>{episode.code} · {episode.title}</option>)}</select></label>
    </header>
    {pending ? <p className="empty-state" role="status">正在读取分集画布…</p>
      : error ? <div className="workspace-error" role="alert"><div><strong>画布上下文读取失败</strong><p>{error instanceof Error ? error.message : String(error)}</p></div><button type="button" className="secondary" onClick={() => { void seasons.refetch(); episodeQueries.forEach((query) => { void query.refetch(); }); }}>重试读取</button></div>
        : episodes.length === 0 ? <section className="empty-state" aria-labelledby="canvas-empty-title"><h3 id="canvas-empty-title">暂无分集</h3><p>请先在项目中创建或导入分集，再打开高级生产画布。</p><Link className="secondary v2-inline-link" to={`/projects/${projectId}`}>返回项目总览</Link></section>
          : <ProductionCanvasPanel episodeId={episodeId} selectedShotId={null} onSelectShot={(shotId) => navigate(`/projects/${projectId}/episodes/${episodeId}/direct/${shotId}`)} />}
  </div>;
}
