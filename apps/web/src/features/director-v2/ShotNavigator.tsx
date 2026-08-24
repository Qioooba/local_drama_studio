import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { commitShotEdit, getShotEditContext, planShotEdit, type ShotReorderCommand } from "../episode-plan-v2/shotEditingApi";
import { persistDirectorBatch, readDirectorBatch } from "./directorBatchState";
import type { DirectorDeskShotNavItem } from "./types";

const STATUS_LABELS: Record<string, string> = {
  DRAFT: "草稿",
  READY: "可生成",
  PRODUCTION_READY: "可生成",
  RUNNING: "生成中",
  FAILED: "失败",
  SELECTED: "已选中",
  APPROVED: "已批准",
};

type ShotNavigatorProps = {
  shots: DirectorDeskShotNavItem[];
  selectedId?: string;
  projectId: string;
  episodeId: string;
  totalShots?: number;
  windowStart?: number;
  windowEnd?: number;
  canEdit?: boolean;
  onChanged?: () => void | Promise<void>;
};

function thumbnailUrl(mediaVersionId: string) {
  return `/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=medium&frame=poster`;
}

function FrameIcon() {
  return <svg aria-hidden="true" focusable="false" viewBox="0 0 20 20"><rect x="3.5" y="4" width="13" height="12" rx="2" /><path d="m6 13 3-3 2 2 2-2 2 3" /></svg>;
}

