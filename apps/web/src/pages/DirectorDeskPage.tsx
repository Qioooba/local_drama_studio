import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { routes } from "../app/routeRegistry";
import { CandidateCompareDialog } from "../features/director-v2/CandidateCompareDialog";
import { DirectorTakeAdoption, type DirectorSelectionType } from "../features/director-v2/DirectorTakeAdoption";
import { DirectorMediaStage } from "../features/director-v2/DirectorMediaStage";
import { DirectorTimelinePreview } from "../features/director-v2/DirectorTimelinePreview";
import { EpisodeShotBoard } from "../features/director-v2/EpisodeShotBoard";
import { ShotNavigator } from "../features/director-v2/ShotNavigator";
import { ShotGenerationInspector } from "../features/director-v2/ShotGenerationInspector";
import { DirectorSoundInspector } from "../features/director-v2/DirectorSoundInspector";
import { readDirectorBatch, removeDirectorBatch, updateDirectorBatchDone } from "../features/director-v2/directorBatchState";
import { useStudioCommand } from "../features/commands/useStudioCommand";
import { useProjectEventInvalidation } from "../features/events/useProjectEventInvalidation";
import { adoptShotWorkingVersionV2, getShotStudioV2, getStoryboardWorkspace, listEpisodeProductionShotsV2, markShotReadyV2, submitShotGenerationV2, type ShotStudioCandidate } from "../generated/api";
import { DirectorSourcePassage } from "../features/source-passage/DirectorSourcePassage";
import "../features/director-v2/director-desk.css";

type InspectorContext = "generate" | "takes" | "sound";

const INSPECTOR_CONTEXTS: Array<{ id: InspectorContext; label: string }> = [
  { id: "generate", label: "AI 生成" },
  { id: "takes", label: "候选与证据" },
  { id: "sound", label: "对白与声音" },
];

const STATUS_LABELS: Record<string, string> = {
  DRAFT: "草稿",
  READY: "可生成",
  PRODUCTION_READY: "可生成",
  RUNNING: "生成中",
  FAILED: "失败",
  SELECTED: "已选中",
  APPROVED: "已批准",
};

