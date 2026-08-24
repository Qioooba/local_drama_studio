import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getEpisodeCockpit } from "./api";
import { useProjectEventInvalidation } from "../events/useProjectEventInvalidation";
import { queryKeys } from "../../query/queryKeys";
import "./episode-cockpit.css";

export function EpisodeCockpit({ projectId, episodeId }: { projectId: string; episodeId: string }) {
  const cockpit = useQuery({ queryKey: queryKeys.episodes.cockpit(episodeId), queryFn: () => getEpisodeCockpit(episodeId) });
  useProjectEventInvalidation(
    projectId,
    ["SHOT_REVISION_CREATED", "JOB_QUEUED", "JOB_FINISHED", "JOB_REQUEUED", "ARTIFACT_REGISTERED", "SELECTION_CHANGED", "REVIEW_SUBMITTED"],
    [queryKeys.episodes.cockpit(episodeId)],
  );
  if (cockpit.isPending) return <section className="episode-cockpit panel" role="status">正在汇总本集生产事实…</section>;
  if (cockpit.error) return <section className="episode-cockpit panel"><p className="inline-error" role="alert">驾驶舱读取失败：{String(cockpit.error)}</p><button className="secondary" type="button" onClick={() => void cockpit.refetch()}>重试读取</button></section>;
  const data = cockpit.data;
  const direct = `/projects/${projectId}/episodes/${episodeId}/direct`;
  const review = `/projects/${projectId}/episodes/${episodeId}/review`;
  return <section className="episode-cockpit panel" aria-labelledby="episode-cockpit-title">
    <div className="panel-heading"><div><p className="eyebrow">Episode Cockpit · 只读事实</p><h3 id="episode-cockpit-title">{data.episode.code} 生产驾驶舱</h3></div><button className="secondary" type="button" onClick={() => void cockpit.refetch()} disabled={cockpit.isFetching}>{cockpit.isFetching ? "刷新中…" : "刷新"}</button></div>
    <div className="episode-cockpit-stats">
      <article><span>已导演</span><strong>{data.shots.directed}<small> / {data.shots.total}</small></strong></article>
      <article><span>有候选</span><strong>{data.shots.with_candidates}<small> 镜头</small></strong></article>
      <article><span>已选择</span><strong>{data.shots.selected}<small> 镜头</small></strong></article>
      <article><span>已批准</span><strong>{data.shots.approved}<small> 镜头</small></strong></article>
      <article className={data.shots.failed ? "attention" : ""}><span>失败</span><strong>{data.shots.failed}<small> 镜头 / {data.jobs.failed} 任务</small></strong></article>
      <article className={data.shots.stale ? "attention" : ""}><span>已过期</span><strong>{data.shots.stale}<small> 镜头</small></strong></article>
      <article><span>镜头桥</span><strong>{data.bridges.ready}<small> / {data.bridges.total} 可用</small></strong></article>
      <article><span>声音</span><strong>{data.audio.verified}<small> / {data.audio.bindings} 已验证</small></strong></article>
      <article className={data.qc.failed ? "attention" : ""}><span>QC</span><strong>{data.qc.passed}<small> 通过 · {data.qc.failed} 失败</small></strong></article>
    </div>
    {data.blockers.length > 0 ? <ul className="episode-cockpit-blockers" aria-label="当前阻塞">{data.blockers.map((item) => <li key={item.code}><strong>{item.count}</strong><span>{item.label}</span></li>)}</ul> : <p className="review-success">当前聚合事实没有发现失败或 stale blocker；人工审核仍需显式完成。</p>}
    <nav className="episode-cockpit-actions" aria-label="分集生产入口">
      <a className="primary-action" href="#episode-production-controls">生成剩余 <span>{data.shots.remaining_generation}</span></a>
      <Link className="secondary" to={`${direct}?filter=failed`}>仅重试失败</Link>
      <Link className="secondary" to={review}>运行 QC</Link>
      <Link className="secondary" to={`/projects/${projectId}/episodes/${episodeId}/timeline`}>合成</Link>
      <Link className="secondary" to={review}>送审</Link>
      <Link className="secondary" to={`/projects/${projectId}/episodes/${episodeId}/delivery`}>导出</Link>
    </nav>
    <p className="muted episode-cockpit-honesty">“生成剩余”进入下方现有生产预检；其余入口打开真实工作区。此驾驶舱本身不创建任务、不自动批准，也不伪造执行结果。</p>
  </section>;
}
