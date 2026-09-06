import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { commitStoryboardBatch, getStoryboardWorkspace, planStoryboardBatch, type StoryboardBatchPayload, type StoryboardShot } from "../../generated/api";
import { commitShotEdit, getShotEditContext, planShotEdit, type ShotEditPlan, type ShotReorderCommand, type ShotSplitCommand } from "../episode-plan-v2/shotEditingApi";
import { getShotGroupWorkspace } from "../episode-plan-v2/shotGroupsApi";
import {
  getEpisodePlanAssets, markEpisodePlanShotReady, runPerShot, setEpisodePlanShotAssetState,
  type BatchCommandResult,
} from "../episode-plan-v2/episodePlanTableApi";
import { Drawer } from "../../components/ui";
import { SHOT_TYPES } from "../shared/directorOptions";
import { getDirectorRecipeBinding } from "../recipes-v2/api";
import { StoryboardBatchActions } from "../director-v2/StoryboardBatchActions";
import "../episode-plan-v2/episode-plan-shot-table.css";

type View = "TABLE" | "STORYBOARD" | "TIMELINE";
type Edit = { target_duration_ms: string; shot_type: string; fields?: Record<string, unknown> };
type ToolDrawer = "BATCH" | "STRUCTURE" | null;

type WorkbenchSnapshot = {
  orderedIds: string[];
  edits: Record<string, Edit>;
  copies: Array<{ source_shot_id: string; code: string }>;
  pendingReorder: ShotReorderCommand | null;
  splits: ShotSplitCommand[];
};

type StoredStoryboardDraft = WorkbenchSnapshot & {
  version: 1;
  episodeId: string;
  sourceRevisions: Record<string, number>;
  copySource: string;
  copyCode: string;
  splitSource: string;
  splitAt: string;
  splitCodes: { first: string; second: string };
  savedAt: string;
};

const SHOTS_PER_PAGE = 25;
const STORYBOARD_DRAFT_PREFIX = "local-drama:storyboard-workbench-draft:v1";

export function storyboardDraftKey(episodeId: string): string {
  return `${STORYBOARD_DRAFT_PREFIX}:${episodeId}`;
}

