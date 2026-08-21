import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { routes } from "../app/routeRegistry";
import { approveFormalCandidate, loadDirectorDesk, rerollDirectorCandidate, selectDirectorCandidate } from "../features/director-v2/DirectorDeskClient";
import { DirectorIntentEditor } from "../features/director-v2/DirectorIntentEditor";
import { FrameBridgeControls } from "../features/director-v2/FrameBridgeControls";
import { CandidateCompareDialog } from "../features/director-v2/CandidateCompareDialog";
import { DirectorTakeAdoption, type DirectorSelectionType } from "../features/director-v2/DirectorTakeAdoption";
import { DirectorMediaStage } from "../features/director-v2/DirectorMediaStage";
import { DirectorSoundInspector } from "../features/director-v2/DirectorSoundInspector";
import { ShotNavigator } from "../features/director-v2/ShotNavigator";
import type { DirectorDeskCandidate, RerollReasonCode } from "../features/director-v2/types";
import { useStudioCommand } from "../features/commands/useStudioCommand";
import { useProjectEventInvalidation } from "../features/events/useProjectEventInvalidation";
import { ShotAssetSection } from "../features/production/DirectorShotEditor";
import { ContinuityPanel } from "../features/production/ContinuityPanel";
import { getEpisodeProduction, getShotContinuityContext } from "../generated/api";
import { DirectorSourcePassage } from "../features/source-passage/DirectorSourcePassage";
import "../features/director-v2/director-desk.css";

type InspectorTab = "picture" | "assets" | "generate" | "continuity" | "sound" | "advanced";

