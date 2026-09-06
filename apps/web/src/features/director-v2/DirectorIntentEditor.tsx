import { useCallback, useEffect, useMemo, useState } from "react";
import { StagingBoard, suggestEyeLineFromStaging, type StagingBoardValue } from "./StagingBoard";
import { LazyDirector3DSpike } from "../director-3d/LazyDirector3DSpike";
import type { Director3DValue } from "../director-3d/types";
import {
  ApiRequestError,
  markShotReadyV2,
  putShotDraftV2,
  resolveProfileCameraPlan,
  type ShotDraftWrite,
  type ShotStudioCapabilityOption,
} from "../../generated/api";
import { ProfileExecutionDetailButton } from "../model-config/ProfileExecutionDetailButton";
import { CAMERA_CURVES, CAMERA_DIRECTION_LABELS, CAMERA_DIRECTIONS, CAMERA_MOVEMENTS, COMPOSITIONS, SHOT_TYPES } from "../shared/directorOptions";
import { EmotionPicker, EyeLineControl, MicroExpressionSelect } from "./DirectorPerformanceControls";
import { notifyDraftDirty } from "../drafts/draftGuard";
import { asRecord, findConfigValue } from "../shared/effectiveDefaults";
import "./director-intent-editor.css";

export type DirectorIntentV3 = {
  schema_version: "director-intent.v3";
  shot_type: string;
  composition: {
    preset: string | null;
    framing: string | null;
    subject_position: string | null;
    headroom: string | null;
    lead_room: string | null;
    screen_direction: string | null;
    axis_rule: string | null;
    depth_plan: string | null;
  };
  subject_action: string;
  performance: {
    emotion: string | null;
    intensity: number | null;
    body_action: string | null;
    facial_action: string | null;
    eye_line: string | null;
    blocking_summary: string | null;
  };
  camera_plan: {
    mode: string;
    shot_type: string;
    movement: string;
    direction: string;
    intensity: number;
    curve: string;
    prompt_text: string;
    profile_version_id: string | null;
  };
  target_duration_ms: number;
  dialogue: unknown[] | string | null;
  environment: string | null;
  continuity: string | null;
  transition_plan: Record<string, unknown> | null;
  sound_plan: Record<string, unknown> | null;
  creative_intent: string;
  prompt_modifiers: string[];
  suggestion_sources: Record<string, Record<string, unknown>>;
  staging: StagingBoardValue | null;
  staging_3d: Director3DValue | null;
};

export type DirectorIntentEditorProps = {
  shotId: string;
  shotCode: string;
  currentRevision: { id: string; revision_no: number; is_frozen: boolean; fields: Record<string, unknown> } | null;
  targetDurationMs?: number;
  shotType?: string | null;
  shotStatus?: string | null;
  cameraProfiles?: ShotStudioCapabilityOption[];
  stagingParticipants?: Array<{ id: string; label: string }>;
  intentSuggestions?: {
    environment: { value: string; source_label: string; source_revision?: string; stale?: boolean; stale_reason?: string | null } | null;
    continuity: { value: string | null; source_label: string | null; eligible: boolean; reason: string | null; source_revision?: string; stale?: boolean; stale_reason?: string | null } | null;
    script?: { subject_action: string; creative_intent: string; dialogue: unknown; source_label: string; source_revision_id: string | null; source_fingerprint: string; stale: boolean; stale_reason: string | null } | null;
  };
  blockers?: Array<string | { code?: string; message: string; blocking?: boolean }>;
  canEdit?: boolean;
  onSaved?: (revision: ShotDraftWrite) => void | Promise<void>;
  onReloadRequested?: () => void;
  keyboardShortcutsEnabled?: boolean;
};

const DRAFT_STORAGE_PREFIX = "local-drama:director-intent-draft:v1";
const DRAFT_SCHEMA_VERSION = "director-intent.v3";
const DRAFT_MAX_BYTES = 1_000_000;
const DRAFT_DEBOUNCE_MS = 500;

type StoredDirectorDraft = {
  format_version: 1;
  shot_id: string;
  base_revision_no: number;
  schema_version: string;
  saved_at: string;
  fields: Record<string, unknown>;
};

type DraftCandidate = { key: string; stored: StoredDirectorDraft; stale: boolean };

const draftPrefix = (shotId: string) => `${DRAFT_STORAGE_PREFIX}:${shotId}:`;
const draftKey = (shotId: string, revisionNo: number) => `${draftPrefix(shotId)}${revisionNo}:${DRAFT_SCHEMA_VERSION}`;

function parseStoredDraft(raw: string | null): StoredDirectorDraft | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<StoredDirectorDraft>;
    if (value.format_version !== 1 || typeof value.shot_id !== "string" || typeof value.base_revision_no !== "number"
      || typeof value.schema_version !== "string" || typeof value.saved_at !== "string"
      || !value.fields || typeof value.fields !== "object" || Array.isArray(value.fields)) return null;
    return value as StoredDirectorDraft;
  } catch { return null; }
}

