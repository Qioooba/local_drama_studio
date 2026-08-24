import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bindEpisodeSceneRange, createProjectScene, listEpisodeSceneRanges, listProjectScenes } from "../../generated/api";
import { nextOrdinalCode } from "../shared/autoCode";

export function EpisodeSceneRanges({ projectId, episodeId }: { projectId: string; episodeId: string }) {
  const queryClient = useQueryClient();
  const scenes = useQuery({ queryKey: ["project-scenes", projectId], queryFn: () => listProjectScenes(projectId) });
  const ranges = useQuery({ queryKey: ["episode-scene-ranges", episodeId], queryFn: () => listEpisodeSceneRanges(episodeId) });
  const [sceneTitle, setSceneTitle] = useState("");
  const [sceneId, setSceneId] = useState("");
  const [ordinal, setOrdinal] = useState(1);
  const [sourceStart, setSourceStart] = useState(0);
  const [sourceEnd, setSourceEnd] = useState(1);
  const [sourceLabel, setSourceLabel] = useState("");
  useEffect(() => { if (!sceneId && scenes.data?.items[0]) setSceneId(scenes.data.items[0].id); }, [sceneId, scenes.data?.items]);
  const createScene = useMutation({
    mutationFn: () => createProjectScene(projectId, { code: nextOrdinalCode("SC", scenes.data?.items.map((item) => item.code) ?? []), title: sceneTitle }),
    onSuccess: ({ scene }) => {
      setSceneTitle(""); setSceneId(scene.id);
      void queryClient.invalidateQueries({ queryKey: ["project-scenes", projectId] });
    },
  });
  const bind = useMutation({
    mutationFn: () => bindEpisodeSceneRange(episodeId, { scene_id: sceneId, ordinal, source_start: sourceStart, source_end: sourceEnd, ...(sourceLabel.trim() ? { source_label: sourceLabel.trim() } : {}) }),
    onSuccess: () => {
      setOrdinal((value) => value + 1); setSourceStart(sourceEnd); setSourceEnd(sourceEnd + 1); setSourceLabel("");
      void queryClient.invalidateQueries({ queryKey: ["episode-scene-ranges", episodeId] });
    },
  });
  return <section className="scene-range-panel" aria-labelledby="scene-range-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-WRT-002</p><h4 id="scene-range-title">母本场次与当前集范围</h4></div><span className="status-pill">{ranges.data?.items.length ?? 0} 个关联</span></div>
    <p className="muted">母本场次属于项目；不同分集引用同一 scene UUID，不复制成不同实体。来源范围使用显式起止偏移。</p>
    <div className="scene-range-list">{ranges.data?.items.map((item) => <article key={item.id}><strong>{item.ordinal}. {item.scene_code} · {item.scene_title}</strong><span>{item.source_start}—{item.source_end}{item.source_label ? ` · ${item.source_label}` : ""}</span></article>)}{ranges.data?.items.length === 0 && <p className="empty-state">当前集还没有母本场次范围。</p>}</div>
    <details><summary>管理母本场次与范围</summary>
      <div className="scene-range-form">
        <div className="field-fact"><span>场次编号</span><strong>{nextOrdinalCode("SC", scenes.data?.items.map((item) => item.code) ?? [])}</strong><small>按现有场次自动递增</small></div>
        <label>场次标题<input aria-label="场次标题" value={sceneTitle} onChange={(event) => setSceneTitle(event.target.value)} /></label>
        <button type="button" className="secondary" disabled={scenes.isPending || !sceneTitle.trim() || createScene.isPending} onClick={() => createScene.mutate()}>{createScene.isPending ? "创建中…" : "创建母本场次"}</button>
        <label>已有母本场次<select aria-label="已有母本场次" value={sceneId} onChange={(event) => setSceneId(event.target.value)}><option value="">请选择</option>{scenes.data?.items.map((item) => <option key={item.id} value={item.id}>{item.code} · {item.title}</option>)}</select></label>
        <label>集内顺序<input aria-label="集内顺序" type="number" min={1} value={ordinal} onChange={(event) => setOrdinal(Number(event.target.value))} /></label>
        <label>来源起点<input aria-label="来源起点" type="number" min={0} value={sourceStart} onChange={(event) => setSourceStart(Number(event.target.value))} /></label>
        <label>来源终点<input aria-label="来源终点" type="number" min={1} value={sourceEnd} onChange={(event) => setSourceEnd(Number(event.target.value))} /></label>
        <label>范围说明<input aria-label="范围说明" value={sourceLabel} onChange={(event) => setSourceLabel(event.target.value)} /></label>
        <button type="button" className="secondary" disabled={!sceneId || ordinal < 1 || sourceStart < 0 || sourceEnd <= sourceStart || bind.isPending} onClick={() => bind.mutate()}>{bind.isPending ? "关联中…" : "关联到当前集"}</button>
      </div>
      {(createScene.error || bind.error) && <p className="inline-error" role="alert">保存失败：{String(createScene.error ?? bind.error)}</p>}
    </details>
  </section>;
}