export function ShotNavigator({ shots, selectedId, projectId, episodeId, totalShots = shots.length, windowStart = 0, windowEnd = shots.length, canEdit = false, onChanged }: ShotNavigatorProps) {
  const [query, setQuery] = useState("");
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedShots, setSelectedShots] = useState<Set<string>>(() => new Set());
  const [selectionOnly, setSelectionOnly] = useState(false);
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
  const navigate = useNavigate();
  const filter = searchParams.get("filter") ?? "all";
  const batchSelectionParam = searchParams.get("batch") ?? "";
  const batchDoneParam = searchParams.get("batchDone") ?? "";
  const reorderEnabled = canEdit && filter === "all" && query.trim() === "" && !selectionOnly;

  useEffect(() => {
    if (!batchSelectionParam) return;
    const restored = readDirectorBatch(batchSelectionParam, episodeId, batchDoneParam)
      .shotIds.filter((id) => shots.some((shot) => shot.id === id));
    if (restored.length) setSelectedShots(new Set(restored));
  }, [batchDoneParam, batchSelectionParam, episodeId, shots]);

  const filtered = useMemo(() => shots.filter((shot) => {
    if (selectionOnly && !selectedShots.has(shot.id)) return false;
    const matchesQuery = `${shot.code} ${shot.scene_code ?? ""} ${shot.scene_title ?? ""} ${shot.group_code ?? ""} ${shot.group_title ?? ""}`.toLowerCase().includes(query.toLowerCase());
    if (!matchesQuery) return false;
    if (filter === "failed") return [shot.status, shot.job_status].some((value) => ["FAILED", "BLOCKED", "NEEDS_ATTENTION", "ORPHANED"].includes(String(value ?? "").toUpperCase()));
    if (filter === "stale") return String(shot.continuity_status).toUpperCase().includes("STALE");
    if (filter === "review") return ["REVIEW", "SELECTED"].includes(String(shot.status).toUpperCase());
    return true;
  }), [filter, query, selectedShots, selectionOnly, shots]);

  const grouped = filtered.reduce<Array<{ key: string; label: string; shots: DirectorDeskShotNavItem[] }>>((groups, shot) => {
    const key = shot.scene_id ?? "ungrouped";
    const current = groups.at(-1);
    if (current?.key === key) current.shots.push(shot);
    else groups.push({ key, label: shot.scene_code ? `${shot.scene_code} · ${shot.scene_title ?? "未命名场景"}` : "未分场镜头", shots: [shot] });
    return groups;
  }, []);

  const reorderMutation = useMutation({
    mutationFn: async ({ shotId, targetId, direction }: { shotId: string; targetId: string; direction: "before" | "after" }) => {
      const context = await getShotEditContext(episodeId);
      const source = context.items.find((item) => item.id === shotId);
      const target = context.items.find((item) => item.id === targetId);
      if (!source || !target) throw new Error("镜头顺序已变化，请刷新后重试");
      const reorder: ShotReorderCommand = {
        shot_id: source.id,
        expected_revision: source.revision,
        ...(direction === "before" ? { before_shot_id: target.id } : { after_shot_id: target.id }),
      };
      const payload = { ordering_token: context.ordering_token, reorder, splits: [] };
      const plan = await planShotEdit(episodeId, payload);
      if (!plan.valid) throw new Error(plan.issues.map((issue) => issue.message).join("；") || "排序计划未通过校验");
      await commitShotEdit(episodeId, payload, plan.plan_hash);
    },
    onSuccess: async () => { await onChanged?.(); },
  });

  const moveRelative = (shotId: string, delta: -1 | 1) => {
    const index = shots.findIndex((shot) => shot.id === shotId);
    const target = shots[index + delta];
    if (!target || !reorderEnabled || reorderMutation.isPending) return;
    reorderMutation.mutate({ shotId, targetId: target.id, direction: delta < 0 ? "before" : "after" });
  };
  const dropShot = (targetId: string) => {
    if (!draggedId || draggedId === targetId || !reorderEnabled) return;
    const from = shots.findIndex((shot) => shot.id === draggedId);
    const to = shots.findIndex((shot) => shot.id === targetId);
    reorderMutation.mutate({ shotId: draggedId, targetId, direction: from < to ? "after" : "before" });
    setDraggedId(null);
    setDropTargetId(null);
  };
  const chooseFilter = (nextFilter: string) => {
    setSelectionOnly(false);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (nextFilter === "all") next.delete("filter"); else next.set("filter", nextFilter);
      return next;
    }, { replace: true });
  };
  const toggleShot = (shotId: string) => setSelectedShots((current) => {
    const next = new Set(current);
    if (next.has(shotId)) next.delete(shotId); else next.add(shotId);
    return next;
  });
  const selectedVisible = filtered.filter((shot) => selectedShots.has(shot.id));
  const selectedForBatch = shots.filter((shot) => selectedShots.has(shot.id));
  const startBatch = () => {
    if (!selectedForBatch.length) return;
    const next = new URLSearchParams(searchParams);
    next.set("batch", persistDirectorBatch(episodeId, selectedForBatch.map((shot) => shot.id)));
    next.set("batchIndex", "0");
    next.delete("batchDone");
    navigate(`/projects/${projectId}/episodes/${episodeId}/direct/${selectedForBatch[0].id}?${next.toString()}`);
  };

  return (
    <aside className="director-shot-nav" aria-label="镜头导航">
      <div className="director-zone-title"><div><span>镜头导航</span><strong>{shots.length === totalShots ? `${totalShots} 镜` : `${windowStart + 1}–${windowEnd} / ${totalShots} 镜`}</strong></div></div>
      {shots.length !== totalShots && <small className="director-nav-window-note">当前为所选镜头附近的有界窗口；切换镜头会自动加载相邻范围。</small>}
      <label className="director-search"><span className="sr-only">搜索镜头</span><input type="search" value={query} onChange={(event) => { setQuery(event.target.value); setSelectionOnly(false); }} placeholder="搜索镜号或内容" /></label>
      <div className="director-nav-filters" aria-label="筛选镜头">{[["all", "全部"], ["failed", "失败"], ["stale", "已过期"], ["review", "待审"]].map(([value, label]) => <button key={value} type="button" aria-pressed={filter === value && !selectionOnly} onClick={() => chooseFilter(value)}>{label}</button>)}</div>
      <div className="director-shot-bulk" aria-label="批量镜头动作">
        <button type="button" onClick={() => setSelectedShots(new Set(filtered.map((shot) => shot.id)))} disabled={filtered.length === 0}>全选可见</button>
        <button type="button" onClick={() => { setSelectedShots(new Set()); setSelectionOnly(false); }} disabled={selectedShots.size === 0}>清除</button>
        <button type="button" aria-pressed={selectionOnly} onClick={() => setSelectionOnly((value) => !value)} disabled={selectedShots.size === 0}>仅看所选</button>
        <strong>{selectedShots.size} 已选</strong>
        {selectedShots.size > 0 && <div className="director-shot-bulk-actions">
          <button type="button" onClick={startBatch}>逐镜处理</button>
          <Link to={`/projects/${projectId}/episodes/${episodeId}/review`}>审核入口</Link>
          <Link to={`/projects/${projectId}/episodes/${episodeId}/run`}>生产入口</Link>
        </div>}
        <small>{reorderEnabled ? "拖动手柄或用上下按钮提交正式排序。" : "筛选/搜索时暂停排序；批量入口不会伪造批量提交。"}</small>
        {reorderMutation.error && <p role="alert">排序失败：{reorderMutation.error instanceof Error ? reorderMutation.error.message : String(reorderMutation.error)}</p>}
      </div>
      <div className="director-shot-list" aria-busy={reorderMutation.isPending}>
        {grouped.map((group) => <section className="director-scene-group" key={`${group.key}-${group.shots[0]?.id ?? "empty"}`} aria-label={group.label}><h3><span>{group.label}</span><small>{group.shots.length} 镜</small></h3>{group.shots.map((shot) => {
          const status = STATUS_LABELS[shot.status] ?? shot.status;
          const shotIndex = shots.findIndex((item) => item.id === shot.id);
          return (
            <div
              key={shot.id}
              className={`director-shot-row${draggedId === shot.id ? " dragging" : ""}${dropTargetId === shot.id ? " drop-target" : ""}`}
              draggable={reorderEnabled && !reorderMutation.isPending}
              onDragStart={(event) => { setDraggedId(shot.id); event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", shot.id); }}
              onDragOver={(event) => { if (reorderEnabled) { event.preventDefault(); setDropTargetId(shot.id); } }}
              onDragLeave={() => setDropTargetId((current) => current === shot.id ? null : current)}
              onDrop={(event) => { event.preventDefault(); dropShot(shot.id); }}
              onDragEnd={() => { setDraggedId(null); setDropTargetId(null); }}
            >
              <div className="director-shot-row-actions">
                <input type="checkbox" aria-label={`选择镜头 ${shot.code}`} checked={selectedShots.has(shot.id)} onChange={() => toggleShot(shot.id)} />
                <button type="button" className="director-drag-handle" aria-label={`拖动镜头 ${shot.code}`} title={reorderEnabled ? "拖动排序" : "请先清除搜索和筛选"} disabled={!reorderEnabled || reorderMutation.isPending}>⠿</button>
                <button type="button" aria-label={`上移镜头 ${shot.code}`} disabled={!reorderEnabled || shotIndex <= 0 || reorderMutation.isPending} onClick={() => moveRelative(shot.id, -1)}>↑</button>
                <button type="button" aria-label={`下移镜头 ${shot.code}`} disabled={!reorderEnabled || shotIndex >= shots.length - 1 || reorderMutation.isPending} onClick={() => moveRelative(shot.id, 1)}>↓</button>
              </div>
              <Link className={`director-shot-card${selectedId === shot.id ? " selected" : ""}`} aria-current={selectedId === shot.id ? "true" : undefined} to={`/projects/${projectId}/episodes/${episodeId}/direct/${shot.id}`}>
                <span className="director-shot-thumb">{shot.thumbnail_media_version_id ? <img src={thumbnailUrl(shot.thumbnail_media_version_id)} alt="" loading="lazy" decoding="async" /> : <FrameIcon />}</span>
                <span className="director-shot-copy"><span><strong>{shot.code}</strong><small>{shot.job_status ?? shot.continuity_status}</small></span><span>{shot.group_code ? `${shot.group_code} · ${shot.group_title ?? "镜头组"}` : shot.scene_code ? `${shot.scene_code} · ${shot.scene_title ?? "场景"}` : "镜头生产单元"}</span><small className={`shot-state state-${shot.status.toLowerCase()}`}>{status}</small></span>
              </Link>
            </div>
          );
        })}</section>)}
        {filtered.length === 0 && <p className="director-empty">没有匹配的镜头。</p>}
      </div>
    </aside>
  );
}