function findStoredDraft(shotId: string, revisionNo: number): DraftCandidate | null {
  const prefix = draftPrefix(shotId);
  const currentKey = draftKey(shotId, revisionNo);
  let newest: DraftCandidate | null = null;
  for (let index = 0; index < Math.min(window.localStorage.length, 200); index += 1) {
    const key = window.localStorage.key(index);
    if (!key?.startsWith(prefix)) continue;
    const raw = window.localStorage.getItem(key);
    const stored = parseStoredDraft(raw);
    if (raw && !stored) throw new Error("DRAFT_INVALID_JSON");
    if (!stored || stored.shot_id !== shotId) continue;
    const candidate = { key, stored, stale: key !== currentKey || stored.base_revision_no !== revisionNo || stored.schema_version !== DRAFT_SCHEMA_VERSION };
    if (key === currentKey) return candidate;
    if (!newest || stored.saved_at > newest.stored.saved_at) newest = candidate;
  }
  return newest;
}

function clearShotDrafts(shotId: string): void {
  const prefix = draftPrefix(shotId);
  const keys: string[] = [];
  for (let index = 0; index < window.localStorage.length; index += 1) {
    const key = window.localStorage.key(index);
    if (key?.startsWith(prefix)) keys.push(key);
  }
  keys.forEach((key) => window.localStorage.removeItem(key));
}

const text = (value: unknown): string | null => typeof value === "string" && value.trim() ? value : null;
const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const bounded = (value: unknown, fallback: number): number => typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : fallback;

/** Adapts historical flat v1/v2 fields for editing without rewriting history. Saves always emit v3. */
export function normalizeDirectorIntent(fields: Record<string, unknown>, fallback?: { shotType?: string | null; targetDurationMs?: number }): DirectorIntentV3 {
  const composition = object(fields.composition);
  const performance = object(fields.performance);
  const camera = object(fields.camera_plan);
  const legacyComposition = typeof fields.composition === "string" ? fields.composition : null;
  const shotType = text(fields.shot_type) ?? fallback?.shotType ?? "";
  return {
    schema_version: "director-intent.v3",
    shot_type: shotType,
    composition: {
      preset: text(composition.preset) ?? legacyComposition,
      framing: text(composition.framing), subject_position: text(composition.subject_position), headroom: text(composition.headroom),
      lead_room: text(composition.lead_room), screen_direction: text(composition.screen_direction), axis_rule: text(composition.axis_rule), depth_plan: text(composition.depth_plan),
    },
    subject_action: text(fields.subject_action) ?? text(fields.action) ?? "",
    performance: {
      emotion: text(performance.emotion) ?? text(fields.emotion),
      intensity: typeof performance.intensity === "number" ? bounded(performance.intensity, .5) : typeof fields.emotion_intensity === "number" ? bounded(fields.emotion_intensity, .5) : null,
      body_action: text(performance.body_action) ?? text(fields.body_action), facial_action: text(performance.facial_action) ?? text(fields.facial_action),
      eye_line: text(performance.eye_line) ?? text(fields.eye_line), blocking_summary: text(performance.blocking_summary) ?? text(fields.blocking_summary),
    },
    camera_plan: {
      mode: text(camera.mode) ?? "UNSUPPORTED", shot_type: text(camera.shot_type) ?? shotType, movement: text(camera.movement) ?? "STATIC",
      direction: text(camera.direction) ?? "FORWARD", intensity: bounded(camera.intensity, .5), curve: text(camera.curve) ?? "LINEAR",
      prompt_text: text(camera.prompt_text) ?? "", profile_version_id: text(camera.profile_version_id),
    },
    target_duration_ms: typeof fields.target_duration_ms === "number" ? fields.target_duration_ms : fallback?.targetDurationMs ?? 3_000,
    dialogue: Array.isArray(fields.dialogue) || typeof fields.dialogue === "string" ? fields.dialogue : null,
    environment: text(fields.environment), continuity: text(fields.continuity), transition_plan: Object.keys(object(fields.transition_plan)).length ? object(fields.transition_plan) : null,
    sound_plan: Object.keys(object(fields.sound_plan)).length ? object(fields.sound_plan) : null, creative_intent: text(fields.creative_intent) ?? "",
    prompt_modifiers: Array.isArray(fields.prompt_modifiers) ? fields.prompt_modifiers.filter((item): item is string => typeof item === "string" && Boolean(item.trim())) : [],
    suggestion_sources: Object.fromEntries(Object.entries(object(fields.suggestion_sources)).filter((entry): entry is [string, Record<string, unknown>] => Boolean(entry[1] && typeof entry[1] === "object" && !Array.isArray(entry[1])))),
    staging: fields.staging && typeof fields.staging === "object" ? fields.staging as StagingBoardValue : null,
    staging_3d: fields.staging_3d && typeof fields.staging_3d === "object" ? fields.staging_3d as Director3DValue : null,
  };
}

