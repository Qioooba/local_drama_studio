import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { applyProjectTargetDuration, updateProjectTargetDuration, type ProjectConfiguration } from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";

const DEFAULT_TARGET_DURATION_MS = 120_000;

type EpisodeTarget = {
  id: string;
  code: string;
  title: string;
  target_duration_ms?: number;
  production_status?: string;
  number?: number;
  display_order?: number;
};

function secondsFromMilliseconds(value: number | null | undefined): number {
  const seconds = Math.round(Number(value ?? DEFAULT_TARGET_DURATION_MS) / 1_000);
  return Number.isFinite(seconds) && seconds > 0 ? seconds : DEFAULT_TARGET_DURATION_MS / 1_000;
}

export function ProjectTargetDurationEditor({
  projectId,
  configuration,
  episodes,
}: {
  projectId: string;
  configuration: ProjectConfiguration;
  episodes: EpisodeTarget[];
}) {
  const queryClient = useQueryClient();
  const project = configuration.project;
  const projectRevision = Number(project.revision ?? 1);
  const projectDurationMs = Number(project.target_duration_ms ?? DEFAULT_TARGET_DURATION_MS);
  const [durationSeconds, setDurationSeconds] = useState(() => secondsFromMilliseconds(projectDurationMs));
  const [selectedEpisodeIds, setSelectedEpisodeIds] = useState<string[]>([]);
  const [initializedRevision, setInitializedRevision] = useState(projectRevision);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (initializedRevision === projectRevision) return;
    setDurationSeconds(secondsFromMilliseconds(project.target_duration_ms));
    setSelectedEpisodeIds([]);
    setInitializedRevision(projectRevision);
  }, [initializedRevision, project.revision, project.target_duration_ms, projectRevision]);

  const orderedEpisodes = useMemo(
    () => episodes.slice().sort((left, right) => (left.number ?? left.display_order ?? Number.MAX_SAFE_INTEGER) - (right.number ?? right.display_order ?? Number.MAX_SAFE_INTEGER) || left.code.localeCompare(right.code)),
    [episodes],
  );
  const selectedSet = useMemo(() => new Set(selectedEpisodeIds), [selectedEpisodeIds]);
  const projectDurationChanged = Math.round(durationSeconds * 1_000) !== projectDurationMs;

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.productionSettings.section(projectId, "configuration") }),
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.overview(projectId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.seasons.list(projectId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.episodes.lists() }),
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.lists() }),
    ]);
  };

  const updateMutation = useMutation({
    mutationFn: () => updateProjectTargetDuration(projectId, {
      target_duration_ms: Math.round(durationSeconds * 1_000),
      expected_revision: projectRevision,
    }),
    onSuccess: async () => {
      await invalidate();
      setNotice("项目默认时长已保存；已有分集保持不变。需要修改已有分集时，请勾选后使用下方的显式应用操作。");
    },
    onError: (error) => setNotice(`项目默认时长保存失败：${error instanceof Error ? error.message : String(error)}`),
  });
  const applyMutation = useMutation({
    mutationFn: () => applyProjectTargetDuration(projectId, {
      episode_ids: selectedEpisodeIds,
      expected_revision: projectRevision,
    }),
    onSuccess: async ({ application }) => {
      setSelectedEpisodeIds([]);
      await invalidate();
      setNotice(`已将 ${application.episode_ids.length} 个选定分集解析为 ${Math.round(application.target_duration_ms / 1_000)} 秒；请重新检查这些分集的镜头规划。`);
    },
    onError: (error) => setNotice(`应用到已有分集失败：${error instanceof Error ? error.message : String(error)}`),
  });
  const canApply = selectedEpisodeIds.length > 0 && !projectDurationChanged && !applyMutation.isPending;

  const toggleEpisode = (episodeId: string) => {
    setSelectedEpisodeIds((current) => current.includes(episodeId) ? current.filter((id) => id !== episodeId) : [...current, episodeId]);
  };
  const toggleAll = () => {
    setSelectedEpisodeIds((current) => current.length === orderedEpisodes.length ? [] : orderedEpisodes.map((episode) => episode.id));
  };

  return <section className="panel project-target-duration-editor" aria-labelledby="project-target-duration-title">
    <div className="production-setting-card__head">
      <div><span>时长</span><h3 id="project-target-duration-title">项目默认单集时长</h3></div>
      <span className="status-pill state-active">项目级</span>
    </div>
    <p className="muted">这是当前项目的新分集与新规划默认值。保存默认值不会静默改写已有分集；已有分集只有在你明确勾选并点击应用后才会更新。</p>
    <div className="project-target-duration-controls">
      <label>默认目标时长（秒）<input aria-label="项目默认单集时长（秒）" type="number" min={1} max={86400} step={1} value={durationSeconds} onChange={(event) => { setDurationSeconds(Number(event.target.value)); setNotice(null); }} /></label>
      <button type="button" className="primary-action" disabled={!Number.isFinite(durationSeconds) || durationSeconds <= 0 || durationSeconds > 86400 || !projectDurationChanged || updateMutation.isPending} onClick={() => updateMutation.mutate()}>{updateMutation.isPending ? "保存中…" : "保存项目默认时长"}</button>
    </div>
    <div className="project-target-duration-apply">
      <div className="project-target-duration-apply__head"><div><strong>显式应用到已有分集</strong><small>当前默认：{secondsFromMilliseconds(projectDurationMs)} 秒 · 选定 {selectedEpisodeIds.length} 集</small></div><button type="button" className="secondary" disabled={!orderedEpisodes.length} onClick={toggleAll}>{selectedEpisodeIds.length === orderedEpisodes.length ? "取消全选" : "全选"}</button></div>
      {orderedEpisodes.length ? <ul className="project-target-duration-episodes">{orderedEpisodes.map((episode) => <li key={episode.id}><label><input type="checkbox" checked={selectedSet.has(episode.id)} onChange={() => toggleEpisode(episode.id)} /><span><strong>{episode.code}</strong><small>{episode.title} · 当前 {secondsFromMilliseconds(episode.target_duration_ms)} 秒</small></span></label></li>)}</ul> : <p className="empty-state">当前项目还没有分集。</p>}
      <button type="button" className="secondary" disabled={!canApply} onClick={() => applyMutation.mutate()}>{applyMutation.isPending ? "应用中…" : "将默认时长应用到选定分集"}</button>
      {projectDurationChanged && <small className="muted">请先保存上面的项目默认时长，再应用到已有分集。</small>}
      <small className="muted">应用后这些分集的既有镜头/时间线可能需要重新规划；系统不会替你静默生成或替换媒体。</small>
    </div>
    {notice && <p className={notice.includes("失败") ? "inline-error" : "inline-success"} role={notice.includes("失败") ? "alert" : "status"}>{notice}</p>}
  </section>;
}
