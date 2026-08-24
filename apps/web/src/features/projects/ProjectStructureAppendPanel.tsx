import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { appendProjectEpisode } from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";

type SeasonTarget = { id: string; code: string; title: string; episodes: unknown[] };

export function ProjectStructureAppendPanel({ projectId, seasons }: { projectId: string; seasons: SeasonTarget[] }) {
  const queryClient = useQueryClient();
  const existingEpisodeCount = useMemo(() => seasons.reduce((total, season) => total + season.episodes.length, 0), [seasons]);
  const [mode, setMode] = useState<"existing" | "new-season">(seasons.length ? "existing" : "new-season");
  const [seasonId, setSeasonId] = useState(seasons[0]?.id ?? "");
  const [seasonTitle, setSeasonTitle] = useState("");
  const [episodeTitle, setEpisodeTitle] = useState(`第 ${(seasons[0]?.episodes.length ?? existingEpisodeCount) + 1} 集`);
  const [durationSeconds, setDurationSeconds] = useState(60);
  const append = useMutation({
    mutationFn: () => appendProjectEpisode(projectId, {
      ...(mode === "existing" ? { season_id: seasonId } : { season_title: seasonTitle.trim() || undefined }),
      create_new_season: mode === "new-season",
      episode_title: episodeTitle.trim(),
      target_duration_ms: Math.round(durationSeconds * 1000),
    }),
    onSuccess: async (result) => {
      setSeasonId(result.append.season.id);
      setMode("existing");
      setSeasonTitle("");
      setEpisodeTitle(`第 ${Number(result.append.episode.number ?? 1) + 1} 集`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.seasons.catalog(projectId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.projects.creatorSetup(projectId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.projects.lists() }),
      ]);
    },
  });
  const canSubmit = Boolean(episodeTitle.trim()) && durationSeconds > 0 && (mode === "new-season" || Boolean(seasonId));

  return <details className="project-structure-append" open={seasons.length === 0}>
    <summary>{seasons.length ? "添加季度或分集" : "创建首个季度与分集"}</summary>
    <div className="form-grid">
      <label>添加方式<select aria-label="季度与分集添加方式" value={mode} onChange={(event) => { const nextMode = event.target.value as "existing" | "new-season"; setMode(nextMode); setEpisodeTitle(nextMode === "new-season" ? "第 1 集" : `第 ${(seasons.find((season) => season.id === (seasonId || seasons[0]?.id))?.episodes.length ?? 0) + 1} 集`); }}>
        {seasons.length ? <option value="existing">向已有季度追加一集</option> : null}
        <option value="new-season">新建季度并创建第 1 集</option>
      </select></label>
      {mode === "existing" ? <label>目标季度<select aria-label="追加分集目标季度" value={seasonId || seasons[0]?.id || ""} onChange={(event) => { setSeasonId(event.target.value); const target = seasons.find((season) => season.id === event.target.value); setEpisodeTitle(`第 ${(target?.episodes.length ?? 0) + 1} 集`); }}>{seasons.map((season) => <option key={season.id} value={season.id}>{season.code} · {season.title}</option>)}</select></label> : <label>季度标题（可选）<input value={seasonTitle} onChange={(event) => setSeasonTitle(event.target.value)} placeholder={`第 ${seasons.length + 1} 季`} /></label>}
      <label>分集标题<input value={episodeTitle} maxLength={200} onChange={(event) => setEpisodeTitle(event.target.value)} /></label>
      <label>目标时长（秒）<input type="number" min={1} max={86400} step={1} value={durationSeconds} onChange={(event) => setDurationSeconds(Number(event.target.value))} /></label>
    </div>
    <p className="muted">提交会在一个事务中追加结构，不会改写已有季度、分集或镜头。</p>
    <button type="button" className="secondary" disabled={!canSubmit || append.isPending} onClick={() => append.mutate()}>{append.isPending ? "正在创建…" : mode === "new-season" ? "创建季度与首集" : "追加分集"}</button>
    {append.data ? <p className="review-success" role="status">已创建 {append.data.append.season.code} · {append.data.append.episode.code}。<Link to={`/projects/${projectId}/episodes/${append.data.append.episode.id}/plan`}>进入新分集规划</Link></p> : null}
    {append.error ? <p className="inline-error" role="alert">创建失败：{String(append.error)}</p> : null}
  </details>;
}