function firstText(fields: Record<string, unknown>, keys: string[], fallback: string) {
  for (const key of keys) {
    const value = fields[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return fallback;
}

function DeskIcon({ name }: { name: "source" | "previous" | "next" | "collapse" | "frame" | "compare" }) {
  const paths: Record<typeof name, React.ReactNode> = {
    source: <><path d="M5 3.5h7l3 3v10H5z" /><path d="M12 3.5v3h3M8 10h4M8 13h4" /></>,
    previous: <path d="m12.5 5-5 5 5 5" />,
    next: <path d="m7.5 5 5 5-5 5" />,
    collapse: <path d="M4 7.5h12M4 12.5h12" />,
    frame: <><rect x="3.5" y="4" width="13" height="12" rx="2" /><path d="m6 13 3-3 2 2 2-2 2 3" /></>,
    compare: <><rect x="3.5" y="4" width="5.5" height="12" rx="1" /><rect x="11" y="4" width="5.5" height="12" rx="1" /></>,
  };
  return <svg aria-hidden="true" focusable="false" viewBox="0 0 20 20">{paths[name]}</svg>;
}

export function DirectorDeskPage() {
  const { projectId = "", episodeId = "", shotId } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const requestedContext = searchParams.get("focus") as InspectorContext | null;
  const [inspectorContext, setInspectorContextState] = useState<InspectorContext>(INSPECTOR_CONTEXTS.some((item) => item.id === requestedContext) ? requestedContext! : "generate");
  const setInspectorContext = useCallback((context: InspectorContext) => {
    setInspectorContextState(context);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("focus", context);
      next.delete("inspector");
      return next;
    }, { replace: true });
  }, [setSearchParams]);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [timelineOpen, setTimelineOpen] = useState(true);
  const [timelineTab, setTimelineTab] = useState<"takes" | "timeline">("takes");
  const [compareOpen, setCompareOpen] = useState(false);
  const [adoptionOpen, setAdoptionOpen] = useState(false);
  const [navDrawerOpen, setNavDrawerOpen] = useState(false);
  const [inspectorDrawerOpen, setInspectorDrawerOpen] = useState(Boolean(requestedContext));
  useEffect(() => {
    const hasRequestedContext = INSPECTOR_CONTEXTS.some((item) => item.id === requestedContext);
    setInspectorContextState(hasRequestedContext ? requestedContext! : "generate");
    if (hasRequestedContext) {
      setNavDrawerOpen(false);
      setInspectorDrawerOpen(true);
    }
  }, [requestedContext]);
  const toggleNavDrawer = () => {
    const next = !navDrawerOpen;
    setNavDrawerOpen(next);
    if (next) setInspectorDrawerOpen(false);
  };
  const openInspectorDrawer = () => {
    setNavDrawerOpen(false);
    setInspectorDrawerOpen(true);
  };
  const toggleInspectorDrawer = () => {
    const next = !inspectorDrawerOpen;
    setInspectorDrawerOpen(next);
    if (next) setNavDrawerOpen(false);
  };
  const [activeCandidateId, setActiveCandidateId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");
  useEffect(() => {
    setActiveCandidateId(null);
    setFeedback("");
  }, [shotId]);
  const modalOpen = sourceOpen || compareOpen || adoptionOpen;

  const desk = useQuery({
    queryKey: ["shot-studio-v2", projectId, episodeId, shotId],
    queryFn: () => getShotStudioV2(episodeId, shotId!, { navRadius: 25 }),
    enabled: Boolean(projectId && episodeId && shotId),
    placeholderData: (previousData, previousQuery) => {
      const prevKey = previousQuery?.queryKey;
      if (prevKey && (prevKey[1] !== projectId || prevKey[2] !== episodeId)) {
        return undefined;
      }
      return previousData;
    },
  });
  const shotChooser = useQuery({
    queryKey: ["episode", episodeId, "director-shot-choice"],
    queryFn: () => listEpisodeProductionShotsV2(episodeId, { limit: 100 }),
    enabled: Boolean(projectId && episodeId && !shotId),
  });
  const episodeStoryboard = useQuery({
    queryKey: ["storyboard-workspace", episodeId],
    queryFn: () => getStoryboardWorkspace(episodeId),
    enabled: Boolean(projectId && episodeId && !shotId),
  });
  useProjectEventInvalidation(
    projectId,
    ["JOB_QUEUED", "JOB_CLAIMED", "JOB_FINISHED", "JOB_REQUEUED", "JOB_RECONCILED", "ARTIFACT_REGISTERED", "SHOT_REVISION_CREATED", "SHOT_PRODUCTION_READY", "FRAME_BRIDGE_INHERITED", "FRAME_BRIDGE_CURRENT_FRAME_SET", "FRAME_BRIDGE_LOCKED", "FRAME_BRIDGE_UNLOCKED"],
    [["shot-studio-v2", projectId, episodeId]],
  );
  const shots = desk.data?.shot_nav.items ?? [];
  const selected = desk.data?.current_shot.shot;
  const batchParam = searchParams.get("batch") ?? "";
  const batchDoneParam = searchParams.get("batchDone") ?? "";
  const batchState = useMemo(() => readDirectorBatch(batchParam, episodeId ?? "", batchDoneParam), [batchDoneParam, batchParam, episodeId]);
  const batchShotIds = useMemo(() => batchState.shotIds.filter((id) => shots.some((shot) => shot.id === id)), [batchState.shotIds, shots]);
  const batchDoneIds = useMemo(() => batchState.doneIds.filter((id) => batchShotIds.includes(id)), [batchShotIds, batchState.doneIds]);
  const batchIndex = selected ? batchShotIds.indexOf(selected.id) : -1;
  const batchActive = batchShotIds.length > 0 && batchIndex >= 0;
  const openBatchShot = (index: number, params = searchParams) => {
    const targetId = batchShotIds[index];
    if (!targetId) return;
    const nextParams = new URLSearchParams(params);
    nextParams.set("batchIndex", String(index));
    navigate(`${routes.shotStudio(projectId, episodeId, targetId)}?${nextParams.toString()}`);
  };
  const toggleBatchDone = () => {
    if (!selected || !batchActive) return;
    const nextDone = batchDoneIds.includes(selected.id)
      ? batchDoneIds.filter((id) => id !== selected.id)
      : [...batchDoneIds, selected.id];
    const nextParams = new URLSearchParams(searchParams);
    if (updateDirectorBatchDone(batchParam, episodeId ?? "", nextDone)) nextParams.delete("batchDone");
    else if (nextDone.length) nextParams.set("batchDone", nextDone.join(","));
    else nextParams.delete("batchDone");
    navigate(`${routes.shotStudio(projectId, episodeId, selected.id)}?${nextParams.toString()}`, { replace: true });
  };
  const exitBatch = () => {
    if (!selected) return;
    removeDirectorBatch(batchParam);
    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete("batch");
    nextParams.delete("batchIndex");
    nextParams.delete("batchDone");
    const suffix = nextParams.toString();
    navigate(`${routes.shotStudio(projectId, episodeId, selected.id)}${suffix ? `?${suffix}` : ""}`, { replace: true });
  };
  const candidates = desk.data?.current_shot.candidates ?? [];
  const currentWorkingMediaId = desk.data?.current_shot.current_media?.media_version_id ?? null;
  const activeCandidate = candidates.find((item) => item.media_version_id === activeCandidateId)
    ?? candidates.find((item) => item.selected)
    // The adopted working media is the authoritative selection even when the
    // read model does not mark the candidate's legacy `selected` flag. Keep it
    // as the active candidate so the generation inspector can expose the
    // correct next action (e.g. mark-ready/video generation for a keyframe).
    ?? (currentWorkingMediaId
      ? candidates.find((item) => item.media_version_id === currentWorkingMediaId)
      : undefined)
    ?? candidates[0];
  const activeCandidateIndex = Math.max(0, candidates.findIndex((item) => item.media_version_id === activeCandidate?.media_version_id));
  const comparisonCandidate = candidates.find((item) => item.media_kind === "IMAGE" && item.media_version_id !== activeCandidate?.media_version_id);
  const parentVariantId = activeCandidate?.id ?? desk.data?.current_shot.selected_variant?.id;
  const activeIsKeyframeImage = activeCandidate?.media_kind === "IMAGE" && activeCandidate.stage === "KEYFRAME";
  const explicitSeedRequired = activeCandidate?.seed_policy === "EXPLICIT"
    || Number.isInteger(activeCandidate?.explicit_seed);
  const refreshDesk = () => queryClient.invalidateQueries({ queryKey: ["shot-studio-v2", projectId, episodeId] });
  const markReadyMutation = useMutation({
    mutationFn: () => markShotReadyV2(selected!.id, { expected_revision_no: selected?.revision }),
    onSuccess: async () => {
      setFeedback("镜头已标记为可进入生产。");
      await refreshDesk();
    },
    onError: (error) => setFeedback(`标记就绪失败：${error instanceof Error ? error.message : String(error)}`),
  });
  const selectMutation = useMutation({
    mutationFn: ({ candidate }: { candidate: ShotStudioCandidate; selectionType: DirectorSelectionType }) => adoptShotWorkingVersionV2(candidate.media_version_id),
    onSuccess: refreshDesk,
  });
  const rerollMutation = useMutation({
    mutationFn: () => submitShotGenerationV2(selected!.id, {
      operation: "REROLL",
      stage_code: activeCandidate?.media_kind === "IMAGE" ? "SHOT_IMAGE" : "VIDEO",
      parent_variant_id: parentVariantId!,
      reason_code: "USER_REROLL",
      explicit_seed: explicitSeedRequired ? (() => {
        const value = Math.floor(Math.random() * 900_000) + 100_000;
        return value === activeCandidate?.explicit_seed ? value + 1 : value;
      })() : undefined,
      idempotency_key: globalThis.crypto?.randomUUID?.() ?? `shot-reroll-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    }),
    onSuccess: async (result) => { setFeedback(`新候选已排队（任务 ${result.job.id}）。`); await refreshDesk(); },
    onError: (error) => setFeedback(`重新生成提交失败：${error instanceof Error ? error.message : String(error)}`),
  });
  const currentIndex = desk.data?.shot_nav.selected_index ?? -1;
  const localIndex = selected ? shots.findIndex((shot) => shot.id === selected.id) : -1;
  const previous = localIndex > 0 ? shots[localIndex - 1] : null;
  const next = localIndex >= 0 && localIndex < shots.length - 1 ? shots[localIndex + 1] : null;
  const revision = desk.data?.current_shot.current_revision;
  const fields = revision?.fields ?? {};
  const frameBridge = desk.data?.current_shot.frame_bridge;
  const stageMediaId = activeCandidate?.media_version_id
    ?? desk.data?.current_shot.current_media?.media_version_id
    ?? null;
  const stageMediaKind = activeCandidate?.media_kind
    ?? desk.data?.current_shot.current_media?.media_kind
    ?? null;
  const stageMimeType = activeCandidate?.mime_type ?? desk.data?.current_shot.current_media?.mime_type ?? null;
  const stageDurationMs = activeCandidate?.duration_ms ?? desk.data?.current_shot.current_media?.duration_ms ?? null;
  const sourceExcerpt = desk.data?.current_shot.source_context.source_text
    ?? firstText(fields, ["source_excerpt", "source_text", "excerpt", "dialogue"], "当前镜头尚未关联原文段落。请在分集规划中补充来源范围。");
  const generationHref = `${routes.shotStudio(projectId, episodeId, selected?.id ?? shotId)}?focus=generate`;
  const openGenerationInspector = () => {
    setInspectorContext("generate");
    openInspectorDrawer();
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target;
      const insideForeignDialog = target instanceof Element
        && Boolean(target.closest("[role='dialog'], [role='alertdialog']"))
        && !modalOpen;
      if (event.key === "Escape" && insideForeignDialog) return;
      if (event.key === "Escape") {
        if (modalOpen) {
          event.preventDefault();
          setSourceOpen(false);
          setCompareOpen(false);
          setAdoptionOpen(false);
          return;
        }
        if (inspectorDrawerOpen) {
          event.preventDefault();
          setInspectorDrawerOpen(false);
          return;
        }
        if (navDrawerOpen) {
          event.preventDefault();
          setNavDrawerOpen(false);
          return;
        }
      }
      if (target instanceof Element && target.closest("input, textarea, select, button, a, [contenteditable='true'], [role='button'], [role='dialog']")) return;
      if (modalOpen) return;
      if (event.key === "[" && candidates.length) {
        event.preventDefault();
        setActiveCandidateId(candidates[Math.max(0, activeCandidateIndex - 1)].media_version_id);
      }
      if (event.key === "]" && candidates.length) {
        event.preventDefault();
        setActiveCandidateId(candidates[Math.min(candidates.length - 1, activeCandidateIndex + 1)].media_version_id);
      }
      if (event.key.toLowerCase() === "o") setSourceOpen((value) => !value);
      if (event.key.toLowerCase() === "n") {
        event.preventDefault();
        toggleNavDrawer();
      }
      if (event.key.toLowerCase() === "i") {
        event.preventDefault();
        toggleInspectorDrawer();
      }
      if (event.key.toLowerCase() === "g") {
        event.preventDefault();
        setInspectorContext("generate");
        openInspectorDrawer();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activeCandidateIndex, candidates, inspectorDrawerOpen, modalOpen, navDrawerOpen, setInspectorContext]);

  useStudioCommand(useMemo(() => ({
    id: "shot.previous",
    label: "上一镜",
    description: "保持当前项目与分集上下文",
    group: "当前页面" as const,
    shortcut: "J",
    enabled: () => Boolean(previous && !modalOpen),
    run: () => {
      if (previous) {
        const search = searchParams.toString();
        navigate(`${routes.shotStudio(projectId, episodeId, previous.id)}${search ? `?${search}` : ""}`);
      }
    },
  }), [episodeId, modalOpen, navigate, previous, projectId, searchParams]));
  useStudioCommand(useMemo(() => ({
    id: "shot.next",
    label: "下一镜",
    description: "保持当前项目与分集上下文",
    group: "当前页面" as const,
    shortcut: "K",
    enabled: () => Boolean(next && !modalOpen),
    run: () => {
      if (next) {
        const search = searchParams.toString();
        navigate(`${routes.shotStudio(projectId, episodeId, next.id)}${search ? `?${search}` : ""}`);
      }
    },
  }), [episodeId, modalOpen, navigate, next, projectId, searchParams]));
  useStudioCommand(useMemo(() => ({ id: "shot.generate", label: "打开当前镜头生成设置", description: "先检查模型、资产与资源预检", group: "当前页面" as const, shortcut: "G", enabled: () => Boolean(!modalOpen && selected && desk.data?.allowed_actions.generate), run: () => { setInspectorContext("generate"); setNavDrawerOpen(false); setInspectorDrawerOpen(true); } }), [desk.data?.allowed_actions.generate, modalOpen, selected, setInspectorContext]));
  useStudioCommand(useMemo(() => ({ id: "shot.reroll", label: "重新生成当前镜头", description: "沿用当前能力并自动更换随机条件", group: "当前页面" as const, shortcut: "R", enabled: () => Boolean(!modalOpen && parentVariantId && desk.data?.allowed_actions.generate), run: () => rerollMutation.mutate() }), [desk.data?.allowed_actions.generate, modalOpen, parentVariantId, rerollMutation]));
  useStudioCommand(useMemo(() => ({ id: "shot.compare", label: "并排比较候选", description: "支持 2-up / 4-up 与统一视频播放", group: "当前页面" as const, shortcut: "C", enabled: () => candidates.length > 1 && !modalOpen, run: () => setCompareOpen(true) }), [candidates.length, modalOpen]));
  useStudioCommand(useMemo(() => ({ id: "shot.candidate.1", label: "聚焦候选 1", group: "当前页面" as const, shortcut: "1", enabled: () => Boolean(!modalOpen && candidates[0]), run: () => { if (candidates[0]) setActiveCandidateId(candidates[0].media_version_id); } }), [candidates, modalOpen]));
  useStudioCommand(useMemo(() => ({ id: "shot.candidate.2", label: "聚焦候选 2", group: "当前页面" as const, shortcut: "2", enabled: () => Boolean(!modalOpen && candidates[1]), run: () => { if (candidates[1]) setActiveCandidateId(candidates[1].media_version_id); } }), [candidates, modalOpen]));
  useStudioCommand(useMemo(() => ({ id: "shot.toggle-nav", label: "切换镜头导航抽屉", description: "在紧凑视口下展开或收起镜头导航", group: "当前页面" as const, shortcut: "N", enabled: () => !modalOpen, run: () => { setInspectorDrawerOpen(false); setNavDrawerOpen((value) => !value); } }), [modalOpen]));
  useStudioCommand(useMemo(() => ({ id: "shot.toggle-inspector", label: "切换检查器抽屉", description: "在紧凑视口下展开或收起检查器", group: "当前页面" as const, shortcut: "I", enabled: () => !modalOpen, run: () => { setNavDrawerOpen(false); setInspectorDrawerOpen((value) => !value); } }), [modalOpen]));
  useStudioCommand(useMemo(() => ({ id: "shot.candidate.3", label: "聚焦候选 3", group: "当前页面" as const, shortcut: "3", enabled: () => Boolean(!modalOpen && candidates[2]), run: () => { if (candidates[2]) setActiveCandidateId(candidates[2].media_version_id); } }), [candidates, modalOpen]));

  if (!shotId) {
    if (shotChooser.isLoading || episodeStoryboard.isLoading) return <div className="director-loading" role="status">正在读取本集镜头…</div>;
    if (shotChooser.error) return <div className="director-error" role="alert"><strong>无法读取镜头列表</strong><span>{shotChooser.error instanceof Error ? shotChooser.error.message : String(shotChooser.error)}</span><button type="button" className="secondary" onClick={() => void shotChooser.refetch()}>重试</button></div>;
    return <EpisodeShotBoard projectId={projectId} episodeId={episodeId} shots={shotChooser.data?.items ?? []} storyboard={episodeStoryboard.data?.storyboard.items ?? []} />;
  }
  if (desk.isLoading) return <div className="director-loading" role="status">正在打开导演台…</div>;
  if (desk.error) return <div className="director-error" role="alert"><strong>导演台暂时无法打开</strong><span>{desk.error instanceof Error ? desk.error.message : String(desk.error)}</span><Link to={`/projects/${projectId}/episodes/${episodeId}/plan`}>返回分集规划</Link></div>;
  if (!selected) return <div className="director-error"><strong>本集还没有镜头</strong><span>先在分集规划中创建镜头，再进入导演台精修。</span><Link to={`/projects/${projectId}/episodes/${episodeId}/plan`}>前往分集规划</Link></div>;

  return (
    <div className={`director-desk${timelineOpen ? " timeline-open" : " timeline-collapsed"}${batchActive ? " batch-mode" : ""} ${timelineTab === "timeline" ? "tab-timeline" : "tab-takes"}`}>
      <header className="director-contextbar">
        <nav aria-label="当前位置"><Link to={`/projects/${projectId}`}>{desk.data?.project.name ?? "项目"}</Link><span>/</span><Link to={`/projects/${projectId}/episodes/${episodeId}/plan`}>{desk.data?.episode.title ?? desk.data?.episode.code ?? "本集"}</Link><span>/</span><strong>{selected.code}</strong></nav>
        <div className="director-context-actions">
          <button type="button" className="director-button ghost desk-nav-toggle" aria-keyshortcuts="N" aria-expanded={navDrawerOpen} aria-label="切换镜头列表 (N)" onClick={toggleNavDrawer}><DeskIcon name="collapse" /><span>镜头列表 <kbd>N</kbd></span></button>
          <button type="button" className="director-button ghost desk-inspector-toggle" aria-keyshortcuts="I" aria-expanded={inspectorDrawerOpen} aria-label="切换检查器 (I)" onClick={toggleInspectorDrawer}><DeskIcon name="frame" /><span>检查器 <kbd>I</kbd></span></button>
          <button type="button" className="director-button ghost" aria-keyshortcuts="O" onClick={() => setSourceOpen(true)}><DeskIcon name="source" />查看原文 <kbd>O</kbd></button>
          <button type="button" className="director-button ghost" aria-keyshortcuts="C" disabled={candidates.length < 2} title={candidates.length < 2 ? "至少需要两个候选才能并排比较" : undefined} onClick={() => setCompareOpen(true)}><DeskIcon name="compare" />并排比较</button>
          <Link className="director-button ghost" to={routes.postReview(projectId, episodeId)}><DeskIcon name="compare" />正式审核</Link>
          {desk.data?.allowed_actions.mark_ready && selected.status === "DRAFT" && (
            <button
              type="button"
              className="director-button secondary"
              disabled={markReadyMutation.isPending || desk.isPlaceholderData}
              onClick={() => markReadyMutation.mutate()}
            >
              {markReadyMutation.isPending ? "正在标记就绪…" : "标记就绪"}
            </button>
          )}
          <button type="button" className="director-button primary" disabled={!parentVariantId || !desk.data?.allowed_actions.generate || rerollMutation.isPending || desk.isPlaceholderData} title={!desk.data?.allowed_actions.generate ? "当前镜头尚未满足生成条件" : !parentVariantId ? "当前没有可作为父节点的候选" : undefined} onClick={activeIsKeyframeImage ? openGenerationInspector : () => rerollMutation.mutate()}>{activeIsKeyframeImage ? "重生成首尾帧" : rerollMutation.isPending ? "正在重新生成…" : desk.isPlaceholderData ? "正在载入镜头…" : "重新生成当前镜头"}</button>
        </div>
      </header>

      {batchActive && <section className="director-batchbar" aria-label="逐镜处理批次">
        <div><strong>逐镜处理</strong><span>{batchIndex + 1} / {batchShotIds.length}</span><small>{batchDoneIds.length} 镜已标记处理</small></div>
        <div className="director-batch-actions">
          <button type="button" className="director-button ghost" disabled={batchIndex <= 0} onClick={() => openBatchShot(batchIndex - 1)}>上一项</button>
          <button type="button" className="director-button ghost" aria-pressed={batchDoneIds.includes(selected.id)} onClick={toggleBatchDone}>{batchDoneIds.includes(selected.id) ? "撤销已处理" : "标记本镜已处理"}</button>
          <button type="button" className="director-button primary" disabled={batchIndex >= batchShotIds.length - 1} onClick={() => openBatchShot(batchIndex + 1)}>{batchIndex >= batchShotIds.length - 1 ? "已到最后一项" : "下一项"}</button>
          <button type="button" className="director-button ghost" onClick={exitBatch}>退出逐镜处理</button>
        </div>
      </section>}

      <div className={`director-main-grid${navDrawerOpen ? " nav-drawer-open" : ""}${inspectorDrawerOpen ? " inspector-drawer-open" : ""}`}>
        <div className={`director-shot-nav-container${navDrawerOpen ? " drawer-open" : ""}`}>
          {navDrawerOpen && <div className="director-drawer-backdrop" role="presentation" onClick={() => setNavDrawerOpen(false)} />}
          <div className="director-shot-nav-inner">
            <div className="director-drawer-close-bar">
              <span>镜头导航</span>
              <button type="button" aria-label="关闭镜头导航" onClick={() => setNavDrawerOpen(false)}>×</button>
            </div>
        <ShotNavigator
          shots={shots}
          selectedId={shotId ?? selected.id}
          projectId={projectId}
          episodeId={episodeId}
          totalShots={desk.data?.shot_nav.total}
          windowStart={desk.data?.shot_nav.window_start}
          windowEnd={desk.data?.shot_nav.window_end}
          canEdit={Boolean(desk.data?.allowed_actions.adopt_working_version)}
          onChanged={refreshDesk}
        />
          </div>
        </div>

        <section className={`director-stage${desk.isPlaceholderData ? " is-switching" : ""}`} aria-label="当前镜头媒体舞台" aria-busy={desk.isPlaceholderData}>
          <div className="director-stage-toolbar">
            <div><span className="director-kicker">当前镜头</span><h2>{selected.code} · {firstText(fields, ["title", "summary"], selected.shot_type || "未命名镜头")}</h2></div>
            <div className="director-stepper">
              {previous ? <Link aria-label="上一镜，快捷键 J" aria-keyshortcuts="J" to={{ pathname: routes.shotStudio(projectId, episodeId, previous.id), search: searchParams.toString() ? `?${searchParams.toString()}` : "" }}><DeskIcon name="previous" /></Link> : <button type="button" disabled aria-label="已经是第一镜"><DeskIcon name="previous" /></button>}
              <span>{currentIndex + 1} / {desk.data?.shot_nav.total ?? shots.length}</span>
              {next ? <Link aria-label="下一镜，快捷键 K" aria-keyshortcuts="K" to={{ pathname: routes.shotStudio(projectId, episodeId, next.id), search: searchParams.toString() ? `?${searchParams.toString()}` : "" }}><DeskIcon name="next" /></Link> : <button type="button" disabled aria-label="已经是最后一镜"><DeskIcon name="next" /></button>}
            </div>
          </div>
          <DirectorMediaStage label={`${selected.code} 当前选中媒体`} media={stageMediaId ? { mediaVersionId: stageMediaId, mediaKind: stageMediaKind, mimeType: stageMimeType, durationMs: stageDurationMs, thumbnailReady: activeCandidate?.thumbnail_ready ?? desk.data?.current_shot.current_media?.thumbnail_ready } : null} comparisonMedia={comparisonCandidate ? { mediaVersionId: comparisonCandidate.media_version_id, mediaKind: comparisonCandidate.media_kind, mimeType: comparisonCandidate.mime_type, thumbnailReady: comparisonCandidate.thumbnail_ready } : null} badges={[selected.shot_type || "镜头类型未设", `${Math.round(selected.target_duration_ms / 100) / 10}s`, revision?.is_frozen ? "修订已锁定" : "可编辑"]} onEmptyAction={openGenerationInspector} keyboardShortcutsEnabled={!modalOpen} />
          <div className="director-frame-bridge" aria-label="前后镜头画面衔接">
            <div><span className="frame-node"><DeskIcon name="frame" /><small>上一镜尾帧</small><strong>{frameBridge?.previous?.from_shot_code ?? previous?.code ?? "无"}</strong></span><span className="frame-line" aria-hidden="true" /><span className="frame-node active"><DeskIcon name="frame" /><small>本镜首帧</small><strong>{frameBridge?.current_start ? frameBridge.current_start.status : "待选择"}</strong></span><span className="frame-line" aria-hidden="true" /><span className="frame-node"><DeskIcon name="frame" /><small>本镜尾帧</small><strong>{frameBridge?.current_end ? frameBridge.current_end.status : next ? "连接下一镜" : "片尾"}</strong></span></div>
            <span className="director-frame-auto">AI 自动衔接</span>
          </div>
        </section>

        <div className={`director-inspector-container${inspectorDrawerOpen ? " drawer-open" : ""}`}>
          {inspectorDrawerOpen && <div className="director-drawer-backdrop" role="presentation" onClick={() => setInspectorDrawerOpen(false)} />}
          <aside className="director-inspector" aria-label="镜头检查器">
          <div className="director-zone-title"><div><span>{inspectorContext === "generate" ? "AI 生成" : inspectorContext === "sound" ? "对白与声音" : "候选结果"}</span><strong>{STATUS_LABELS[selected.status] ?? selected.status}</strong></div><button type="button" className="director-inspector-close" aria-label="收起检查器" onClick={() => setInspectorDrawerOpen(false)}>×</button></div>
          <div className="director-inspector-tabs" role="tablist" aria-label="镜头工作上下文">
            {INSPECTOR_CONTEXTS.map((context) => <button key={context.id} type="button" role="tab" aria-selected={inspectorContext === context.id} aria-keyshortcuts={context.id === "generate" ? "G" : undefined} onClick={() => { setInspectorContext(context.id); openInspectorDrawer(); }}>{context.label}</button>)}
          </div>
          <div className="director-inspector-body">
            {inspectorContext === "generate" && (parentVariantId && !activeIsKeyframeImage
              ? <><div className="director-section-head"><strong>继续生成</strong><span>沿用当前 AI 配置</span></div><button type="button" className="director-button primary wide" disabled={!desk.data?.allowed_actions.generate || rerollMutation.isPending} onClick={() => rerollMutation.mutate()}>{rerollMutation.isPending ? "正在重新生成…" : "再生成一个候选"}</button><p className="director-help">AI 自动更换随机条件并保留当前结果，不需要重新设置参数。</p></>
              : <ShotGenerationInspector
                episodeId={episodeId}
                shotId={selected.id}
                shotCode={selected.code}
                shotRevision={selected.revision}
                fields={fields}
                currentShot={desk.data!.current_shot}
                canGenerate={desk.data?.allowed_actions.generate ?? false}
                reviewHref={routes.postReview(projectId, episodeId)}
                onSubmitted={async (message) => { setFeedback(message); await refreshDesk(); }}
              />)}
            {inspectorContext === "sound" && <DirectorSoundInspector
              projectId={projectId}
              shotId={selected.id}
              shotCode={selected.code}
              shotRevision={selected.revision}
              dialogue={desk.data!.current_shot.dialogue}
              videoOptions={candidates.filter((candidate) => candidate.media_kind === "VIDEO").map((candidate) => ({ id: candidate.media_version_id, label: `${candidate.stage ?? "VIDEO"} · Take ${candidate.take_no ?? candidate.variant_no}` }))}
              canEdit={Boolean(desk.data?.allowed_actions.generate || desk.data?.allowed_actions.adopt_working_version)}
              reviewHref={routes.postReview(projectId, episodeId)}
              onChanged={refreshDesk}
            />}
            {inspectorContext === "takes" && <>
              <div className="director-section-head"><strong>候选、质检与审核证据</strong><Link to={routes.postReview(projectId, episodeId)}>打开审核工作区</Link></div>
              <div className="director-continuity-list"><div><span>候选</span><strong>{candidates.length}</strong></div><div><span>当前工作媒体</span><strong>{desk.data?.current_shot.current_media ? "已采用" : "未采用"}</strong></div><div><span>机器质检结果</span><strong>{desk.data?.current_shot.qc_summary.results.length ?? 0}</strong></div><div><span>审核记录</span><strong>{desk.data?.current_shot.review_summary.count ?? 0}</strong></div><div><span>活动任务</span><strong>{desk.data?.current_shot.active_jobs.length ?? 0}</strong></div><div><span>镜头修订</span><strong>v{revision?.revision_no ?? selected.revision ?? 0}</strong></div></div>
              <p className="director-help">这里解释候选来源、工作采用和证据状态；正式批准与退回只在审核工作区写入。</p>
              {candidates.length > 1 && <button type="button" className="director-button secondary wide" onClick={() => setCompareOpen(true)}>并排比较候选</button>}
            </>}
          </div>
        </aside>
        </div>
      </div>

      <section className="director-timeline" aria-label="候选与时间线">
        <div className="director-timeline-header">
          <button type="button" className="director-timeline-toggle" aria-expanded={timelineOpen} onClick={() => setTimelineOpen((value) => !value)}>
            <DeskIcon name="collapse" />
            <span>
              <strong>{timelineTab === "takes" ? "候选比对与采用" : "全集多轨时间线"}</strong>
              <small>{candidates.length} 个候选 · 本集 {desk.data?.episode.shot_count ?? 0} 镜 · {desk.data?.episode.approved_count ?? 0} 已批准</small>
            </span>
          </button>
          {timelineOpen && (
            <div className="director-timeline-tab-bar" role="tablist" aria-label="工作区底栏模式">
              <button
                type="button"
                role="tab"
                aria-selected={timelineTab === "takes"}
                className={`director-timeline-tab-btn${timelineTab === "takes" ? " active" : ""}`}
                onClick={() => setTimelineTab("takes")}
              >
                候选列表 ({candidates.length})
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={timelineTab === "timeline"}
                className={`director-timeline-tab-btn${timelineTab === "timeline" ? " active" : ""}`}
                onClick={() => setTimelineTab("timeline")}
              >
                全集时间线预览
              </button>
            </div>
          )}
        </div>
        {timelineOpen && timelineTab === "takes" && <div className="director-takes-strip">
          {candidates.length > 0 && <DirectorTakeAdoption
            candidates={candidates}
            activeCandidateId={activeCandidate?.media_version_id ?? null}
            currentCandidateId={desk.data?.current_shot.current_media?.media_version_id ?? null}
            pending={selectMutation.isPending}
            onActivate={setActiveCandidateId}
            onAdopt={(candidate, selectionType) => selectMutation.mutateAsync({ candidate, selectionType }).then(() => undefined)}
            onFeedback={setFeedback}
            onDialogOpenChange={setAdoptionOpen}
            renderSecondaryAction={(candidate) => candidate.stage === "FORMAL" && candidate.media_kind === "VIDEO" ? <button type="button" onClick={() => navigate(routes.postReview(projectId, episodeId))}>前往审核</button> : candidate.stage === "PROXY" && candidate.selected ? <button type="button" title="在当前镜头生成正式版本" onClick={openGenerationInspector}>生成正式版</button> : null}
          />}
          {candidates.length === 0 && <div className="director-take-empty"><strong>还没有候选</strong><span>生成后可在这里同屏比较；选择与批准始终是两个动作。</span></div>}
          {activeIsKeyframeImage ? <button type="button" className="director-new-take" onClick={openGenerationInspector}>+ 重生成首尾帧</button> : parentVariantId ? <button type="button" className="director-new-take" disabled={rerollMutation.isPending} onClick={() => rerollMutation.mutate()}>+ 再生成一个</button> : candidates.length ? <button type="button" className="director-new-take" disabled>+ 先选择候选</button> : <Link className="director-new-take" to={generationHref} onClick={openGenerationInspector}>+ 生成首个候选</Link>}
        </div>}
        {timelineOpen && timelineTab === "timeline" && <div className="director-timeline-preview-pane">
          <DirectorTimelinePreview
            projectId={projectId}
            episodeId={episodeId}
            currentShotId={selected.id}
            onSelectShot={(targetShotId) => {
              const search = searchParams.toString();
              navigate(`${routes.shotStudio(projectId, episodeId, targetShotId)}${search ? `?${search}` : ""}`);
            }}
          />
        </div>}
      </section>

      {(feedback || (desk.data?.current_shot.active_jobs.length ? `${desk.data.current_shot.active_jobs.length} 个生成任务正在执行` : "")) ? (
        <div className="director-feedback" role="status" aria-live="polite">
          {feedback || (desk.data?.current_shot.active_jobs.length ? `${desk.data.current_shot.active_jobs.length} 个生成任务正在执行` : "")}
        </div>
      ) : null}

      {sourceOpen && <div className="director-drawer-scrim" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setSourceOpen(false)}><aside className="director-source-drawer" role="dialog" aria-modal="true" aria-labelledby="source-title"><div className="director-drawer-head"><div><span className="director-kicker">原文对照</span><h2 id="source-title">{selected.code} 来源片段</h2></div><button type="button" aria-label="关闭原文" onClick={() => setSourceOpen(false)}>×</button></div><DirectorSourcePassage projectId={projectId} sourceContext={desk.data?.current_shot.source_context ?? {}} fallbackExcerpt={sourceExcerpt} /><div className="director-source-meta"><span>镜头：{selected.code}</span><span>修订：v{revision?.revision_no ?? selected.revision ?? 0}</span></div><Link className="director-button ghost" to={`/projects/${projectId}/episodes/${episodeId}/plan?view=source#plan-source`}>在分集规划中查看原文</Link></aside></div>}

      {compareOpen && <CandidateCompareDialog candidates={candidates} initialCandidateId={activeCandidate?.media_version_id} onClose={() => setCompareOpen(false)} />}
    </div>
  );
}