function ChoiceGrid({ label, value, options, onChange }: { label: string; value: string | null; options: ReadonlyArray<readonly [string, string]>; onChange: (value: string) => void }) {
  return <fieldset className="intent-choice-field"><legend>{label}</legend><div className="intent-choice-grid">{options.map(([code, title]) => <button key={code} type="button" title={title} className={value === code ? "selected" : ""} aria-pressed={value === code} onClick={() => onChange(code)}><strong>{title}</strong></button>)}</div></fieldset>;
}

export function DirectorIntentEditor({ shotId, shotCode, currentRevision, targetDurationMs, shotType, shotStatus, cameraProfiles = [], stagingParticipants = [], intentSuggestions, blockers = [], canEdit = true, onSaved, onReloadRequested, keyboardShortcutsEnabled = true }: DirectorIntentEditorProps) {
  const initial = useMemo(() => normalizeDirectorIntent(currentRevision?.fields ?? {}, { shotType, targetDurationMs }), [currentRevision?.id, shotType, targetDurationMs]);
  const [draft, setDraft] = useState(initial);
  const [baseline, setBaseline] = useState(JSON.stringify(initial));
  const [freeze, setFreeze] = useState(false);
  const [saving, setSaving] = useState(false);
  const [markingReady, setMarkingReady] = useState(false);
  const [resolvingCamera, setResolvingCamera] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ message: string; currentRevisionNo?: number } | null>(null);
  const [draftCandidate, setDraftCandidate] = useState<DraftCandidate | null>(null);
  const [storageError, setStorageError] = useState<string | null>(null);
  const storageKey = currentRevision ? draftKey(shotId, currentRevision.revision_no) : null;

  useEffect(() => {
    setDraft(initial); setBaseline(JSON.stringify(initial)); setMessage(null); setConflict(null); setDraftCandidate(null); setStorageError(null);
    if (!currentRevision) return;
    try { setDraftCandidate(findStoredDraft(shotId, currentRevision.revision_no)); }
    catch (error) { setStorageError(error instanceof Error && error.message === "DRAFT_INVALID_JSON"
      ? "本地草稿 JSON 已损坏，未应用到当前表单；当前服务端 revision 保持不变。"
      : "浏览器本地草稿存储不可用；当前编辑仍可手动保存到服务端。"); }
  }, [currentRevision?.revision_no, initial, shotId]);
  const dirty = JSON.stringify(draft) !== baseline;
  const change = <K extends keyof DirectorIntentV3>(key: K, value: DirectorIntentV3[K]) => { setDraft((old) => ({ ...old, [key]: value })); setMessage(null); };
  const changeComposition = (value: Partial<DirectorIntentV3["composition"]>) => change("composition", { ...draft.composition, ...value });
  const changePerformance = (value: Partial<DirectorIntentV3["performance"]>) => change("performance", { ...draft.performance, ...value });
  const changeCamera = (value: Partial<DirectorIntentV3["camera_plan"]>) => change("camera_plan", { ...draft.camera_plan, ...value });
  const selectedCameraProfile = cameraProfiles.find((profile) => profile.version_id === draft.camera_plan.profile_version_id);
  const cameraContract = asRecord(asRecord(selectedCameraProfile?.capability_contract).camera);
  const supportedValues = (keys: string[], fallback: string[]) => {
    const value = findConfigValue(cameraContract, keys);
    return Array.isArray(value) && value.length ? value.map(String) : fallback;
  };
  const supportedMovements = supportedValues(["movements", "supported_movements"], CAMERA_MOVEMENTS.map(([value]) => value));
  const supportedDirections = supportedValues(["directions", "supported_directions"], [...CAMERA_DIRECTIONS]);
  const supportedCurves = supportedValues(["curves", "supported_curves"], CAMERA_CURVES.map(([value]) => value));
  const movementOptions = CAMERA_MOVEMENTS.filter(([value]) => supportedMovements.includes(value));
  const stagingEyeLine = useMemo(() => draft.staging ? suggestEyeLineFromStaging(draft.staging) : null, [draft.staging]);
  const stagingEyeLineSource = draft.suggestion_sources.staging_eye_line;
  const stagingEyeLineStale = Boolean(stagingEyeLine && stagingEyeLineSource && stagingEyeLineSource.source_fingerprint !== stagingEyeLine.source_fingerprint);
  const adoptScriptSuggestion = () => {
    const suggestion = intentSuggestions?.script;
    if (!suggestion) return;
    setDraft((old) => ({
      ...old,
      subject_action: suggestion.subject_action || old.subject_action,
      creative_intent: suggestion.creative_intent || old.creative_intent,
      dialogue: Array.isArray(suggestion.dialogue) || typeof suggestion.dialogue === "string" ? suggestion.dialogue : old.dialogue,
      suggestion_sources: { ...old.suggestion_sources, script: { source_fingerprint: suggestion.source_fingerprint, source_revision_id: suggestion.source_revision_id, source_label: suggestion.source_label } },
    }));
    setMessage(null);
  };

  const semanticMissing = useMemo(() => {
    const items: string[] = [];
    if (!draft.shot_type) items.push("请选择景别");
    if (!draft.composition.preset) items.push("请选择构图");
    if (!draft.subject_action.trim()) items.push("补充画面或主体动作");
    if (!draft.performance.emotion) items.push("补充表演情绪");
    if (!draft.camera_plan.movement) items.push("请选择运镜");
    if (!draft.camera_plan.profile_version_id) items.push("选择已发布的运镜配置");
    else if (!new Set(["NATIVE", "PROMPT_FALLBACK"]).has(draft.camera_plan.mode)) items.push("当前配置不支持所选运镜，请调整运镜或更换配置");
    if (!Number.isFinite(draft.target_duration_ms) || draft.target_duration_ms <= 0) items.push("时长必须大于 0 秒");
    if (!draft.creative_intent.trim()) items.push("补充创作意图");
    return items;
  }, [draft]);
  const localMissing = useMemo(
    () => dirty ? [...semanticMissing, "镜头意图尚未保存"] : semanticMissing,
    [dirty, semanticMissing],
  );
  const serverMissing = blockers.filter((item) => typeof item === "string" || item.blocking !== false).map((item) => typeof item === "string" ? item : item.message);
  const missing = [...new Set([...localMissing, ...serverMissing])];

  useEffect(() => {
    if (!dirty || !storageKey || !currentRevision || draftCandidate?.key === storageKey) return;
    const timer = window.setTimeout(() => {
      try {
        const serialized = JSON.stringify({
          format_version: 1, shot_id: shotId, base_revision_no: currentRevision.revision_no,
          schema_version: DRAFT_SCHEMA_VERSION, saved_at: new Date().toISOString(), fields: draft,
        } satisfies StoredDirectorDraft);
        if (new Blob([serialized]).size > DRAFT_MAX_BYTES) throw new Error("DRAFT_TOO_LARGE");
        window.localStorage.setItem(storageKey, serialized);
        setStorageError(null);
      } catch (error) {
        setStorageError(error instanceof Error && error.message === "DRAFT_TOO_LARGE"
          ? "本地草稿超过 1 MB，已停止自动缓冲；请尽快手动保存。"
          : "本地草稿写入失败（可能是容量或隐私设置限制）；请尽快手动保存。");
      }
    }, DRAFT_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [currentRevision?.revision_no, dirty, draft, draftCandidate?.key, shotId, storageKey]);

  const discardStoredDraft = () => {
    if (!draftCandidate) return;
    try { window.localStorage.removeItem(draftCandidate.key); setStorageError(null); }
    catch { setStorageError("无法清理本地草稿；可继续编辑，但下次刷新可能仍会提示恢复。"); }
    setDraftCandidate(null);
  };

  const applyStoredDraft = () => {
    if (!draftCandidate) return;
    setDraft(normalizeDirectorIntent(draftCandidate.stored.fields, { shotType, targetDurationMs }));
    if (!draftCandidate.stale) setDraftCandidate(null);
    else {
      try { window.localStorage.removeItem(draftCandidate.key); }
      catch { setStorageError("旧草稿已迁移到当前表单，但无法清理旧缓冲记录。"); }
      setDraftCandidate(null);
      setMessage(`已迁移基于第 ${draftCandidate.stored.base_revision_no} 版的本地草稿；保存前请复核差异。`);
    }
  };

  const discardCurrentDraft = useCallback(() => {
    setDraft(JSON.parse(baseline) as DirectorIntentV3);
    setMessage(null);
    setConflict(null);
    try {
      if (storageKey) window.localStorage.removeItem(storageKey);
      setStorageError(null);
      return true;
    } catch {
      setStorageError("修改已在表单中放弃，但浏览器未能清理本地草稿。");
      return false;
    }
  }, [baseline, storageKey]);

  const save = useCallback(async () => {
    if (!canEdit || !dirty || saving || !currentRevision?.revision_no) return false;
    setSaving(true); setMessage(null); setConflict(null);
    try {
      const { shot_revision: revision } = await putShotDraftV2(shotId, { fields: draft as unknown as Record<string, unknown>, expected_revision_no: currentRevision.revision_no, freeze });
      try { clearShotDrafts(shotId); setStorageError(null); }
      catch { setStorageError("服务端保存成功，但浏览器未能清理本地草稿记录，可在下次提示时丢弃。"); }
      setDraftCandidate(null);
      setBaseline(JSON.stringify(draft));
      setMessage(`已保存为第 ${revision.revision_no} 版${revision.is_frozen ? "（已冻结）" : ""}`);
      await onSaved?.(revision);
      return true;
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 409) {
        const current = error.details?.current_revision_no;
        setConflict({ message: error.message, currentRevisionNo: typeof current === "number" ? current : undefined });
      } else setMessage(`保存失败：${error instanceof Error ? error.message : String(error)}`);
      return false;
    } finally { setSaving(false); }
  }, [canEdit, currentRevision?.revision_no, dirty, draft, freeze, onSaved, saving, shotId]);

  useEffect(() => {
    notifyDraftDirty(dirty, { save, discard: discardCurrentDraft });
    return () => notifyDraftDirty(false);
  }, [dirty, discardCurrentDraft, save, shotId]);

  const resolveCamera = useCallback(async () => {
    if (!draft.camera_plan.profile_version_id || !draft.shot_type || !draft.camera_plan.movement || resolvingCamera) return;
    setResolvingCamera(true); setMessage(null);
    try {
      const result = await resolveProfileCameraPlan(draft.camera_plan.profile_version_id, {
        shot_type: draft.shot_type,
        movement: draft.camera_plan.movement,
        direction: draft.camera_plan.direction,
        intensity: draft.camera_plan.intensity,
        curve: draft.camera_plan.curve,
        prompt_text: draft.camera_plan.prompt_text,
      });
      changeCamera(result.resolution.camera_plan);
      setMessage(`运镜能力已裁决：${result.resolution.camera_plan.mode}`);
    } catch (error) {
      setMessage(`运镜裁决失败：${error instanceof Error ? error.message : String(error)}`);
    } finally { setResolvingCamera(false); }
  }, [draft.camera_plan, draft.shot_type, resolvingCamera]);

  // Auto-resolve the camera capability whenever the structured camera fields
  // change, so the director is not forced to click a manual resolve button.
  // Only kicks in when the mode is still UNSUPPORTED (i.e. a field has been
  // changed and the previous resolution is no longer authoritative).
  useEffect(() => {
    if (draft.camera_plan.mode !== "UNSUPPORTED") return;
    if (!draft.camera_plan.profile_version_id || !draft.shot_type || !draft.camera_plan.movement) return;
    const timer = window.setTimeout(() => { void resolveCamera(); }, DRAFT_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [draft.camera_plan, draft.shot_type, resolveCamera]);

  const saveAndReady = useCallback(async () => {
    if (!canEdit || saving || markingReady || !currentRevision?.revision_no) return;
    setMessage(null); setConflict(null);
    try {
      if (dirty) {
        setSaving(true); setMarkingReady(true);
        const { shot_revision: revision } = await markShotReadyV2(shotId, { draft: draft as unknown as Record<string, unknown>, expected_revision_no: currentRevision.revision_no, freeze });
        try { clearShotDrafts(shotId); setStorageError(null); }
        catch { setStorageError("服务端保存成功，但浏览器未能清理本地草稿记录，可在下次提示时丢弃。"); }
        setDraftCandidate(null);
        setBaseline(JSON.stringify(draft));
        setMessage(`已保存为第 ${revision.revision_no} 版${revision.is_frozen ? "（已冻结）" : ""}，镜头已标记为可进入生产`);
        await onSaved?.(revision);
      } else if (shotStatus === "DIRECTED" && semanticMissing.length === 0) {
        setMarkingReady(true);
        await markShotReadyV2(shotId, { expected_revision_no: currentRevision.revision_no });
        setMessage("镜头已标记为可进入生产");
        await onSaved?.({ id: currentRevision.id, shot_id: shotId, revision_no: currentRevision.revision_no, is_frozen: currentRevision.is_frozen, fields: currentRevision.fields });
      }
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 409) {
        const current = error.details?.current_revision_no;
        setConflict({ message: error.message, currentRevisionNo: typeof current === "number" ? current : undefined });
      } else setMessage(`保存并就绪失败，未写入新版本：${error instanceof Error ? error.message : String(error)}`);
    } finally { setSaving(false); setMarkingReady(false); }
  }, [canEdit, currentRevision, dirty, draft, freeze, markingReady, onSaved, saving, semanticMissing.length, shotId, shotStatus]);

  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    const guardLinks = (event: MouseEvent) => {
      const link = (event.target as Element | null)?.closest("a[href]");
      if (link && !window.confirm("当前镜头意图尚未保存，确定离开并丢弃修改吗？")) { event.preventDefault(); event.stopPropagation(); }
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", guardLinks, true);
    return () => { window.removeEventListener("beforeunload", beforeUnload); document.removeEventListener("click", guardLinks, true); };
  }, [dirty]);
  useEffect(() => {
    if (!keyboardShortcutsEnabled) return;
    const keyboardSave = (event: KeyboardEvent) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); void save(); } };
    window.addEventListener("keydown", keyboardSave);
    return () => window.removeEventListener("keydown", keyboardSave);
  }, [keyboardShortcutsEnabled, save]);

  if (!currentRevision) return <section className="intent-editor intent-empty"><strong>{shotCode} 尚无可编辑版本</strong><span>先创建镜头初始版本，再编辑导演意图。</span></section>;
  return <section className="intent-editor" aria-labelledby="director-intent-title">
    <header className="intent-editor-head"><div><span>单镜导演设计</span><h3 id="director-intent-title">{shotCode} · 单镜编辑</h3></div><div className="intent-revision"><span>第 {currentRevision.revision_no} 版</span>{currentRevision.is_frozen && <span>当前版本已冻结</span>}<strong className={dirty ? "dirty" : "saved"}>{dirty ? "未保存" : "已保存"}</strong></div></header>
    <div className="intent-editor-scroll">
      {draftCandidate && <aside className={`intent-draft-recovery ${draftCandidate.stale ? "stale" : ""}`} role="status">
        <div><strong>{draftCandidate.stale ? "发现过期的本地草稿" : "发现可恢复的本地草稿"}</strong><span>{draftCandidate.stale
          ? `草稿基于第 ${draftCandidate.stored.base_revision_no} 版，当前为第 ${currentRevision.revision_no} 版；不会自动套用。`
          : `保存于 ${new Date(draftCandidate.stored.saved_at).toLocaleString()}，尚未写入服务端。`}</span></div>
        <div><button type="button" onClick={applyStoredDraft}>{draftCandidate.stale ? "显式迁移并复核" : "恢复草稿"}</button><button type="button" className="intent-discard" onClick={discardStoredDraft}>丢弃本地草稿</button></div>
      </aside>}
      {storageError && <p className="intent-message error" role="alert">{storageError}</p>}
      <ChoiceGrid label="景别" value={draft.shot_type} options={SHOT_TYPES} onChange={(value) => { change("shot_type", value); changeCamera({ shot_type: value, mode: "UNSUPPORTED" }); }} />
      <ChoiceGrid label="构图" value={draft.composition.preset} options={COMPOSITIONS} onChange={(preset) => changeComposition({ preset })} />
      <div className="intent-section"><h4>画面与动作</h4>
        {intentSuggestions?.script && <aside className={`intent-inheritance-suggestion ${intentSuggestions.script.stale ? "is-stale" : ""}`} role={intentSuggestions.script.stale ? "alert" : undefined}><div><strong>{intentSuggestions.script.stale ? "剧本来源已更新" : "剧本拆解建议"}</strong><span>来源：{intentSuggestions.script.source_label}</span><p>{[intentSuggestions.script.subject_action, intentSuggestions.script.creative_intent].filter(Boolean).join("；")}</p>{intentSuggestions.script.stale_reason && <p>{intentSuggestions.script.stale_reason}</p>}</div><button type="button" disabled={!canEdit || currentRevision.is_frozen || (!intentSuggestions.script.stale && draft.suggestion_sources.script?.source_fingerprint === intentSuggestions.script.source_fingerprint)} onClick={adoptScriptSuggestion}>{intentSuggestions.script.stale ? "重新采用并复核" : draft.suggestion_sources.script?.source_fingerprint === intentSuggestions.script.source_fingerprint ? "已采用" : "采用到本镜"}</button></aside>}
        <label>主体动作<textarea value={draft.subject_action} onChange={(event) => change("subject_action", event.target.value)} placeholder="谁在做什么？动作从哪里开始、在哪里结束？" /></label><label>画面创作意图<textarea value={draft.creative_intent} onChange={(event) => change("creative_intent", event.target.value)} placeholder="这一镜要让观众感受到什么？" /></label></div>
      <div className="intent-section"><h4>表演与情绪</h4>
        <EmotionPicker value={draft.performance.emotion} intensity={draft.performance.intensity ?? .5} disabled={!canEdit || currentRevision.is_frozen} onChange={changePerformance} />
        <label>身体动作<textarea value={draft.performance.body_action ?? ""} disabled={!canEdit || currentRevision.is_frozen} onChange={(event) => changePerformance({ body_action: event.target.value || null })} placeholder="保留创作自由：描述姿态、动作节奏与停顿" /></label>
        <MicroExpressionSelect value={draft.performance.facial_action} disabled={!canEdit || currentRevision.is_frozen} onChange={(facial_action) => changePerformance({ facial_action })} />
        <EyeLineControl value={draft.performance.eye_line} disabled={!canEdit || currentRevision.is_frozen} onChange={(eye_line) => changePerformance({ eye_line })} />
        {stagingEyeLine && <aside className={`intent-inheritance-suggestion ${stagingEyeLineStale ? "is-stale" : ""}`} role={stagingEyeLineStale ? "alert" : undefined}><div><strong>{stagingEyeLineStale ? "站位已变化，视线建议需复核" : "根据二维站位建议视线"}</strong><span>{stagingEyeLine.reason}</span></div><button type="button" disabled={!canEdit || currentRevision.is_frozen || (!stagingEyeLineStale && draft.performance.eye_line === stagingEyeLine.value && stagingEyeLineSource?.source_fingerprint === stagingEyeLine.source_fingerprint)} onClick={() => setDraft((old) => ({ ...old, performance: { ...old.performance, eye_line: stagingEyeLine.value }, suggestion_sources: { ...old.suggestion_sources, staging_eye_line: { source_fingerprint: stagingEyeLine.source_fingerprint, source_label: `${stagingEyeLine.subject_label}→${stagingEyeLine.target_label}` } } }))}>{stagingEyeLineStale ? "按新站位更新" : "采用视线建议"}</button></aside>}
      </div>
      <details className="intent-section"><summary>二维站位预演</summary><StagingBoard value={draft.staging ?? undefined} participantOptions={stagingParticipants} disabled={!canEdit || currentRevision.is_frozen} onChange={(staging, output) => setDraft((old) => ({ ...old, staging, performance: { ...old.performance, blocking_summary: output.blocking_summary }, camera_plan: { ...old.camera_plan, ...output.camera_plan, mode: "PROMPT_FALLBACK" } }))} /></details>
      <details className="intent-section"><summary>可选的三维导演预演</summary><LazyDirector3DSpike value={draft.staging_3d ?? undefined} participantOptions={stagingParticipants} disabled={!canEdit || currentRevision.is_frozen} onChange={(staging_3d, output) => setDraft((old) => ({ ...old, staging_3d, composition: { ...old.composition, ...output.director_intent_patch.composition }, performance: { ...old.performance, ...output.director_intent_patch.performance }, camera_plan: { ...old.camera_plan, ...output.camera_plan, mode: "PROMPT_FALLBACK" } }))} /></details>
      <div className="intent-section">
        <h4>运镜</h4>
        <ChoiceGrid label="运动方式" value={draft.camera_plan.movement} options={movementOptions.length ? movementOptions : CAMERA_MOVEMENTS} onChange={(movement) => changeCamera({ movement, mode: "UNSUPPORTED" })} />
        <div className="intent-fields-2">
          <label>方向<select value={draft.camera_plan.direction} onChange={(event) => changeCamera({ direction: event.target.value, mode: "UNSUPPORTED" })}>{supportedDirections.map((value) => <option key={value} value={value}>{CAMERA_DIRECTION_LABELS[value as keyof typeof CAMERA_DIRECTION_LABELS] ?? value}</option>)}</select></label>
          <label>强度 <output>{Math.round(draft.camera_plan.intensity * 100)}%</output><input aria-label="运镜强度" type="range" min="0" max="1" step="0.05" value={draft.camera_plan.intensity} onChange={(event) => changeCamera({ intensity: Number(event.target.value), mode: "UNSUPPORTED" })} /></label>
        </div>
        <label>已发布运镜配置<select aria-label="已发布运镜配置" value={draft.camera_plan.profile_version_id ?? ""} onChange={(event) => changeCamera({ profile_version_id: event.target.value || null, mode: "UNSUPPORTED" })}><option value="">请选择</option>{cameraProfiles.map((profile) => <option key={profile.version_id} value={profile.version_id}>{profile.title} · 第 {profile.version_no ?? "?"} 版</option>)}</select></label><ProfileExecutionDetailButton profileVersionId={draft.camera_plan.profile_version_id} />
        <button type="button" className="intent-resolve-camera" disabled={resolvingCamera || !draft.camera_plan.profile_version_id || !draft.shot_type || !draft.camera_plan.movement} onClick={() => void resolveCamera()}>{resolvingCamera ? "裁决中…" : "立即重新裁决运镜"}</button>
        <small className="intent-resolve-hint">运镜字段变更后，系统会按已发布配置自动判断能否执行；页面只展示当前配置支持的运动方式、方向和节奏。</small>
        <details>
          <summary>高级：缓动与兼容方式</summary>
          <div className="intent-fields-2">
            <label>运动节奏<select value={draft.camera_plan.curve} onChange={(event) => changeCamera({ curve: event.target.value, mode: "UNSUPPORTED" })}>{CAMERA_CURVES.filter(([value]) => supportedCurves.includes(value)).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <div className="intent-camera-resolution"><span>系统采用方式</span><strong>{draft.camera_plan.mode === "NATIVE" ? "模型原生运镜" : draft.camera_plan.mode === "PROMPT_FALLBACK" ? "提示词兼容运镜" : "等待系统裁决"}</strong></div>
          </div>
          {draft.camera_plan.mode === "PROMPT_FALLBACK" && <label>兼容运镜补充描述<textarea value={draft.camera_plan.prompt_text} onChange={(event) => changeCamera({ prompt_text: event.target.value, mode: "UNSUPPORTED" })} placeholder="只补充预设无法表达的镜头运动；通常无需填写" /><small>系统会先按所选运镜生成描述，仅在确有特殊路径时补充。</small></label>}
        </details>
      </div>
      <div className="intent-section"><h4>时长与环境</h4><div className="intent-duration"><input aria-label="目标时长（秒）" type="number" min="0.1" max="600" step="0.1" value={draft.target_duration_ms / 1000} onChange={(event) => change("target_duration_ms", Math.round(Number(event.target.value) * 1000))} /><span>秒</span></div>
        {intentSuggestions?.environment && <aside className={`intent-inheritance-suggestion ${intentSuggestions.environment.stale ? "is-stale" : ""}`} role={intentSuggestions.environment.stale ? "alert" : undefined}><div><strong>{intentSuggestions.environment.stale ? "场景资料已更新" : "场景环境建议"}</strong><span>来源：{intentSuggestions.environment.source_label}</span><p>{intentSuggestions.environment.value}</p>{intentSuggestions.environment.stale_reason && <p>{intentSuggestions.environment.stale_reason}</p>}</div><button type="button" disabled={!canEdit || currentRevision.is_frozen || (!intentSuggestions.environment.stale && draft.environment === intentSuggestions.environment.value)} onClick={() => setDraft((old) => ({ ...old, environment: intentSuggestions.environment!.value, suggestion_sources: { ...old.suggestion_sources, environment: { source_revision: intentSuggestions.environment!.source_revision, source_label: intentSuggestions.environment!.source_label } } }))}>{intentSuggestions.environment.stale ? "重新采用并复核" : draft.environment === intentSuggestions.environment.value ? "已采用" : "采用建议"}</button></aside>}
        <label>环境差异与补充<textarea value={draft.environment ?? ""} onChange={(event) => change("environment", event.target.value || null)} placeholder="采用场景建议后，可补充本镜特有的天气、光线或空间变化" /></label>
        {intentSuggestions?.continuity && <aside className={`intent-inheritance-suggestion ${intentSuggestions.continuity.eligible ? "" : "unavailable"} ${intentSuggestions.continuity.stale ? "is-stale" : ""}`} role={intentSuggestions.continuity.stale ? "alert" : undefined}><div><strong>{intentSuggestions.continuity.stale ? "上一镜已更新" : "前后镜连续性建议"}</strong><span>{intentSuggestions.continuity.source_label ? `来源：${intentSuggestions.continuity.source_label}` : "基于镜头顺序检查"}</span>{intentSuggestions.continuity.value ? <p>{intentSuggestions.continuity.value}</p> : <p>{intentSuggestions.continuity.reason}</p>}{intentSuggestions.continuity.stale_reason && <p>{intentSuggestions.continuity.stale_reason}</p>}</div>{intentSuggestions.continuity.value && <button type="button" disabled={!canEdit || currentRevision.is_frozen || (!intentSuggestions.continuity.stale && draft.continuity === intentSuggestions.continuity.value)} onClick={() => setDraft((old) => ({ ...old, continuity: intentSuggestions.continuity!.value, suggestion_sources: { ...old.suggestion_sources, continuity: { source_revision: intentSuggestions.continuity!.source_revision, source_label: intentSuggestions.continuity!.source_label } } }))}>{intentSuggestions.continuity.stale ? "重新继承并复核" : draft.continuity === intentSuggestions.continuity.value ? "已采用" : "继承并复核"}</button>}</aside>}
        <label>连续性变化<textarea value={draft.continuity ?? ""} onChange={(event) => change("continuity", event.target.value || null)} placeholder="记录服装、伤势、持物、位置或动作承接的变化" /></label>
      </div>
      <aside className={`intent-ready ${missing.length ? "blocked" : "ready"}`}><div><strong>进入生产前检查</strong><span>{missing.length ? `还有 ${missing.length} 项需要处理` : "导演设计已满足当前生产条件"}</span></div>{missing.length > 0 && <ul>{missing.map((item) => <li key={item}>{item}</li>)}</ul>}</aside>
      {conflict && <div className="intent-conflict" role="alert"><strong>保存冲突</strong><span>{conflict.message}{conflict.currentRevisionNo ? `（服务器已更新到第 ${conflict.currentRevisionNo} 版）` : ""}</span><button type="button" onClick={onReloadRequested}>刷新最新版本</button></div>}
      {message && <p className={message.includes("失败") ? "intent-message error" : "intent-message"} role={message.includes("失败") ? "alert" : "status"}>{message}</p>}
    </div>
    <footer className="intent-editor-actions"><label><input type="checkbox" checked={freeze} disabled={!canEdit} onChange={(event) => setFreeze(event.target.checked)} />保存后冻结</label><button type="button" className="intent-discard" disabled={!dirty || saving} onClick={() => void discardCurrentDraft()}>放弃修改</button><button type="button" className="intent-save" disabled={!canEdit || !dirty || saving} onClick={() => void save()}>{saving ? "保存中…" : `仅保存为第 ${currentRevision.revision_no + 1} 版`}<kbd>Ctrl S</kbd></button><button type="button" className="intent-save-and-ready" title={semanticMissing.length ? semanticMissing.join("；") : undefined} disabled={!canEdit || saving || markingReady || shotStatus !== "DIRECTED" || semanticMissing.length > 0} onClick={() => void saveAndReady()}>{markingReady ? "处理中…" : shotStatus === "READY" ? "已可进入生产" : "保存并就绪"}</button></footer>
  </section>;
}
