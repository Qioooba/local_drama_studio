import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  applyScriptBreakdownDraft,
  listEpisodes,
  listScriptBreakdownDrafts,
  listSeasons,
  reviseScriptBreakdownDraftScene,
  type BreakdownDraftSceneRevisionPayload,
  type ScriptBreakdownDraft,
} from "../../generated/api";
import { queryKeys } from "../../query/queryKeys";
import { requestScriptBreakdown } from "../story-workspace-v2/breakdownClient";

function scenesOf(item: ScriptBreakdownDraft): Array<Record<string, unknown>> {
  return Array.isArray(item.draft.scenes) ? item.draft.scenes : [];
}

function shotsOf(scene: Record<string, unknown>): Array<Record<string, unknown>> {
  return Array.isArray(scene.shots) ? scene.shots.filter((shot): shot is Record<string, unknown> => Boolean(shot && typeof shot === "object")) : [];
}

function durationSeconds(item: ScriptBreakdownDraft): number {
  return scenesOf(item).reduce(
    (sceneTotal, scene) => sceneTotal + shotsOf(scene).reduce((shotTotal, shot) => shotTotal + Math.max(0, Number(shot.duration_seconds ?? 0)), 0),
    0,
  );
}

function formatSeconds(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1).replace(/\.0$/, "");
}

function dialogueText(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(dialogueText).filter(Boolean).join("；");
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const entry = value as Record<string, unknown>;
    if (typeof entry.text === "string") return typeof entry.speaker === "string" && entry.speaker ? `${entry.speaker}：${entry.text}` : entry.text;
  }
  return value == null ? "" : "";
}