const INSPECTOR_TABS: Array<{ id: InspectorTab; label: string }> = [
  { id: "picture", label: "画面" },
  { id: "assets", label: "角色场景" },
  { id: "generate", label: "生成" },
  { id: "continuity", label: "连贯性" },
  { id: "sound", label: "声音" },
  { id: "advanced", label: "高级" },
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

const REROLL_REASONS: Array<{ code: RerollReasonCode; label: string }> = [
  { code: "IDENTITY_FIX", label: "人物不一致" },
  { code: "MOTION_FIX", label: "动作不对" },
  { code: "COMPOSITION_FIX", label: "构图不对" },
  { code: "PROMPT_TUNE", label: "镜头语言不对" },
  { code: "CONTINUITY_FIX", label: "连续性问题" },
  { code: "OTHER", label: "其他" },
];

function firstText(fields: Record<string, unknown>, keys: string[], fallback: string) {
  for (const key of keys) {
    const value = fields[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return fallback;
}

type GenerationResolution = {
  capability?: string;
  source?: string;
  mode?: string;
  profile?: { code?: string; title?: string; version_no?: number } | null;
  blocked_reason?: string | null;
};

function generationResolutions(value: Record<string, unknown> | undefined): GenerationResolution[] {
  const items = value?.resolutions;
  return Array.isArray(items) ? items.filter((item): item is GenerationResolution => Boolean(item && typeof item === "object")) : [];
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
  const queryClient = useQueryClient();
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("picture");
  const [sourceOpen, setSourceOpen] = useState(false);
  const [timelineOpen, setTimelineOpen] = useState(true);
  const [resampleOpen, setResampleOpen] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [adoptionOpen, setAdoptionOpen] = useState(false);
  const [navDrawerOpen, setNavDrawerOpen] = useState(false);
  const [inspectorDrawerOpen, setInspectorDrawerOpen] = useState(false);
  const [rerollReason, setRerollReason] = useState<RerollReasonCode>("USER_REROLL");
  const [activeCandidateId, setActiveCandidateId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");
  const modalOpen = sourceOpen || resampleOpen || compareOpen || approvalOpen || adoptionOpen;

  const desk = useQuery({
    queryKey: ["director-desk-v2", projectId, episodeId, shotId],
    queryFn: () => loadDirectorDesk(projectId, episodeId, shotId),
    enabled: Boolean(projectId && episodeId && shotId),
  });
  const shotChooser = useQuery({
    queryKey: ["episode", episodeId, "director-shot-choice"],
    queryFn: () => getEpisodeProduction(episodeId),
    enabled: Boolean(projectId && episodeId && !shotId),
  });
  useProjectEventInvalidation(
    projectId,
    ["JOB_QUEUED", "JOB_CLAIMED", "JOB_FINISHED", "JOB_REQUEUED", "JOB_RECONCILED", "ARTIFACT_REGISTERED", "SHOT_REVISION_CREATED", "SHOT_PRODUCTION_READY", "FRAME_BRIDGE_INHERITED", "FRAME_BRIDGE_CURRENT_FRAME_SET", "FRAME_BRIDGE_LOCKED", "FRAME_BRIDGE_UNLOCKED"],
    [["director-desk-v2", projectId, episodeId]],
  );
  const shots = desk.data?.shot_nav.items ?? [];
  const selected = desk.data?.current_shot.shot;
  const continuity = useQuery({
    queryKey: ["shot-continuity-context", selected?.id],
    queryFn: () => getShotContinuityContext(selected!.id),
    enabled: Boolean(selected?.id && inspectorTab === "continuity"),
  });

  const candidates = desk.data?.current_shot.candidates ?? [];
  const activeCandidate = candidates.find((item) => item.media_version_id === activeCandidateId)
    ?? candidates.find((item) => item.selected || item.approved)
    ?? candidates[0];
  const activeCandidateIndex = Math.max(0, candidates.findIndex((item) => item.media_version_id === activeCandidate?.media_version_id));
  const comparisonCandidate = candidates.find((item) => item.media_kind === "IMAGE" && item.media_version_id !== activeCandidate?.media_version_id);
  const parentVariantId = activeCandidate?.id ?? desk.data?.current_shot.selected_variant?.id;
  const resolvedGeneration = generationResolutions(desk.data?.current_shot.generation_preferences);
  const refreshDesk = () => queryClient.invalidateQueries({ queryKey: ["director-desk-v2", projectId, episodeId] });
  const selectMutation = useMutation({
    mutationFn: ({ candidate, selectionType }: { candidate: DirectorDeskCandidate; selectionType: DirectorSelectionType }) => selectDirectorCandidate(candidate.media_version_id, selectionType),
    onSuccess: refreshDesk,
  });
  const approveMutation = useMutation({
    mutationFn: (candidate: DirectorDeskCandidate) => approveFormalCandidate(projectId, candidate.media_version_id),
    onSuccess: async () => { setFeedback("正式候选已通过预检并用于交付。"); setApprovalOpen(false); await refreshDesk(); },
    onError: (error) => setFeedback(`批准未提交：${error instanceof Error ? error.message : String(error)}`),
  });
  const rerollMutation = useMutation({
    mutationFn: () => rerollDirectorCandidate(parentVariantId!, rerollReason),
    onSuccess: async (result) => { setFeedback(`新候选已排队（任务 ${result.job.id}）。`); setResampleOpen(false); await refreshDesk(); },
    onError: (error) => setFeedback(`重抽提交失败：${error instanceof Error ? error.message : String(error)}`),
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
    ?? (typeof fields.media_version_id === "string" ? fields.media_version_id : null);
  const stageMediaKind = activeCandidate?.media_kind
    ?? desk.data?.current_shot.current_media?.media_kind
    ?? (typeof fields.media_kind === "string" ? fields.media_kind : null);
  const stageMimeType = activeCandidate?.mime_type ?? desk.data?.current_shot.current_media?.mime_type ?? null;
  const stageDurationMs = activeCandidate?.duration_ms ?? desk.data?.current_shot.current_media?.duration_ms ?? null;
  const sourceExcerpt = desk.data?.current_shot.source_context.source_text
    ?? firstText(fields, ["source_excerpt", "source_text", "excerpt", "dialogue"], "当前镜头尚未关联原文段落。请在分集规划中补充来源范围。");
  const generationHref = selected?.id ? routes.generation(projectId, episodeId, selected.id) : (shotId ? routes.generation(projectId, episodeId, shotId) : routes.generation(projectId, episodeId));

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target;
            if (event.key === "Escape") {
        if (modalOpen) {
          event.preventDefault();
          setSourceOpen(false);
          setResampleOpen(false);
          setCompareOpen(false);
          setApprovalOpen(false);
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
        setNavDrawerOpen((value) => !value);
      }
      if (event.key.toLowerCase() === "i") {
        event.preventDefault();
        setInspectorDrawerOpen((value) => !value);
      }
      if (event.key.toLowerCase() === "g") {
        event.preventDefault();
        setInspectorTab("generate");
        setInspectorDrawerOpen(true);
      }
      if (event.key.toLowerCase() === "f") {
        event.preventDefault();
        setInspectorTab("continuity");
        setInspectorDrawerOpen(true);
      }
      if (event.key.toLowerCase() === "a") {
        event.preventDefault();
        setInspectorTab("assets");
        setInspectorDrawerOpen(true);
      }
      if (event.key === "Enter") {
        event.preventDefault();
        setInspectorTab("picture");
        setInspectorDrawerOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [activeCandidateIndex, candidates, inspectorDrawerOpen, modalOpen, navDrawerOpen]);

  useStudioCommand(useMemo(() => ({ id: "shot.previous", label: "上一镜", description: "保持当前项目与分集上下文", group: "当前页面" as const, shortcut: "J", enabled: () => Boolean(previous && !modalOpen), run: () => { if (previous) navigate(`/projects/${projectId}/episodes/${episodeId}/direct/${previous.id}`); } }), [episodeId, modalOpen, navigate, previous, projectId]));
  useStudioCommand(useMemo(() => ({ id: "shot.next", label: "下一镜", description: "保持当前项目与分集上下文", group: "当前页面" as const, shortcut: "K", enabled: () => Boolean(next && !modalOpen), run: () => { if (next) navigate(`/projects/${projectId}/episodes/${episodeId}/direct/${next.id}`); } }), [episodeId, modalOpen, navigate, next, projectId]));
  useStudioCommand(useMemo(() => ({ id: "shot.generate", label: "打开当前镜头生成设置", description: "先检查模型、资产与资源预检", group: "当前页面" as const, shortcut: "G", enabled: () => Boolean(!modalOpen && selected && desk.data?.permissions.can_generate), run: () => { setInspectorTab("generate"); setInspectorDrawerOpen(true); } }), [desk.data?.permissions.can_generate, modalOpen, selected]));
  useStudioCommand(useMemo(() => ({ id: "shot.reroll", label: "重抽当前镜头", description: "创建新的 Variant 分支，不重试旧 Job", group: "当前页面" as const, shortcut: "R", enabled: () => Boolean(!modalOpen && parentVariantId && desk.data?.permissions.can_generate), run: () => setResampleOpen(true) }), [desk.data?.permissions.can_generate, modalOpen, parentVariantId]));
  useStudioCommand(useMemo(() => ({ id: "shot.assets", label: "打开当前镜头资产选择", description: "进入本镜资产绑定，不写入选择前的任何事实", group: "当前页面" as const, shortcut: "A", enabled: () => !modalOpen, run: () => { setInspectorTab("assets"); setInspectorDrawerOpen(true); } }), [modalOpen]));
  useStudioCommand(useMemo(() => ({ id: "shot.frame-bridge", label: "打开镜头桥", description: "管理首尾帧来源、锁定与 stale", group: "当前页面" as const, shortcut: "F", enabled: () => Boolean(!modalOpen && frameBridge), run: () => { setInspectorTab("continuity"); setInspectorDrawerOpen(true); } }), [frameBridge, modalOpen]));
  useStudioCommand(useMemo(() => ({ id: "shot.compare", label: "并排比较候选", description: "支持 2-up / 4-up 与统一视频播放", group: "当前页面" as const, shortcut: "C", enabled: () => candidates.length > 1 && !modalOpen, run: () => setCompareOpen(true) }), [candidates.length, modalOpen]));
  useStudioCommand(useMemo(() => ({ id: "shot.details", label: "打开当前镜头详情", description: "聚焦画面与镜头 Inspector", group: "当前页面" as const, shortcut: "Enter", enabled: () => !modalOpen, run: () => { setInspectorTab("picture"); setInspectorDrawerOpen(true); } }), [modalOpen]));
  useStudioCommand(useMemo(() => ({ id: "shot.candidate.1", label: "聚焦候选 1", group: "当前页面" as const, shortcut: "1", enabled: () => Boolean(!modalOpen && candidates[0]), run: () => { if (candidates[0]) setActiveCandidateId(candidates[0].media_version_id); } }), [candidates, modalOpen]));
  useStudioCommand(useMemo(() => ({ id: "shot.candidate.2", label: "聚焦候选 2", group: "当前页面" as const, shortcut: "2", enabled: () => Boolean(!modalOpen && candidates[1]), run: () => { if (candidates[1]) setActiveCandidateId(candidates[1].media_version_id); } }), [candidates, modalOpen]));
  useStudioCommand(useMemo(() => ({ id: "shot.toggle-nav", label: "切换镜头导航抽屉", description: "在紧凑视口下展开或收起镜头导航", group: "当前页面" as const, shortcut: "N", enabled: () => !modalOpen, run: () => setNavDrawerOpen((value) => !value) }), [modalOpen]));
  useStudioCommand(useMemo(() => ({ id: "shot.toggle-inspector", label: "切换检查器抽屉", description: "在紧凑视口下展开或收起检查器", group: "当前页面" as const, shortcut: "I", enabled: () => !modalOpen, run: () => setInspectorDrawerOpen((value) => !value) }), [modalOpen]));
  useStudioCommand(useMemo(() => ({ id: "shot.candidate.3", label: "聚焦候选 3", group: "当前页面" as const, shortcut: "3", enabled: () => Boolean(!modalOpen && candidates[2]), run: () => { if (candidates[2]) setActiveCandidateId(candidates[2].media_version_id); } }), [candidates, modalOpen]));

  if (!shotId) {
    if (shotChooser.isLoading) return <div className="director-loading" role="status">正在读取本集镜头…</div>;
    if (shotChooser.error) return <div className="director-error" role="alert"><strong>无法读取镜头列表</strong><span>{shotChooser.error instanceof Error ? shotChooser.error.message : String(shotChooser.error)}</span><button type="button" className="secondary" onClick={() => void shotChooser.refetch()}>重试</button></div>;
    const chooserItems = shotChooser.data?.items ?? [];
    return <section className="director-shot-choice" aria-labelledby="director-shot-choice-title"><div><p className="eyebrow">导演台入口</p><h2 id="director-shot-choice-title">先选择要精修的镜头</h2><p>未指定镜头时不会静默打开第一镜，也不会启用生成、重抽或批准操作。</p></div>{chooserItems.length ? <nav aria-label="选择导演镜头">{chooserItems.map((shot, index) => <Link key={String(shot.id)} to={routes.directorDesk(projectId, episodeId, String(shot.id))}><span>{String(shot.code ?? `镜头 ${index + 1}`)}</span><small>{String(shot.status ?? "未开始")}</small></Link>)}</nav> : <p className="empty-state">本集还没有镜头，请先在分集规划中创建镜头。</p>}<Link className="secondary v2-inline-link" to={routes.episodePlan(projectId, episodeId)}>返回分集规划</Link></section>;
  }
  if (desk.isLoading) return <div className="director-loading" role="status">正在打开导演台…</div>;
  if (desk.error) return <div className="director-error" role="alert"><strong>导演台暂时无法打开</strong><span>{desk.error instanceof Error ? desk.error.message : String(desk.error)}</span><Link to={`/projects/${projectId}/episodes/${episodeId}/plan`}>返回分集规划</Link></div>;
  if (!selected) return <div className="director-error"><strong>本集还没有镜头</strong><span>先在分集规划中创建镜头，再进入导演台精修。</span><Link to={`/projects/${projectId}/episodes/${episodeId}/plan`}>前往分集规划</Link></div>;

  return (
    <div className={`director-desk${timelineOpen ? " timeline-open" : ""}`}>
      <header className="director-contextbar">
        <nav aria-label="当前位置"><Link to={`/projects/${projectId}`}>{desk.data?.project.name ?? "项目"}</Link><span>/</span><Link to={`/projects/${projectId}/episodes/${episodeId}/plan`}>{desk.data?.episode.title ?? desk.data?.episode.code ?? "本集"}</Link><span>/</span><strong>{selected.code}</strong></nav>
        <div className="director-context-actions">
          <button type="button" className="director-button ghost desk-nav-toggle" aria-keyshortcuts="N" aria-expanded={navDrawerOpen} aria-label="切换镜头列表 (N)" onClick={() => setNavDrawerOpen((value) => !value)}><DeskIcon name="collapse" /><span>镜头列表 <kbd>N</kbd></span></button>
          <button type="button" className="director-button ghost desk-inspector-toggle" aria-keyshortcuts="I" aria-expanded={inspectorDrawerOpen} aria-label="切换检查器 (I)" onClick={() => setInspectorDrawerOpen((value) => !value)}><DeskIcon name="frame" /><span>检查器 <kbd>I</kbd></span></button>
          <button type="button" className="director-button ghost" aria-keyshortcuts="O" onClick={() => setSourceOpen(true)}><DeskIcon name="source" />查看原文 <kbd>O</kbd></button>
          <button type="button" className="director-button ghost" aria-keyshortcuts="C" disabled={candidates.length < 2} title={candidates.length < 2 ? "至少需要两个候选才能并排比较" : undefined} onClick={() => setCompareOpen(true)}><DeskIcon name="compare" />并排比较</button>
          <Link className="director-button ghost" to={`/projects/${projectId}/episodes/${episodeId}/review`}><DeskIcon name="compare" />集审核</Link>
          <button type="button" className="director-button primary" disabled={!parentVariantId || !desk.data?.permissions.can_generate} title={!desk.data?.permissions.can_generate ? "当前权限不允许生成候选" : !parentVariantId ? "当前没有可作为父节点的候选" : undefined} onClick={() => setResampleOpen(true)}>重抽当前镜头</button>
        </div>
      </header>

      <div className={`director-main-grid${navDrawerOpen ? " nav-drawer-open" : ""}${inspectorDrawerOpen ? " inspector-drawer-open" : ""}`}>
        <div className={`director-shot-nav-container${navDrawerOpen ? " drawer-open" : ""}`}>
          {navDrawerOpen && <div className="director-drawer-backdrop" role="presentation" onClick={() => setNavDrawerOpen(false)} />}
          <div className="director-shot-nav-inner">
            <div className="director-drawer-close-bar">
              <span>镜头导航</span>
              <button type="button" aria-label="关闭镜头导航" onClick={() => setNavDrawerOpen(false)}>×</button>
            </div>
        <ShotNavigator shots={shots} selectedId={selected.id} projectId={projectId} episodeId={episodeId} canEdit={Boolean(desk.data?.permissions.can_edit && !desk.data?.read_only)} onChanged={refreshDesk} />
          </div>
        </div>

        <main className="director-stage" aria-label="当前镜头媒体舞台">
          <div className="director-stage-toolbar">
            <div><span className="director-kicker">当前镜头</span><h2>{selected.code} · {firstText(fields, ["title", "summary"], selected.shot_type || "未命名镜头")}</h2></div>
            <div className="director-stepper">
              {previous ? <Link aria-label="上一镜，快捷键 J" aria-keyshortcuts="J" to={`/projects/${projectId}/episodes/${episodeId}/direct/${previous.id}`}><DeskIcon name="previous" /></Link> : <button type="button" disabled aria-label="已经是第一镜"><DeskIcon name="previous" /></button>}
              <span>{currentIndex + 1} / {desk.data?.shot_nav.total ?? shots.length}</span>
              {next ? <Link aria-label="下一镜，快捷键 K" aria-keyshortcuts="K" to={`/projects/${projectId}/episodes/${episodeId}/direct/${next.id}`}><DeskIcon name="next" /></Link> : <button type="button" disabled aria-label="已经是最后一镜"><DeskIcon name="next" /></button>}
            </div>
          </div>
          <DirectorMediaStage label={`${selected.code} 当前选中媒体`} media={stageMediaId ? { mediaVersionId: stageMediaId, mediaKind: stageMediaKind, mimeType: stageMimeType, durationMs: stageDurationMs } : null} comparisonMedia={comparisonCandidate ? { mediaVersionId: comparisonCandidate.media_version_id, mediaKind: comparisonCandidate.media_kind, mimeType: comparisonCandidate.mime_type } : null} badges={[selected.shot_type || "镜头类型未设", `${Math.round(selected.target_duration_ms / 100) / 10}s`, revision?.is_frozen ? "修订已锁定" : "可编辑"]} onEmptyAction={() => navigate(generationHref)} keyboardShortcutsEnabled={!modalOpen} />
          <div className="director-frame-bridge" aria-label="镜头桥 Frame Bridge">
            <div><span className="frame-node"><DeskIcon name="frame" /><small>上一镜尾帧</small><strong>{frameBridge?.previous?.from_shot_code ?? previous?.code ?? "无"}</strong></span><span className="frame-line" aria-hidden="true" /><span className="frame-node active"><DeskIcon name="frame" /><small>本镜首帧</small><strong>{frameBridge?.current_start ? frameBridge.current_start.status : "待选择"}</strong></span><span className="frame-line" aria-hidden="true" /><span className="frame-node"><DeskIcon name="frame" /><small>本镜尾帧</small><strong>{frameBridge?.current_end ? frameBridge.current_end.status : next ? "连接下一镜" : "片尾"}</strong></span></div>
            <button type="button" className="director-text-button" onClick={() => setInspectorTab("continuity")}>管理来源与锁定</button>
          </div>
        </main>

        <div className={`director-inspector-container${inspectorDrawerOpen ? " drawer-open" : ""}`}>
          {inspectorDrawerOpen && <div className="director-drawer-backdrop" role="presentation" onClick={() => setInspectorDrawerOpen(false)} />}
          <aside className="director-inspector" aria-label="镜头检查器">
          <div className="director-zone-title"><div><span>Inspector</span><strong>{STATUS_LABELS[selected.status] ?? selected.status}</strong></div><button type="button" className="director-inspector-close" aria-label="收起检查器" onClick={() => setInspectorDrawerOpen(false)}>×</button></div>
          <div className="director-inspector-tabs" role="tablist" aria-label="检查器分类">
            {INSPECTOR_TABS.map((tab) => <button key={tab.id} type="button" role="tab" aria-selected={inspectorTab === tab.id} aria-keyshortcuts={tab.id === "generate" ? "G" : tab.id === "continuity" ? "F" : tab.id === "assets" ? "A" : tab.id === "picture" ? "Enter" : undefined} onClick={() => { setInspectorTab(tab.id); setInspectorDrawerOpen(true); }}>{tab.label}</button>)}
          </div>
          <div className="director-inspector-body">
            {inspectorTab === "picture" && <DirectorIntentEditor shotId={selected.id} shotCode={selected.code} currentRevision={revision ?? null} targetDurationMs={selected.target_duration_ms} shotType={selected.shot_type} blockers={desk.data?.current_shot.blockers ?? []} canEdit={desk.data?.permissions.can_edit ?? false} keyboardShortcutsEnabled={!modalOpen} onSaved={async () => { await refreshDesk(); }} onReloadRequested={() => { void refreshDesk(); }} />}
            {inspectorTab === "assets" && <><div className="director-section-head"><strong>本镜资产与角色</strong><Link to={`/projects/${projectId}/assets`}>打开资产圣经</Link></div><p className="director-help">绑定与解绑会调用正式镜头资产命令；资产状态继续由 Asset Bible / 分集策划的 0042 命令管理。</p><ShotAssetSection projectId={projectId} shotId={selected.id} canEdit={Boolean(desk.data?.permissions.can_edit)} /></>}
            {inspectorTab === "generate" && <><div className="director-section-head"><strong>本镜生效生成配置</strong><Link to={`/projects/${projectId}/production-settings`}>管理生产设置</Link></div><div className="director-placeholder-list">{resolvedGeneration.map((resolution) => <span key={resolution.capability ?? "unknown"}><strong>{resolution.capability ?? "未知能力"}</strong> · {resolution.blocked_reason ? `阻塞：${resolution.blocked_reason}` : resolution.profile?.title ?? resolution.profile?.code ?? resolution.mode ?? "AUTO"}<small>{resolution.source ? `来源：${resolution.source}` : "由 Shot → Episode → Project → AUTO 解析"}</small></span>)}{resolvedGeneration.length === 0 && <span>尚无可解析的生成偏好；提交时不会静默选择其他能力。</span>}</div>{parentVariantId ? <button type="button" className="director-button primary wide" onClick={() => setResampleOpen(true)}>创建新候选分支</button> : <Link className="director-button primary wide" to={generationHref}>预检并生成首个候选</Link>}<p className="director-help">候选数量与质量策略由整集生产模式或生成工作台的真实提交计划决定；这里不保存临时假设置。提交前会检查模型版本、磁盘、显存和素材约束。</p></>}
            {inspectorTab === "continuity" && <>
              {frameBridge && <FrameBridgeControls frameBridge={frameBridge} currentCandidate={activeCandidate ?? null} canEdit={desk.data?.permissions.can_edit ?? false} onChanged={async () => { await refreshDesk(); void continuity.refetch(); }} />}
              {continuity.isLoading && <p className="loading-state" role="status">正在读取前后镜头连续性…</p>}
              {continuity.isError && <p className="inline-error" role="alert">连续性对照读取失败：{continuity.error instanceof Error ? continuity.error.message : String(continuity.error)}</p>}
              {!continuity.isLoading && !continuity.isError && <ContinuityPanel context={continuity.data?.continuity} />}
            </>}
            {inspectorTab === "sound" && <DirectorSoundInspector projectId={projectId} episodeId={episodeId} shotCode={selected.code} dialogue={firstText(fields, ["dialogue", "narration", "source_excerpt"], "本镜无台词")} assets={desk.data?.current_shot.assets ?? []} />}
            {inspectorTab === "advanced" && <><div className="director-continuity-list"><div><span>修订号</span><strong>v{revision?.revision_no ?? selected.revision ?? 0}</strong></div><div><span>镜头 ID</span><code>{selected.id}</code></div><div><span>冻结</span><strong>{revision?.is_frozen ? "是" : "否"}</strong></div><div><span>活动任务</span><strong>{desk.data?.current_shot.active_jobs.length ?? 0}</strong></div></div><p className="director-help">高级字段只展示真实生产标识，不影响常规导演操作。</p></>}
          </div>
        </aside>
        </div>
      </div>

      <section className="director-timeline" aria-label="候选与时间线">
        <button type="button" className="director-timeline-toggle" aria-expanded={timelineOpen} onClick={() => setTimelineOpen((value) => !value)}><DeskIcon name="collapse" /><span><strong>Takes & Timeline</strong><small>{candidates.length} 个候选 · 本集 {desk.data?.episode.shot_count ?? 0} 镜 · {desk.data?.episode.approved_count ?? 0} 已批准</small></span></button>
        {timelineOpen && <div className="director-takes-strip">
          {candidates.length > 0 && <DirectorTakeAdoption
            candidates={candidates}
            activeCandidateId={activeCandidate?.media_version_id ?? null}
            currentCandidateId={desk.data?.current_shot.current_media?.media_version_id ?? null}
            pending={selectMutation.isPending}
            onActivate={setActiveCandidateId}
            onAdopt={(candidate, selectionType) => selectMutation.mutateAsync({ candidate, selectionType }).then(() => undefined)}
            onFeedback={setFeedback}
            onDialogOpenChange={setAdoptionOpen}
            renderSecondaryAction={(candidate) => candidate.stage === "FORMAL" && candidate.media_kind === "VIDEO" ? <button type="button" disabled={!desk.data?.permissions.can_approve || approveMutation.isPending} title={!desk.data?.permissions.can_approve ? "当前权限不允许批准交付" : approveMutation.isPending ? "正在提交批准" : "打开批准确认"} onClick={() => { setActiveCandidateId(candidate.media_version_id); setApprovalOpen(true); }}>用于交付</button> : null}
          />}
          {candidates.length === 0 && <div className="director-take-empty"><strong>还没有候选</strong><span>生成后可在这里同屏比较；选择与批准始终是两个动作。</span></div>}
          {parentVariantId ? <button type="button" className="director-new-take" onClick={() => setResampleOpen(true)}>+ 重抽候选</button> : <Link className="director-new-take" to={generationHref}>+ 生成首个候选</Link>}
        </div>}
      </section>

      <div className="director-feedback" role="status" aria-live="polite">{feedback || (desk.data?.current_shot.active_jobs.length ? `${desk.data.current_shot.active_jobs.length} 个生成任务正在执行` : "")}</div>

      {sourceOpen && <div className="director-drawer-scrim" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setSourceOpen(false)}><aside className="director-source-drawer" role="dialog" aria-modal="true" aria-labelledby="source-title"><div className="director-drawer-head"><div><span className="director-kicker">原文对照</span><h2 id="source-title">{selected.code} 来源片段</h2></div><button type="button" aria-label="关闭原文" onClick={() => setSourceOpen(false)}>×</button></div><DirectorSourcePassage projectId={projectId} sourceContext={desk.data?.current_shot.source_context ?? {}} fallbackExcerpt={sourceExcerpt} /><div className="director-source-meta"><span>镜头：{selected.code}</span><span>修订：v{revision?.revision_no ?? selected.revision ?? 0}</span></div><Link className="director-button ghost" to={`/projects/${projectId}/episodes/${episodeId}/plan#plan-source`}>在分集规划中查看原文</Link></aside></div>}

      {resampleOpen && <div className="director-dialog-scrim" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setResampleOpen(false)}><section className="director-dialog" role="dialog" aria-modal="true" aria-labelledby="resample-title"><div><span className="director-kicker">创作变体，不是失败重试</span><h2 id="resample-title">为什么要重抽 {selected.code}？</h2><p>将以当前候选 Variant 为父节点创建新分支并排队，原因会写入版本血缘。</p></div><div className="director-reason-grid">{REROLL_REASONS.map((reason) => <button key={reason.code} className={rerollReason === reason.code ? "selected" : ""} aria-pressed={rerollReason === reason.code} type="button" onClick={() => setRerollReason(reason.code)}>{reason.label}</button>)}</div><div className="director-dialog-actions"><button type="button" className="director-button ghost" onClick={() => setResampleOpen(false)}>取消</button><button type="button" className="director-button primary" disabled={!parentVariantId || rerollMutation.isPending} onClick={() => rerollMutation.mutate()}>{rerollMutation.isPending ? "正在提交…" : "创建并排队"}</button></div>{!parentVariantId && <p className="director-help">当前没有可作为父节点的 Variant；请先通过生成工作台创建首个候选。</p>}<p className="director-help">任务执行失败请前往任务详情使用“重试”，不会创建新的创作候选。</p></section></div>}
      {approvalOpen && activeCandidate && <div className="director-dialog-scrim" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setApprovalOpen(false)}><section className="director-dialog" role="alertdialog" aria-modal="true" aria-labelledby="approval-title" aria-describedby="approval-description"><div><span className="director-kicker">人工交付门禁</span><h2 id="approval-title">确认将 Take {activeCandidate.take_no ?? activeCandidate.variant_no} 用于交付？</h2><p id="approval-description">这会提交正式人工批准，并将当前 FORMAL 视频指向交付。机器检查通过不等于本次人工批准；“用于交付”只打开本确认框，不会直接写入。</p></div><div className="director-dialog-actions"><button type="button" className="director-button ghost" autoFocus onClick={() => setApprovalOpen(false)}>取消</button><button type="button" className="director-button primary" disabled={approveMutation.isPending} title={approveMutation.isPending ? "正在提交人工批准" : undefined} onClick={() => approveMutation.mutate(activeCandidate)}>{approveMutation.isPending ? "正在批准…" : "确认批准用于交付"}</button></div></section></div>}
      {compareOpen && <CandidateCompareDialog candidates={candidates} initialCandidateId={activeCandidate?.media_version_id} onClose={() => setCompareOpen(false)} />}
    </div>
  );
}