function readStoryboardDraft(episodeId: string): StoredStoryboardDraft | null {
  try {
    const raw = window.localStorage.getItem(storyboardDraftKey(episodeId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredStoryboardDraft>;
    if (parsed.version !== 1 || parsed.episodeId !== episodeId || !Array.isArray(parsed.orderedIds)) return null;
    return parsed as StoredStoryboardDraft;
  } catch {
    return null;
  }
}

export function StoryboardBatchWorkbench({ projectId, episodeId }: { projectId?: string; episodeId: string }) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["storyboard", episodeId], queryFn: () => getStoryboardWorkspace(episodeId) });
  const editContext = useQuery({ queryKey: ["shot-edit-context", episodeId], queryFn: () => getShotEditContext(episodeId) });
  const grouping = useQuery({ queryKey: ["shot-group-workspace", episodeId], queryFn: () => getShotGroupWorkspace(episodeId) });
  const assets = useQuery({ queryKey: ["episode-plan-assets", projectId ?? "none"], queryFn: () => getEpisodePlanAssets(projectId as string), enabled: Boolean(projectId) });
  const recipeBinding = useQuery({ queryKey: ["director-recipe-binding", projectId ?? "none"], queryFn: () => getDirectorRecipeBinding(projectId as string), enabled: Boolean(projectId) });
  const source = query.data?.storyboard.items ?? [];
  const [view, setView] = useState<View>("TABLE");
  const [orderedIds, setOrderedIds] = useState<string[]>([]);
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const [copySource, setCopySource] = useState("");
  const [copyCode, setCopyCode] = useState("");
  const [copies, setCopies] = useState<Array<{ source_shot_id: string; code: string }>>([]);
  const [plan, setPlan] = useState<Awaited<ReturnType<typeof planStoryboardBatch>>["plan"] | null>(null);
  const [page, setPage] = useState(0);
  const [shotDrawerId, setShotDrawerId] = useState<string | null>(null);
  const [toolDrawer, setToolDrawer] = useState<ToolDrawer>(null);
  const [pendingReorder, setPendingReorder] = useState<ShotReorderCommand | null>(null);
  const [splits, setSplits] = useState<ShotSplitCommand[]>([]);
  const [splitSource, setSplitSource] = useState("");
  const [splitAt, setSplitAt] = useState("");
  const [splitCodes, setSplitCodes] = useState({ first: "", second: "" });
  const [semanticPlan, setSemanticPlan] = useState<ShotEditPlan | null>(null);
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [batchShotType, setBatchShotType] = useState("");
  const [assetStateChoice, setAssetStateChoice] = useState("");
  const [batchResults, setBatchResults] = useState<BatchCommandResult[]>([]);
  const [undoStack, setUndoStack] = useState<WorkbenchSnapshot[]>([]);
  const [redoStack, setRedoStack] = useState<WorkbenchSnapshot[]>([]);
  const [draftNotice, setDraftNotice] = useState("");
  const restoredEpisodeRef = useRef("");
  const structureStageRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    setOrderedIds([]);
    setEdits({});
    setCopies([]);
    setPlan(null);
    setPendingReorder(null);
    setSplits([]);
    setSemanticPlan(null);
    setUndoStack([]);
    setRedoStack([]);
    setDraftNotice("");
    restoredEpisodeRef.current = "";
  }, [episodeId]);
  useEffect(() => {
    const incomingIds = source.map((item) => item.id);
    setOrderedIds((current) => {
      if (!current.length) return incomingIds;
      const incoming = new Set(incomingIds);
      return [...current.filter((id) => incoming.has(id)), ...incomingIds.filter((id) => !current.includes(id))];
    });
  }, [query.dataUpdatedAt]);
  useEffect(() => {
    if (!source.length || restoredEpisodeRef.current === episodeId) return;
    restoredEpisodeRef.current = episodeId;
    const draft = readStoryboardDraft(episodeId);
    if (!draft) return;
    const currentRevisions = new Map(source.map((shot) => [shot.id, Number(shot.revision)]));
    const affectedIds = new Set([
      ...Object.keys(draft.edits ?? {}),
      ...(draft.copies ?? []).map((item) => item.source_shot_id),
      ...(draft.splits ?? []).map((item) => item.shot_id),
      ...(draft.pendingReorder ? [draft.pendingReorder.shot_id] : []),
    ]);
    const stale = [...affectedIds].some((id) => !currentRevisions.has(id) || currentRevisions.get(id) !== draft.sourceRevisions?.[id]);
    if (stale) {
      window.localStorage.removeItem(storyboardDraftKey(episodeId));
      setDraftNotice("检测到服务端镜头版本已变化，未恢复过期编辑计划；请基于当前版本重新编排。");
      return;
    }
    const validIds = new Set(source.map((shot) => shot.id));
    setOrderedIds([
      ...draft.orderedIds.filter((id) => validIds.has(id)),
      ...source.map((shot) => shot.id).filter((id) => !draft.orderedIds.includes(id)),
    ]);
    setEdits(draft.edits ?? {});
    setCopies(draft.copies ?? []);
    setPendingReorder(draft.pendingReorder ?? null);
    setSplits(draft.splits ?? []);
    setCopySource(draft.copySource ?? "");
    setCopyCode(draft.copyCode ?? "");
    setSplitSource(draft.splitSource ?? "");
    setSplitAt(draft.splitAt ?? "");
    setSplitCodes(draft.splitCodes ?? { first: "", second: "" });
    setDraftNotice("已恢复本集尚未提交的分镜编辑计划。");
  }, [episodeId, source]);
  const byId = useMemo(() => new Map(source.map((item) => [item.id, item])), [source]);
  const groupShotById = useMemo(() => new Map((grouping.data?.shots ?? []).map((item) => [item.id, item])), [grouping.data]);
  const sceneById = useMemo(() => new Map((grouping.data?.scenes ?? []).map((item) => [item.id, item])), [grouping.data]);
  const groupById = useMemo(() => new Map((grouping.data?.groups ?? []).map((item) => [item.id, item])), [grouping.data]);
  const assetsByShot = useMemo(() => {
    const result = new Map<string, Array<{ assetId: string; name: string; kind: string; role: string; stateId: string | null }>>();
    for (const item of assets.data ?? []) for (const usage of item.usage.shots) {
      const current = result.get(usage.shot_id) ?? [];
      current.push({ assetId: item.asset.id, name: item.asset.name, kind: item.asset.kind, role: usage.role_in_shot, stateId: usage.asset_state_id });
      result.set(usage.shot_id, current);
    }
    return result;
  }, [assets.data]);
  const ordered = orderedIds.map((id) => byId.get(id)).filter(Boolean) as StoryboardShot[];
  const sourceIds = source.map((item) => item.id);
  const draftDirty = Boolean(
    Object.keys(edits).length || copies.length || pendingReorder || splits.length || copySource || splitSource ||
    (orderedIds.length && orderedIds.join("|") !== sourceIds.join("|"))
  );
  const currentSnapshot = (): WorkbenchSnapshot => ({
    orderedIds: [...orderedIds],
    edits: structuredClone(edits),
    copies: structuredClone(copies),
    pendingReorder: pendingReorder ? { ...pendingReorder } : null,
    splits: structuredClone(splits),
  });
  const recordUndo = () => {
    setUndoStack((current) => [...current.slice(-29), currentSnapshot()]);
    setRedoStack([]);
  };
  const restoreSnapshot = (snapshot: WorkbenchSnapshot) => {
    setOrderedIds(snapshot.orderedIds);
    setEdits(snapshot.edits);
    setCopies(snapshot.copies);
    setPendingReorder(snapshot.pendingReorder);
    setSplits(snapshot.splits);
    setPlan(null);
    setSemanticPlan(null);
  };
  const undo = () => {
    const snapshot = undoStack.at(-1);
    if (!snapshot) return;
    setRedoStack((current) => [...current.slice(-29), currentSnapshot()]);
    setUndoStack((current) => current.slice(0, -1));
    restoreSnapshot(snapshot);
  };
  const redo = () => {
    const snapshot = redoStack.at(-1);
    if (!snapshot) return;
    setUndoStack((current) => [...current.slice(-29), currentSnapshot()]);
    setRedoStack((current) => current.slice(0, -1));
    restoreSnapshot(snapshot);
  };
  const pageCount = Math.max(1, Math.ceil(ordered.length / SHOTS_PER_PAGE));
  const pageStart = page * SHOTS_PER_PAGE;
  const visibleShots = ordered.slice(pageStart, pageStart + SHOTS_PER_PAGE);
  const drawerShot = shotDrawerId ? byId.get(shotDrawerId) ?? null : null;
  useEffect(() => {
    setPage((current) => Math.min(current, pageCount - 1));
  }, [pageCount]);
  const persistDraft = () => {
    try {
      const sourceRevisions = Object.fromEntries(source.map((shot) => [shot.id, Number(shot.revision)]));
      const draft: StoredStoryboardDraft = {
        version: 1,
        episodeId,
        sourceRevisions,
        ...currentSnapshot(),
        copySource,
        copyCode,
        splitSource,
        splitAt,
        splitCodes,
        savedAt: new Date().toISOString(),
      };
      window.localStorage.setItem(storyboardDraftKey(episodeId), JSON.stringify(draft));
    } catch {
      setDraftNotice("浏览器无法保存分镜草稿；离开前请先完成预检与提交。");
    }
  };
  useEffect(() => {
    if (!query.data || restoredEpisodeRef.current !== episodeId) return;
    if (!draftDirty) {
      window.localStorage.removeItem(storyboardDraftKey(episodeId));
      return;
    }
    const timer = window.setTimeout(persistDraft, 350);
    return () => {
      window.clearTimeout(timer);
      persistDraft();
    };
  }, [copyCode, copySource, draftDirty, edits, episodeId, orderedIds, pendingReorder, query.data, splitAt, splitCodes, splitSource, splits]);
  useEffect(() => {
    if (!draftDirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      persistDraft();
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [draftDirty, edits, orderedIds, pendingReorder, splits, copySource, copyCode, splitSource, splitAt, splitCodes]);
  useEffect(() => {
    if (toolDrawer !== "STRUCTURE") return;
    structureStageRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [toolDrawer]);
  const payload = (): StoryboardBatchPayload => ({
    ordered_shot_ids: orderedIds,
    edits: Object.entries(edits).map(([shot_id, edit]) => ({ shot_id, expected_revision: Number(byId.get(shot_id)?.revision), target_duration_ms: Number(edit.target_duration_ms), shot_type: edit.shot_type, ...(edit.fields ? { fields: edit.fields } : {}) })),
    copies,
  });
  const invalidatePlan = () => setPlan(null);
  const planning = useMutation({ mutationFn: () => planStoryboardBatch(episodeId, payload()), onSuccess: (data) => setPlan(data.plan) });
  const committing = useMutation({ mutationFn: () => commitStoryboardBatch(episodeId, payload(), String(plan?.plan_hash)), onSuccess: async (data) => {
    queryClient.setQueryData(["storyboard", episodeId], { storyboard: data.result.storyboard });
    setOrderedIds(data.result.storyboard.items.map((item) => item.id));
    setEdits({});
    setCopies([]);
    setPlan(null);
    setUndoStack([]);
    setRedoStack([]);
    window.localStorage.removeItem(storyboardDraftKey(episodeId));
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["shot-edit-context", episodeId] }),
      queryClient.invalidateQueries({ queryKey: ["shot-group-workspace", episodeId] }),
      queryClient.invalidateQueries({ queryKey: ["episode", episodeId, "production"] }),
    ]);
  } });
  const move = (index: number, offset: number) => { const next = [...orderedIds]; const target = index + offset; if (target < 0 || target >= next.length) return; const shot = byId.get(next[index]); const anchor = next[target]; if (!shot) return; recordUndo(); [next[index], next[target]] = [next[target], next[index]]; setOrderedIds(next); setPendingReorder(offset < 0 ? { shot_id: shot.id, before_shot_id: anchor, expected_revision: shot.revision } : { shot_id: shot.id, after_shot_id: anchor, expected_revision: shot.revision }); setSemanticPlan(null); invalidatePlan(); };
  const dropBefore = (targetId: string) => { if (!draggedId || draggedId === targetId) return; const shot = byId.get(draggedId); if (!shot) return; recordUndo(); const next = orderedIds.filter((id) => id !== draggedId); next.splice(next.indexOf(targetId), 0, draggedId); setOrderedIds(next); setPendingReorder({ shot_id: draggedId, before_shot_id: targetId, expected_revision: shot.revision }); setSemanticPlan(null); setDraggedId(null); invalidatePlan(); };
  const update = (shot: StoryboardShot, field: "target_duration_ms" | "shot_type", value: string) => { if (!edits[shot.id]) recordUndo(); setEdits((current) => ({ ...current, [shot.id]: { ...current[shot.id], target_duration_ms: current[shot.id]?.target_duration_ms ?? String(shot.target_duration_ms), shot_type: current[shot.id]?.shot_type ?? shot.shot_type, [field]: field === "target_duration_ms" ? value.replace(/^(-?)0+(?=\d)/, "$1") : value } })); invalidatePlan(); };
  const addCopy = () => {
    if (!copySource || !copyCode.trim()) return;
    recordUndo();
    setCopies((current) => {
      const next = [...current, { source_shot_id: copySource, code: copyCode.trim() }];
      const shot = byId.get(copySource);
      setCopyCode(shot ? `${shot.code}-COPY-${next.filter((item) => item.source_shot_id === shot.id).length + 1}` : "");
      return next;
    });
    invalidatePlan();
  };
  const semanticPayload = () => ({ ordering_token: String(editContext.data?.ordering_token ?? ""), reorder: pendingReorder, splits });
  const semanticPlanning = useMutation({ mutationFn: () => planShotEdit(episodeId, semanticPayload()), onSuccess: setSemanticPlan });
  const semanticCommitting = useMutation({ mutationFn: () => commitShotEdit(episodeId, semanticPayload(), String(semanticPlan?.plan_hash)), onSuccess: async () => { setPendingReorder(null); setSplits([]); setSemanticPlan(null); setSplitSource(""); setUndoStack([]); setRedoStack([]); window.localStorage.removeItem(storyboardDraftKey(episodeId)); await Promise.all([queryClient.invalidateQueries({ queryKey: ["storyboard", episodeId] }), queryClient.invalidateQueries({ queryKey: ["shot-edit-context", episodeId] }), queryClient.invalidateQueries({ queryKey: ["shot-group-workspace", episodeId] }), queryClient.invalidateQueries({ queryKey: ["episode", episodeId, "production"] })]); } });
  const addSplit = () => { const shot = byId.get(splitSource); const firstDuration = Number(splitAt); if (!shot || !splitCodes.first.trim() || !splitCodes.second.trim() || firstDuration <= 0 || firstDuration >= shot.target_duration_ms) return; recordUndo(); setSplits((current) => [...current.filter((item) => item.shot_id !== shot.id), { shot_id: shot.id, expected_revision: shot.revision, first_code: splitCodes.first.trim(), second_code: splitCodes.second.trim(), first_duration_ms: firstDuration }]); setSplitCodes({ first: "", second: "" }); setSplitAt(""); setSemanticPlan(null); };
  const toggleShot = (shotId: string) => setSelectedIds((current) => current.includes(shotId) ? current.filter((id) => id !== shotId) : [...current, shotId]);
  const applyBatchShotType = () => {
    const value = batchShotType.trim();
    if (!value || !selectedIds.length) return;
    recordUndo();
    setEdits((current) => {
      const next = { ...current };
      for (const shotId of selectedIds) {
        const shot = byId.get(shotId);
        if (!shot) continue;
        next[shotId] = {
          target_duration_ms: current[shotId]?.target_duration_ms ?? String(shot.target_duration_ms),
          shot_type: value,
        };
      }
      return next;
    });
    invalidatePlan();
  };
  const applyBoundRecipe = () => {
    const binding = recipeBinding.data;
    if (!binding || !selectedIds.length) return;
    const coverageShotType: Record<string, string> = {
      MEDIUM_CLOSEUP_BIASED: "MEDIUM_CLOSE",
      BALANCED: "MEDIUM",
      WIDE_CONTEXT: "WIDE",
      CLOSEUP_INTIMATE: "CLOSEUP",
    };
    recordUndo();
    setEdits((current) => {
      const next = { ...current };
      for (const shotId of selectedIds) {
        const shot = byId.get(shotId);
        if (!shot) continue;
        next[shotId] = {
          target_duration_ms: String(binding.recipe.shot_planning.avg_duration_ms),
          shot_type: coverageShotType[binding.recipe.shot_planning.dialogue_coverage] ?? current[shotId]?.shot_type ?? shot.shot_type,
          fields: {
            ...(current[shotId]?.fields ?? {}),
            director_recipe_application: {
              recipe_version_id: binding.recipe_version_id,
              recipe_hash: binding.recipe_hash,
              code: binding.code,
              version_no: binding.version_no,
              applied_scope: "STORYBOARD_SELECTED_SHOTS",
            },
          },
        };
      }
      return next;
    });
    invalidatePlan();
  };
  const refreshAfterBatch = async () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["storyboard", episodeId] }),
    queryClient.invalidateQueries({ queryKey: ["episode-plan-assets", projectId] }),
  ]);
  const batchAssetState = useMutation({ mutationFn: async () => {
    const [assetId, stateId] = assetStateChoice.split(":");
    if (!assetId || !stateId) throw new Error("请选择资产状态");
    return runPerShot(selectedIds, (shotId) => setEpisodePlanShotAssetState(shotId, assetId, stateId));
  }, onSuccess: async (results) => { setBatchResults(results); await refreshAfterBatch(); } });
  const batchReady = useMutation({ mutationFn: () => runPerShot(selectedIds, markEpisodePlanShotReady), onSuccess: async (results) => { setBatchResults(results); await refreshAfterBatch(); } });
  const splitShot = splitSource ? byId.get(splitSource) : undefined;
  const splitDuration = splitShot?.target_duration_ms ?? 0;
  const firstSplitDuration = Math.max(0, Math.min(splitDuration, Number(splitAt) || 0));
  const splitRatio = splitDuration ? Math.round((firstSplitDuration / splitDuration) * 100) : 50;
  const discardDraft = () => {
    setOrderedIds(sourceIds);
    setEdits({});
    setCopies([]);
    setPendingReorder(null);
    setSplits([]);
    setCopySource("");
    setCopyCode("");
    setSplitSource("");
    setSplitAt("");
    setSplitCodes({ first: "", second: "" });
    setPlan(null);
    setSemanticPlan(null);
    setUndoStack([]);
    setRedoStack([]);
    setDraftNotice("已放弃本地编辑计划，服务端分镜未改变。");
    window.localStorage.removeItem(storyboardDraftKey(episodeId));
  };
  if (query.isPending) return <section className="subpanel"><p className="empty-state">正在读取分镜批量台…</p></section>;
  if (query.error) return <section className="subpanel" role="alert">{String(query.error)}</section>;
  return <section className="subpanel storyboard-workbench" aria-labelledby="storyboard-title">
    <div className="panel-heading">
      <div><p className="eyebrow">FR-ING-003 · 分镜批量台</p><h4 id="storyboard-title">身份稳定的镜头清单</h4></div>
      <span className="status-pill">{source.length} 镜 · {(query.data!.storyboard.total_duration_ms / 1000).toFixed(1)} 秒</span>
    </div>
    <p className="muted">默认只呈现当前 25 镜并在工作区内部滚动；结构编排在下方全宽舞台预览，提交前不会改变服务端镜头。</p>

    <div className="storyboard-command-bar">
      <div className="storyboard-tabs" role="group" aria-label="分镜显示方式">
        {query.data!.storyboard.views.map((item) => <button type="button" aria-pressed={view === item} className={view === item ? "active" : "secondary"} onClick={() => setView(item)} key={item}>{item === "TABLE" ? "清单" : item === "STORYBOARD" ? "故事板" : "时间线"}</button>)}
      </div>
      <div className="storyboard-command-actions">
        <span className="muted">已选 {selectedIds.length} 镜</span>
        <button type="button" className="secondary" disabled={!undoStack.length} onClick={undo}>撤销</button>
        <button type="button" className="secondary" disabled={!redoStack.length} onClick={redo}>重做</button>
        <button type="button" className="secondary" onClick={() => setToolDrawer("BATCH")}>批量操作</button>
        <button type="button" className={toolDrawer === "STRUCTURE" ? "primary-action" : "secondary"} aria-pressed={toolDrawer === "STRUCTURE"} onClick={() => setToolDrawer((current) => current === "STRUCTURE" ? null : "STRUCTURE")}>全宽结构编排</button>
      </div>
    </div>
    {draftNotice && <p className={draftNotice.includes("无法") || draftNotice.includes("过期") ? "inline-warning" : "frame-feedback success"} role="status">{draftNotice}</p>}
    {draftDirty && <div className="storyboard-draft-status" role="status"><span>本集有尚未提交的编辑计划，已自动保存在此设备。</span><button type="button" className="secondary" onClick={discardDraft}>放弃本地计划</button></div>}

    <div className="storyboard-page-summary" aria-live="polite">
      <span>第 {page + 1} / {pageCount} 页</span>
      <span>{ordered.length ? `${pageStart + 1}–${Math.min(pageStart + SHOTS_PER_PAGE, ordered.length)} / ${ordered.length} 镜` : "0 镜"}</span>
    </div>

    <div className="storyboard-bounded-viewport" tabIndex={0} aria-label={`当前镜头${view === "TABLE" ? "清单" : view === "STORYBOARD" ? "故事板" : "时间线"}，可在区域内滚动`}>
      {view === "TABLE" && <div className="storyboard-table" role="list" aria-label="分镜批量编辑清单">
        {visibleShots.map((shot, localIndex) => {
          const index = pageStart + localIndex;
          const groupShot = groupShotById.get(shot.id);
          const scene = groupShot?.scene_id ? sceneById.get(groupShot.scene_id) : undefined;
          const group = groupShot?.group_id ? groupById.get(groupShot.group_id) : undefined;
          const shotAssets = assetsByShot.get(shot.id) ?? [];
          return <article className="storyboard-row episode-plan-shot-row" role="listitem" draggable onDragStart={() => setDraggedId(shot.id)} onDragOver={(event) => event.preventDefault()} onDrop={() => dropBefore(shot.id)} key={shot.id}>
            <input type="checkbox" aria-label={`选择 ${shot.code}`} checked={selectedIds.includes(shot.id)} onChange={() => toggleShot(shot.id)} />
            <span className="shot-ordinal">{index + 1}</span>
            <div className="shot-identity"><strong title={shot.code}>{shot.code}</strong><small>{shot.shot_type} · {(shot.target_duration_ms / 1000).toFixed(2)} 秒 · r{shot.revision}</small></div>
            <div className="shot-context"><span>Scene：{scene ? `${scene.code} · ${scene.title}` : "未指派"}</span><span>Beat：{group ? `${group.code} · ${group.title}` : "未分组"}</span></div>
            <div className="shot-content"><span>画面/动作：{String(shot.fields.visual ?? shot.fields.subject_action ?? shot.fields.action ?? "未填写")}</span><span>对白：{String(shot.fields.dialogue ?? "无")}</span></div>
            <div className="shot-assets">{shotAssets.length ? <span>{shotAssets.length} 项角色/场景资产</span> : <span>角色/场景资产：未绑定</span>}</div>
            <div className="shot-status"><span className="status-pill">{shot.status}</span><small>{["READY", "GENERATING", "REVIEW", "APPROVED"].includes(shot.status) ? "Production Ready" : "未 Ready"}</small></div>
            <button type="button" className="secondary" onClick={() => setShotDrawerId(shot.id)}>编辑详情</button>
          </article>;
        })}
      </div>}
      {view === "STORYBOARD" && <div className="storyboard-grid">{visibleShots.map((shot, localIndex) => <article key={shot.id}><span className="status-pill">#{pageStart + localIndex + 1}</span><h5>{shot.code}</h5><p>{String(shot.fields.subject_action ?? shot.fields.action ?? "未填写动作")}</p><small>{shot.target_duration_ms} ms · r{shot.revision}</small><button type="button" className="secondary" onClick={() => setShotDrawerId(shot.id)}>编辑详情</button></article>)}</div>}
      {view === "TIMELINE" && <div className="storyboard-timeline">{visibleShots.map((shot, localIndex) => <article key={shot.id}><span>#{pageStart + localIndex + 1}</span><strong>{shot.code}</strong><small>{edits[shot.id]?.target_duration_ms ?? shot.target_duration_ms} ms</small><button type="button" className="secondary" onClick={() => setShotDrawerId(shot.id)}>编辑</button></article>)}</div>}
      {!visibleShots.length && <p className="empty-state">当前分集还没有镜头。</p>}
    </div>

    <nav className="storyboard-pagination" aria-label="镜头分页">
      <button type="button" className="secondary" disabled={page === 0} onClick={() => setPage((current) => Math.max(0, current - 1))}>上一页</button>
      <span>每页最多 {SHOTS_PER_PAGE} 镜</span>
      <button type="button" className="secondary" disabled={page >= pageCount - 1} onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}>下一页</button>
    </nav>

    <Drawer open={Boolean(drawerShot)} onClose={() => setShotDrawerId(null)} title={drawerShot ? `${drawerShot.code} · 镜头详情` : "镜头详情"} width={560} dirtyGuard={Boolean(drawerShot && edits[drawerShot.id])}>
      {drawerShot && (() => {
        const index = ordered.findIndex((item) => item.id === drawerShot.id);
        const groupShot = groupShotById.get(drawerShot.id);
        const scene = groupShot?.scene_id ? sceneById.get(groupShot.scene_id) : undefined;
        const group = groupShot?.group_id ? groupById.get(groupShot.group_id) : undefined;
        const shotAssets = assetsByShot.get(drawerShot.id) ?? [];
        return <div className="storyboard-drawer-stack">
          <dl className="shot-detail-facts"><div><dt>Scene</dt><dd>{scene ? `${scene.code} · ${scene.title}` : "未指派"}</dd></div><div><dt>Beat</dt><dd>{group ? `${group.code} · ${group.title}` : "未分组"}</dd></div><div><dt>状态</dt><dd>{drawerShot.status} · r{drawerShot.revision}</dd></div></dl>
          <div className="shot-detail-copy"><strong>画面 / 动作</strong><p>{String(drawerShot.fields.visual ?? drawerShot.fields.subject_action ?? drawerShot.fields.action ?? "未填写")}</p><strong>对白</strong><p>{String(drawerShot.fields.dialogue ?? "无")}</p></div>
          <div className="shot-detail-assets"><strong>角色 / 场景资产</strong>{shotAssets.length ? shotAssets.map((item) => <span key={`${item.assetId}-${item.role}`}>{item.kind} · {item.name}（{item.role}{item.stateId ? " · 已设状态" : ""}）</span>) : <span>未绑定</span>}</div>
          <label>时长 ms<input aria-label={`${drawerShot.code} 时长`} type="number" min="1" value={edits[drawerShot.id]?.target_duration_ms ?? drawerShot.target_duration_ms} onChange={(event) => update(drawerShot, "target_duration_ms", event.target.value)} /></label>
          <label>镜头类型<select aria-label={`${drawerShot.code} 类型`} value={edits[drawerShot.id]?.shot_type ?? drawerShot.shot_type} onChange={(event) => update(drawerShot, "shot_type", event.target.value)}>{SHOT_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <div className="action-row"><button type="button" className="secondary" disabled={index <= 0} onClick={() => move(index, -1)}>上移一位</button><button type="button" className="secondary" disabled={index < 0 || index >= ordered.length - 1} onClick={() => move(index, 1)}>下移一位</button></div>
          <p className="muted">字段改动先进入计划；排序改动进入语义编辑预览，不会覆盖镜头身份或历史 revision。</p>
        </div>;
      })()}
    </Drawer>

    <Drawer open={toolDrawer === "BATCH"} onClose={() => setToolDrawer(null)} title={`批量操作 · 已选 ${selectedIds.length} 镜`} width={620}>
      <div className="storyboard-drawer-stack">
        {!selectedIds.length && <p className="empty-state">先在镜头清单中勾选目标镜头，再执行批量命令。</p>}
        {projectId && <section className="storyboard-recipe-apply" aria-label="项目导演配方批量套用">
          <strong>项目导演配方</strong>
          {recipeBinding.isPending ? <span>正在读取项目绑定…</span> : recipeBinding.data ? <><span>{recipeBinding.data.code} v{recipeBinding.data.version_no} · 平均 {(recipeBinding.data.recipe.shot_planning.avg_duration_ms / 1000).toFixed(2)} 秒 · {recipeBinding.data.recipe.shot_planning.dialogue_coverage}</span><button type="button" className="secondary" disabled={!selectedIds.length} onClick={applyBoundRecipe}>套用到所选镜头草稿</button><small>只写入本地编辑计划；预检与确认后才创建带 Recipe 版本来源的新镜头修订。</small></> : <span>项目尚未绑定导演配方；可在生产设置中显式选择版本。</span>}
          {recipeBinding.error && <span className="error-text" role="alert">导演配方读取失败：{String(recipeBinding.error)}</span>}
        </section>}
        <label><span>批量镜头类型</span><select aria-label="批量镜头类型" value={batchShotType} onChange={(event) => setBatchShotType(event.target.value)}><option value="">请选择镜头类型</option>{SHOT_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <button type="button" className="secondary" disabled={!selectedIds.length || !batchShotType.trim()} onClick={applyBatchShotType}>应用到所选镜头草稿</button>
        <label><span>资产状态（逐镜调用 0042 命令）</span><select value={assetStateChoice} onChange={(event) => setAssetStateChoice(event.target.value)}><option value="">请选择资产与状态</option>{assets.data?.flatMap((item) => item.states.map((state) => <option key={state.id} value={`${item.asset.id}:${state.id}`}>{item.asset.code} · {item.asset.name} → {state.label}</option>))}</select></label>
        <button type="button" className="secondary" disabled={!selectedIds.length || !assetStateChoice || batchAssetState.isPending} onClick={() => batchAssetState.mutate()}>{batchAssetState.isPending ? "正在逐镜设置…" : "批量设置资产状态"}</button>
        <button type="button" disabled={!selectedIds.length || batchReady.isPending} onClick={() => batchReady.mutate()}>{batchReady.isPending ? "正在逐镜提交…" : "批量 Production Ready"}</button>
        <p className="muted">资产与 Ready 各镜独立提交；失败不会伪装为整批成功。</p>
        {batchResults.length > 0 && <div className="episode-plan-batch-result" aria-live="polite"><strong>批量结果：成功 {batchResults.filter((item) => item.ok).length} · 失败 {batchResults.filter((item) => !item.ok).length}</strong>{batchResults.filter((item) => !item.ok).map((item) => <p className="episode-plan-batch-failure" key={item.shotId}>{byId.get(item.shotId)?.code ?? item.shotId}：{item.message}</p>)}</div>}
        <StoryboardBatchActions episodeId={episodeId} selectedShotIds={selectedIds} onChanged={async () => { await query.refetch(); }} />
      </div>
    </Drawer>

    {toolDrawer === "STRUCTURE" && <section className="storyboard-structure-stage" aria-labelledby="storyboard-structure-title" ref={structureStageRef}>
      <div className="storyboard-structure-heading">
        <div><p className="eyebrow">可撤销预览</p><h4 id="storyboard-structure-title">重排、拆分与复制</h4><p className="muted">保持镜头大盘可见；先编辑与预览，再按语义类型分别确认提交。</p></div>
        <button type="button" className="secondary" onClick={() => setToolDrawer(null)}>收起结构编排</button>
      </div>
      <div className="storyboard-structure-grid">
        <section aria-labelledby="storyboard-copy-title"><div className="section-title"><span id="storyboard-copy-title">复制镜头</span><small>创建新身份，不复用原镜头 id</small></div><div className="storyboard-copy"><label>复制来源<select aria-label="复制来源" value={copySource} onChange={(event) => { const shot = byId.get(event.target.value); setCopySource(event.target.value); setCopyCode(shot ? `${shot.code}-COPY-${copies.filter((item) => item.source_shot_id === shot.id).length + 1}` : ""); invalidatePlan(); }}><option value="">选择镜头</option>{source.map((shot) => <option key={shot.id} value={shot.id}>{shot.code}</option>)}</select></label><div className="field-fact"><span>新编号</span><strong>{copyCode || "选择来源后自动生成"}</strong></div><button type="button" className="secondary" onClick={addCopy} disabled={!copySource || !copyCode.trim()}>加入复制计划</button><span className="muted">待复制 {copies.length} 项</span></div></section>
        <section aria-labelledby="storyboard-split-title"><div className="section-title"><span id="storyboard-split-title">拆分镜头</span><small>自动编号，提交后保留来源谱系</small></div><div className="storyboard-split-editor"><label>拆分镜头<select aria-label="拆分镜头" value={splitSource} onChange={(event) => { const shot = byId.get(event.target.value); setSplitSource(event.target.value); if (shot) { setSplitAt(String(Math.floor(shot.target_duration_ms / 2))); setSplitCodes({ first: `${shot.code}-A`, second: `${shot.code}-B` }); } else { setSplitAt(""); setSplitCodes({ first: "", second: "" }); } }}><option value="">选择镜头</option>{source.map((shot) => <option key={shot.id} value={shot.id}>{shot.code} · {(shot.target_duration_ms / 1000).toFixed(2)} 秒</option>)}</select></label>{splitShot && <><label className="storyboard-split-range">拆分比例：{splitRatio}% / {100 - splitRatio}%<input aria-label="拆分比例" type="range" min="10" max="90" step="5" value={Math.max(10, Math.min(90, splitRatio))} onChange={(event) => setSplitAt(String(Math.round(splitDuration * Number(event.target.value) / 100)))} /></label><div className="storyboard-split-preview" aria-label="拆分时长预览"><span style={{ flex: Math.max(1, firstSplitDuration) }}><strong>{splitCodes.first}</strong>{(firstSplitDuration / 1000).toFixed(2)} 秒</span><span style={{ flex: Math.max(1, splitDuration - firstSplitDuration) }}><strong>{splitCodes.second}</strong>{((splitDuration - firstSplitDuration) / 1000).toFixed(2)} 秒</span></div><div className="storyboard-auto-codes"><div className="field-fact"><span>第一段编号</span><strong>{splitCodes.first}</strong></div><div className="field-fact"><span>第二段编号</span><strong>{splitCodes.second}</strong></div></div></>}<button type="button" className="secondary" disabled={!splitSource} onClick={addSplit}>加入拆分计划</button></div></section>
      </div>
      <div className="storyboard-commit-lanes">
        <div className="action-row"><button type="button" className="secondary" onClick={() => semanticPlanning.mutate()} disabled={semanticPlanning.isPending || (!pendingReorder && !splits.length) || !editContext.data}>{semanticPlanning.isPending ? "生成预览…" : "预览重排 / 拆分"}</button><button type="button" onClick={() => semanticCommitting.mutate()} disabled={!semanticPlan?.valid || semanticCommitting.isPending}>{semanticCommitting.isPending ? "提交中…" : "确认语义编辑"}</button>{semanticPlan && <span className={semanticPlan.valid ? "ok-text" : "error-text"}>{semanticPlan.valid ? `将重排 ${semanticPlan.summary.reordered ? 1 : 0} 项、拆分 ${semanticPlan.summary.split} 镜；timeline 标记 stale；不复制候选` : `${semanticPlan.issues.length} 个冲突`}</span>}</div>
        {semanticPlan?.issues.map((issue) => <p className="error-text" role="alert" key={issue.code}>{issue.code}：{issue.message}</p>)}
        {(semanticPlanning.error || semanticCommitting.error) && <p className="error-text" role="alert">{String(semanticPlanning.error ?? semanticCommitting.error)}</p>}
        <div className="action-row"><button type="button" className="secondary" onClick={() => planning.mutate()} disabled={planning.isPending || !source.length || Boolean(pendingReorder || splits.length)}>{planning.isPending ? "校验中…" : "校验字段 / 复制计划"}</button><button type="button" onClick={() => committing.mutate()} disabled={!plan?.valid || committing.isPending}>{committing.isPending ? "提交中…" : "确认字段 / 复制"}</button>{plan && <span className={plan.valid ? "ok-text" : "error-text"}>{plan.valid ? `可提交 · ${plan.plan_hash.slice(0, 12)}` : `${plan.issues.length} 个冲突`}</span>}</div>
        {plan?.issues.map((issue, index) => <p className="error-text" role="alert" key={`${issue.code}-${index}`}>{issue.code}：{issue.message}</p>)}
        {(planning.error || committing.error) && <p className="error-text" role="alert">{String(planning.error ?? committing.error)}</p>}
        {committing.isSuccess && <p className="ok-text">批量提交完成；镜头 identity 与既有历史均保留。</p>}
      </div>
    </section>}
  </section>;
}