function SceneRevisionEditor({
  scene,
  sceneNo,
  draftRevision,
  busy,
  error,
  onCancel,
  onSave,
}: {
  scene: Record<string, unknown>;
  sceneNo: number;
  draftRevision: number;
  busy: boolean;
  error?: unknown;
  onCancel: () => void;
  onSave: (payload: BreakdownDraftSceneRevisionPayload) => void;
}) {
  const sourceShots = shotsOf(scene);
  const [title, setTitle] = useState(String(scene.title ?? ""));
  const [summary, setSummary] = useState(String(scene.summary ?? ""));
  const [characters, setCharacters] = useState(Array.isArray(scene.characters) ? scene.characters.map(String).join("、") : "");
  const [shots, setShots] = useState(() => sourceShots.map((shot, index) => ({
    shot_no: Number(shot.shot_no ?? index + 1),
    visual: String(shot.visual ?? ""),
    action: String(shot.action ?? ""),
    dialogue: dialogueText(shot.dialogue),
    duration_seconds: Math.max(0, Number(shot.duration_seconds ?? 0)),
  })));
  const [changeNote, setChangeNote] = useState("");
  const durationIsValid = (value: number) => Number.isFinite(value) && value >= 1 && value <= 15;
  const valid = Boolean(title.trim() && changeNote.trim().length >= 2 && shots.every((shot) => durationIsValid(shot.duration_seconds)));
  const updateShot = (index: number, field: "visual" | "action" | "dialogue" | "duration_seconds", value: string | number) => {
    setShots((current) => current.map((shot, position) => position === index ? { ...shot, [field]: value } : shot));
  };
  return <div className="breakdown-apply-area" aria-label={`编辑场 ${sceneNo}`}>
    <p className="review-guidance">保存会创建可审计的人工修订；模型原始输出不会被覆盖。镜头数量、编号和顺序保持锁定。</p>
    <label>场次标题<input aria-label={`场 ${sceneNo} 标题`} value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} /></label>
    <label>场次摘要<textarea aria-label={`场 ${sceneNo} 摘要`} value={summary} maxLength={4000} onChange={(event) => setSummary(event.target.value)} /></label>
    <label>出场角色<input aria-label={`场 ${sceneNo} 出场角色`} value={characters} onChange={(event) => setCharacters(event.target.value)} placeholder="用顿号或逗号分隔" /></label>
    {shots.map((shot, index) => <fieldset key={shot.shot_no}>
      <legend>镜 {shot.shot_no}</legend>
      <label>画面<textarea aria-label={`场 ${sceneNo} 镜 ${shot.shot_no} 画面`} value={shot.visual} maxLength={4000} onChange={(event) => updateShot(index, "visual", event.target.value)} /></label>
      <label>动作<textarea aria-label={`场 ${sceneNo} 镜 ${shot.shot_no} 动作`} value={shot.action} maxLength={4000} onChange={(event) => updateShot(index, "action", event.target.value)} /></label>
      <label>对白<textarea aria-label={`场 ${sceneNo} 镜 ${shot.shot_no} 对白`} value={String(shot.dialogue)} maxLength={8000} onChange={(event) => updateShot(index, "dialogue", event.target.value)} /></label>
      <label>时长（秒）<input aria-label={`场 ${sceneNo} 镜 ${shot.shot_no} 时长`} type="number" min="1" max="15" step="0.1" value={shot.duration_seconds} aria-invalid={!durationIsValid(shot.duration_seconds)} onChange={(event) => updateShot(index, "duration_seconds", Number(event.target.value))} />{!durationIsValid(shot.duration_seconds) && <small className="inline-error" role="alert">单镜时长必须在 1–15 秒之间</small>}</label>
    </fieldset>)}
    <label>修改说明<input aria-label={`场 ${sceneNo} 修改说明`} value={changeNote} maxLength={500} onChange={(event) => setChangeNote(event.target.value)} placeholder="例如：校正动作与镜头节奏" /></label>
    <div className="inline-control"><button type="button" className="secondary" onClick={onCancel} disabled={busy}>取消</button><button type="button" disabled={!valid || busy} onClick={() => onSave({
      expected_revision: draftRevision,
      change_note: changeNote.trim(),
      title: title.trim(),
      summary: summary.trim(),
      characters: characters.split(/[、,，]/).map((name) => name.trim()).filter(Boolean),
      shots,
    })}>{busy ? "保存修订中…" : "保存人工修订"}</button></div>
    {Boolean(error) && <p className="inline-error" role="alert">{String(error)}</p>}
  </div>;
}

