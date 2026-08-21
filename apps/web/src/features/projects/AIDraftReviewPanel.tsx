import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { applyScriptBreakdownDraft, listEpisodes, listScriptBreakdownDrafts, listSeasons } from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";

export function AIDraftReviewPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const drafts = useQuery({ queryKey: queryKeys.scriptBreakdown.all(projectId), queryFn: () => listScriptBreakdownDrafts(projectId) });
  const seasons = useQuery({ queryKey: queryKeys.seasons.list(projectId), queryFn: () => listSeasons(projectId) });
  const firstSeasonId = seasons.data?.items?.[0]?.id;
  const episodes = useQuery({ queryKey: queryKeys.episodes.list(firstSeasonId as string), queryFn: () => listEpisodes(firstSeasonId as string), enabled: Boolean(firstSeasonId) });
  const defaultEpisodeId = episodes.data?.items?.[0]?.id;
  const [selectedEpisode, setSelectedEpisode] = useState<Record<string, string>>({});
  const apply = useMutation({
    mutationFn: ({ draftId, episodeId }: { draftId: string; episodeId: string }) => applyScriptBreakdownDraft(draftId, { episode_id: episodeId }),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: queryKeys.scriptBreakdown.all(projectId) }); },
  });

  return <section className="ai-draft-panel" aria-labelledby="ai-draft-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-WRT-007 · 本地 LLM 草稿</p><h3 id="ai-draft-title">AI 辅助提取草稿</h3></div><span className="status-pill">{drafts.data?.items.length ?? 0} 份</span></div>
    <p className="review-guidance">模型输出只保存为 DRAFT_READY；不会自动创建或覆盖母本场次、镜头或创作资料。采纳必须由后续显式人工流程完成。</p>
    {drafts.isPending && <p className="empty-state">正在读取本地草稿…</p>}
    {drafts.error && <div className="query-error-actions"><p className="inline-error" role="alert">{String(drafts.error)}</p><button type="button" className="secondary" onClick={() => void drafts.refetch()} disabled={drafts.isFetching}>{drafts.isFetching ? "正在重试…" : "重新读取草稿"}</button></div>}
    <div className="ai-draft-list">{drafts.data?.items.map((item) => {
      const scenes = Array.isArray(item.draft.scenes) ? item.draft.scenes : [];
      const shots = scenes.reduce((count, scene) => count + (Array.isArray(scene.shots) ? scene.shots.length : 0), 0);
      const questions = item.confidence.questions ?? [];
      const passages = item.confidence.source_passages ?? [];
      const applied = item.application_status === "APPLIED";
      const busy = apply.isPending && apply.variables?.draftId === item.id;
      const selected = selectedEpisode[item.id] ?? defaultEpisodeId ?? "";
      return <article key={item.id}><div><strong>{item.source_document_title}</strong><span>{item.status} · {applied ? "APPLIED" : "NOT_APPLIED"}</span></div><p>{scenes.length} 个建议场次 · {shots} 个建议镜头</p><p className="draft-evidence">{item.evidence_status === "COMPLETE" ? `证据完整 · 置信度 ${Math.round((item.confidence.confidence?.overall ?? 0) * 100)}% · ${questions.length} 个待确认问题 · ${passages.length} 条原文引用` : "历史草稿 · 未记录完整 Profile/置信度/问题/原文引用，不补造证据"}</p><small>{item.source_document_code} · Profile {item.profile_version_id ?? "legacy 未记录"} · requires_human_action={applied ? "false" : "true"} · automatic_apply=false</small>
        {applied
          ? <p className="frame-feedback success">已应用：本草稿的场次/镜头/对白已落地为生产实体，不能重复应用。</p>
          : <div className="breakdown-apply-area" aria-label={`应用到成片 · ${item.source_document_title}`}><label>应用到成片目标集<span className="inline-control"><select aria-label="选择目标集" value={selected} onChange={(event) => setSelectedEpisode((previous) => ({ ...previous, [item.id]: event.target.value }))}>{episodes.data?.items.map((episode) => <option key={episode.id} value={episode.id}>{episode.code} · {episode.title}</option>)}</select><button type="button" className="secondary" disabled={!selected || busy} onClick={() => { if (selected) apply.mutate({ draftId: item.id, episodeId: selected }); }}>{busy ? "应用中…" : "应用到成片"}</button></span></label>{!episodes.data?.items.length && !episodes.isPending && <p className="muted">项目暂无分集，请先创建集。</p>}</div>}
        {apply.data?.apply.draft_id === item.id && <p className="frame-feedback success" aria-label="应用结果摘要">应用完成：创建 {apply.data.apply.created.scenes} 场 · {apply.data.apply.created.shots} 镜 · {apply.data.apply.created.lines} 条对白；角色 {apply.data.apply.extracted_characters.map((character) => `${character.name}×${character.scene_count}`).join("、")}</p>}
        {apply.error && apply.variables?.draftId === item.id && <p className="inline-error" role="alert">{String(apply.error)}</p>}
      </article>;
    })}</div>
    {drafts.data?.items.length === 0 && <p className="empty-state">当前没有真实本地 LLM 拆解草稿；不会显示模拟建议。</p>}
  </section>;
}