function DraftTree({
  item,
  selectedSceneNos,
  onToggleScene,
  onReviseScene,
  revisingSceneNo,
  revisionError,
}: {
  item: ScriptBreakdownDraft;
  selectedSceneNos: number[];
  onToggleScene: (sceneNo: number, selected: boolean) => void;
  onReviseScene: (sceneNo: number, payload: BreakdownDraftSceneRevisionPayload) => void;
  revisingSceneNo?: number;
  revisionError?: unknown;
}) {
  const [editingSceneNo, setEditingSceneNo] = useState<number>();
  useEffect(() => {
    setEditingSceneNo(undefined);
  }, [item.effective_draft_revision_no]);
  const questions = item.confidence.questions ?? [];
  const passages = item.confidence.source_passages ?? [];
  const appliedSceneNos = new Set(item.applied_scene_nos ?? []);
  return <details className="ai-draft-details">
    <summary>展开完整草稿 · 逐场逐镜审阅</summary>
    <div className="ai-draft-tree">
      {scenesOf(item).map((scene, sceneIndex) => {
        const sceneNo = Number(scene.scene_no ?? sceneIndex + 1);
        const sceneApplied = appliedSceneNos.has(sceneNo);
        return <section key={[String(scene.scene_no ?? sceneIndex), sceneIndex].join("-")}>
        <h4><label><input type="checkbox" aria-label={`选择场 ${sceneNo}`} checked={sceneApplied || selectedSceneNos.includes(sceneNo)} disabled={sceneApplied} onChange={(event) => onToggleScene(sceneNo, event.target.checked)} />场 {sceneNo} · {String(scene.title ?? "未命名场次")}</label>{sceneApplied && <span className="status-pill">已应用</span>}</h4>
        {!sceneApplied && editingSceneNo !== sceneNo && <button type="button" className="secondary" aria-label={`编辑场 ${sceneNo}`} onClick={() => setEditingSceneNo(sceneNo)}>编辑场次字段</button>}
        {editingSceneNo === sceneNo && !sceneApplied
          ? <SceneRevisionEditor scene={scene} sceneNo={sceneNo} draftRevision={item.revision} busy={revisingSceneNo === sceneNo} error={revisingSceneNo === sceneNo ? revisionError : undefined} onCancel={() => setEditingSceneNo(undefined)} onSave={(payload) => onReviseScene(sceneNo, payload)} />
          : <>{Boolean(scene.summary) && <p>{String(scene.summary)}</p>}
        {Array.isArray(scene.characters) && scene.characters.length > 0 && <p className="muted">出场：{scene.characters.map(String).join("、")}</p>}
        <ol>{shotsOf(scene).map((shot, shotIndex) => <li key={[String(shot.shot_no ?? shotIndex), shotIndex].join("-")}>
          <strong>镜 {String(shot.shot_no ?? shotIndex + 1)}</strong>
          {Boolean(shot.visual) && <span>画面：{String(shot.visual)}</span>}
          {Boolean(shot.action) && <span>动作：{String(shot.action)}</span>}
          {Boolean(dialogueText(shot.dialogue)) && <span>对白：{dialogueText(shot.dialogue)}</span>}
          <small>{formatSeconds(Math.max(0, Number(shot.duration_seconds ?? 0)))} 秒</small>
        </li>)}</ol></>}
      </section>;
      })}
      {questions.length > 0 && <section><h4>待确认问题</h4><ul>{questions.map((question) => <li key={question}>{question}</li>)}</ul></section>}
      {passages.length > 0 && <section><h4>原文依据</h4><ul>{passages.map((passage, index) => <li key={[passage.scene_no, passage.source_start, index].join("-")}><q>{passage.quote}</q><small>场 {passage.scene_no} · 字符 {passage.source_start}–{passage.source_end}</small></li>)}</ul></section>}
    </div>
  </details>;
}

export function AIDraftReviewPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const drafts = useQuery({ queryKey: queryKeys.scriptBreakdown.all(projectId), queryFn: () => listScriptBreakdownDrafts(projectId), refetchInterval: 3_000 });
  const seasons = useQuery({ queryKey: queryKeys.seasons.list(projectId), queryFn: () => listSeasons(projectId) });
  const [seasonId, setSeasonId] = useState("");
  const effectiveSeasonId = seasonId || seasons.data?.items[0]?.id || "";
  const episodes = useQuery({
    queryKey: queryKeys.episodes.list(effectiveSeasonId || "missing"),
    queryFn: () => listEpisodes(effectiveSeasonId),
    enabled: Boolean(effectiveSeasonId),
  });
  const defaultEpisodeId = episodes.data?.items[0]?.id;
  const [selectedEpisode, setSelectedEpisode] = useState<Record<string, string>>({});
  const [reviewed, setReviewed] = useState<Record<string, boolean>>({});
  const [selectedScenes, setSelectedScenes] = useState<Record<string, number[]>>({});
  const [retryNotice, setRetryNotice] = useState<Record<string, string>>({});

  useEffect(() => {
    if (seasonId || !seasons.data?.items[0]?.id) return;
    setSeasonId(seasons.data.items[0].id);
  }, [seasonId, seasons.data?.items]);

  const apply = useMutation({
    mutationFn: ({ draftId, episodeId, sceneNos }: { draftId: string; episodeId: string; sceneNos: number[] }) => applyScriptBreakdownDraft(draftId, { episode_id: episodeId, scene_nos: sceneNos }),
    onSuccess: (_result, variables) => {
      setSelectedScenes((current) => { const next = { ...current }; delete next[variables.draftId]; return next; });
      setReviewed((current) => ({ ...current, [variables.draftId]: false }));
      void queryClient.invalidateQueries({ queryKey: queryKeys.scriptBreakdown.all(projectId) });
    },
  });
  const retry = useMutation({
    mutationFn: async ({ item, episodeId }: { item: ScriptBreakdownDraft; episodeId: string }) => {
      if (!item.profile_version_id) throw new Error("草稿未记录 Profile，无法安全重新生成");
      return {
        draftId: item.id,
        result: await requestScriptBreakdown(
          item.import_session_id,
          item.profile_version_id,
          episodeId,
          ["safe-review", item.id, episodeId, Date.now()].join(":"),
          {
            sourceParagraphStart: item.confidence.source_paragraph_start,
            sourceParagraphEnd: item.confidence.source_paragraph_end,
          },
        ),
      };
    },
    onSuccess: ({ draftId, result }) => {
      setRetryNotice((current) => ({ ...current, [draftId]: "新的安全复检任务已排队：" + result.job.id.slice(0, 12) }));
    },
  });
  const revise = useMutation({
    mutationFn: ({ draftId, sceneNo, payload }: { draftId: string; sceneNo: number; payload: BreakdownDraftSceneRevisionPayload }) => reviseScriptBreakdownDraftScene(draftId, sceneNo, payload),
    onSuccess: (_result, variables) => {
      setReviewed((current) => ({ ...current, [variables.draftId]: false }));
      void queryClient.invalidateQueries({ queryKey: queryKeys.scriptBreakdown.all(projectId) });
    },
  });

  return <section className="ai-draft-panel" aria-labelledby="ai-draft-title">
    <div className="panel-heading"><div><p className="eyebrow">AI 草稿 · 人工确认</p><h3 id="ai-draft-title">AI 辅助提取草稿</h3></div><span className="status-pill">{drafts.data?.items.length ?? 0} 份</span></div>
    <p className="review-guidance">模型输出只保存为待审草稿，不会自动创建或覆盖正式场次、镜头或创作资料。展开核对后才能应用。</p>
    {seasons.data && seasons.data.items.length > 1 && <label>目标季度<select aria-label="选择目标季度" value={effectiveSeasonId} onChange={(event) => { setSeasonId(event.target.value); setSelectedEpisode({}); }}>{seasons.data.items.map((season) => <option key={season.id} value={season.id}>{season.code} · {season.title}</option>)}</select></label>}
    {drafts.isPending && <p className="empty-state">正在读取本地草稿…</p>}
    {drafts.error && <div className="query-error-actions"><p className="inline-error" role="alert">{String(drafts.error)}</p><button type="button" className="secondary" onClick={() => void drafts.refetch()} disabled={drafts.isFetching}>{drafts.isFetching ? "正在重试…" : "重新读取草稿"}</button></div>}
    <div className="ai-draft-list">{drafts.data?.items.map((item) => {
      const scenes = scenesOf(item);
      const shotCount = scenes.reduce((count, scene) => count + shotsOf(scene).length, 0);
      const questions = item.confidence.questions ?? [];
      const passages = item.confidence.source_passages ?? [];
      const applied = item.application_status === "APPLIED";
      const partiallyApplied = item.application_status === "PARTIALLY_APPLIED";
      const sceneNos = scenes.map((scene, index) => Number(scene.scene_no ?? index + 1));
      const remainingSceneNos = item.remaining_scene_nos ?? sceneNos.filter((sceneNo) => !(item.applied_scene_nos ?? []).includes(sceneNo));
      const selectedSceneNos = selectedScenes[item.id] ?? remainingSceneNos;
      const busy = apply.isPending && apply.variables?.draftId === item.id;
      const selected = selectedEpisode[item.id] ?? item.confidence.target_episode_id ?? defaultEpisodeId ?? "";
      const episode = episodes.data?.items.find((candidate) => candidate.id === selected);
      const draftSeconds = item.human_edited ? durationSeconds(item) : item.confidence.normalized_total_duration_seconds ?? item.confidence.total_duration_seconds ?? durationSeconds(item);
      const targetSeconds = Number(episode?.target_duration_ms ?? 0) / 1_000;
      const pipelineDraft = item.confidence.source === "story_pipeline";
      const durationContractPassed = item.confidence.duration_contract_status === "PASS" || item.confidence.pipeline_duration_contract_status === "PASS";
      const durationMismatch = Boolean(targetSeconds > 0 && Math.abs(draftSeconds - targetSeconds) > Math.max(0.5, targetSeconds * 0.05) && !durationContractPassed);
      const blockers = item.application_blockers ?? [];
      const canApply = Boolean(selected && selectedSceneNos.length > 0 && reviewed[item.id] && !durationMismatch && blockers.length === 0 && !busy);
      const modelSeconds = item.confidence.model_total_duration_seconds;
      return <article key={item.id}>
        <div><strong>{item.source_document_title}</strong><span>{applied ? "已应用" : partiallyApplied ? "部分应用" : item.status === "DRAFT_READY" ? "待审核" : item.status}</span></div>
        <p>{scenes.length} 个建议场次 · {shotCount} 个建议镜头</p>
        <p className="draft-evidence">{pipelineDraft
          ? `完整文字建档 · ${item.confidence.provider ?? "本地模型"} / ${item.confidence.model ?? "模型未记录"} · 时长${durationContractPassed ? "通过" : "待确认"} ${formatSeconds(draftSeconds)} / ${formatSeconds(Number(item.confidence.target_duration_seconds ?? targetSeconds))} 秒`
          : item.evidence_status === "COMPLETE"
            ? "证据完整 · 置信度 " + Math.round((item.confidence.confidence?.overall ?? 0) * 100) + "% · " + questions.length + " 个待确认问题 · " + passages.length + " 条原文引用"
            : "历史草稿 · 未记录完整 Profile/置信度/问题/原文引用，不补造证据"}</p>
        {item.human_edited && <p className="frame-feedback success">人工修订 v{item.effective_draft_revision_no} · 原始模型输出已保留；应用将采用当前修订。</p>}
        <small>{item.source_document_code} · Profile {item.profile_version_id ?? "历史未记录"} · 需要人工确认：{applied ? "否" : "是"} · 不会自动应用</small>
        {!applied && <DraftTree item={item} selectedSceneNos={selectedSceneNos} onToggleScene={(sceneNo, selected) => setSelectedScenes((current) => ({ ...current, [item.id]: selected ? [...new Set([...(current[item.id] ?? remainingSceneNos), sceneNo])].sort((left, right) => left - right) : (current[item.id] ?? remainingSceneNos).filter((candidate) => candidate !== sceneNo) }))} onReviseScene={(sceneNo, payload) => revise.mutate({ draftId: item.id, sceneNo, payload })} revisingSceneNo={revise.isPending && revise.variables?.draftId === item.id ? revise.variables.sceneNo : undefined} revisionError={revise.error && revise.variables?.draftId === item.id ? revise.error : undefined} />}
        {modelSeconds !== undefined && item.confidence.duration_adjustment_status === "NORMALIZED_TO_TARGET" && <p className="review-guidance">模型原始合计 {formatSeconds(modelSeconds)} 秒，系统仅将镜头时长确定性归一到 {formatSeconds(draftSeconds)} 秒；{item.human_edited ? `当前内容采用人工修订 v${item.effective_draft_revision_no}。` : "场景与动作未改。"}</p>}
        {(item.confidence.stripped_ungrounded_dialogue_count ?? 0) > 0 && <p className="review-guidance">检测到 {item.confidence.stripped_ungrounded_dialogue_count} 镜对白无法逐字对齐本场原文；已确定性清空并记录原输出哈希。</p>}
        {(item.confidence.extractive_fallback_scene_count ?? 0) > 0 && <p className="review-guidance">检测到 {item.confidence.extractive_fallback_scene_count} 场与引用原文匹配不足；已替换为逐句原文安全镜头并记录原场景哈希。</p>}
        {durationMismatch && <p className="inline-error" role="alert">草稿总时长 {formatSeconds(draftSeconds)} 秒 · 目标 {formatSeconds(targetSeconds)} 秒，超出允许偏差，禁止应用。请重新生成或调整目标集。</p>}
        {blockers.map((blocker) => <p className="inline-error" role="alert" key={blocker.code}>{blocker.message}</p>)}
        {applied
          ? <p className="frame-feedback success">已应用：本草稿的场次/镜头/对白已落地为生产实体，不能重复应用。</p>
          : <div className="breakdown-apply-area" aria-label={"应用到成片 · " + item.source_document_title}>
              {partiallyApplied && <p className="frame-feedback">已应用 {item.applied_scene_nos?.length ?? 0} 场，剩余 {remainingSceneNos.length} 场；已应用场次不可重复选择。</p>}
              <label>应用到成片目标集<span className="inline-control"><select aria-label="选择目标集" value={selected} onChange={(event) => setSelectedEpisode((previous) => ({ ...previous, [item.id]: event.target.value }))}>{episodes.data?.items.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.code} · {candidate.title}</option>)}</select><button type="button" aria-label="应用到成片" className="secondary" disabled={!canApply} onClick={() => { if (canApply) apply.mutate({ draftId: item.id, episodeId: selected, sceneNos: selectedSceneNos }); }}>{busy ? "应用中…" : `应用所选 ${selectedSceneNos.length} 场`}</button></span></label>
              <label><input type="checkbox" checked={Boolean(reviewed[item.id])} onChange={(event) => setReviewed((current) => ({ ...current, [item.id]: event.target.checked }))} />我已展开并审阅所选场次、镜头、对白、原文依据和目标时长，确认将创建正式生产实体</label>
              {!episodes.data?.items.length && !episodes.isPending && <p className="muted">当前季度暂无分集，请先在项目中快速新建一集。</p>}
              {blockers.length > 0 && selected && <button type="button" className="secondary" disabled={retry.isPending && retry.variables?.item.id === item.id} onClick={() => retry.mutate({ item, episodeId: selected })}>{retry.isPending && retry.variables?.item.id === item.id ? "正在排队…" : "按当前原文与目标重新生成"}</button>}
              {retryNotice[item.id] && <p className="frame-feedback success">{retryNotice[item.id]}</p>}
              {retry.error && retry.variables?.item.id === item.id && <p className="inline-error" role="alert">重新生成失败：{String(retry.error)}</p>}
            </div>}
        {apply.data?.apply.draft_id === item.id && <p className="frame-feedback success" aria-label="应用结果摘要">{apply.data.apply.applied ? "草稿全部应用完成" : `所选场次应用完成，剩余 ${apply.data.apply.remaining_scene_nos.length} 场`}：创建 {apply.data.apply.created.scenes} 场 · {apply.data.apply.created.shots} 镜 · {apply.data.apply.created.lines} 条对白；角色 {apply.data.apply.extracted_characters.map((character) => character.name + "×" + character.scene_count).join("、") || "无"}</p>}
        {apply.error && apply.variables?.draftId === item.id && <p className="inline-error" role="alert">{String(apply.error)}</p>}
      </article>;
    })}</div>
    {drafts.data?.items.length === 0 && <p className="empty-state">当前没有本地大语言模型生成的拆解草稿；页面不会用模拟建议填充。</p>}
  </section>;
}
