import { useCallback, useEffect, useMemo, useState } from "react";
import { DirectorIntentApiError, saveDirectorIntentRevision, type ShotRevisionWrite } from "./directorIntentClient";
import { StagingBoard, type StagingBoardValue } from "./StagingBoard";
import { LazyDirector3DSpike } from "../director-3d/LazyDirector3DSpike";
import type { Director3DValue } from "../director-3d/types";
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
  staging: StagingBoardValue | null;
  staging_3d: Director3DValue | null;
};

export type DirectorIntentEditorProps = {
  shotId: string;
  shotCode: string;
  currentRevision: { id: string; revision_no: number; is_frozen: boolean; fields: Record<string, unknown> } | null;
  targetDurationMs?: number;
  shotType?: string | null;
  blockers?: Array<string | { code?: string; message: string; blocking?: boolean }>;
  canEdit?: boolean;
  onSaved?: (revision: ShotRevisionWrite) => void | Promise<void>;
  onReloadRequested?: () => void;
  keyboardShortcutsEnabled?: boolean;
};

const SHOT_TYPES = [
  ["ESTABLISHING", "大全景"], ["WIDE", "全景"], ["MEDIUM", "中景"], ["MEDIUM_CLOSE", "中近景"],
  ["CLOSEUP", "近景"], ["EXTREME_CLOSEUP", "特写"], ["POV", "POV"], ["INSERT", "插入"],
] as const;
const COMPOSITIONS = [
  ["CENTER", "居中"], ["LEFT_THIRD", "左三分"], ["RIGHT_THIRD", "右三分"], ["SYMMETRY", "对称"],
  ["OVER_SHOULDER", "过肩"], ["TWO_SHOT", "双人"], ["LOW_ANGLE", "低机位"], ["HIGH_ANGLE", "高机位"],
] as const;
const MOVEMENTS = [
  ["STATIC", "固定"], ["PUSH_IN", "推进"], ["PULL_OUT", "拉远"], ["PAN", "摇摄"], ["TILT", "俯仰"],
  ["TRUCK", "横移"], ["PEDESTAL", "升降"], ["ZOOM", "变焦"], ["ORBIT", "环绕"], ["ROLL", "滚转"],
] as const;

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
    staging: fields.staging && typeof fields.staging === "object" ? fields.staging as StagingBoardValue : null,
    staging_3d: fields.staging_3d && typeof fields.staging_3d === "object" ? fields.staging_3d as Director3DValue : null,
  };
}

function ChoiceGrid({ label, value, options, onChange }: { label: string; value: string | null; options: ReadonlyArray<readonly [string, string]>; onChange: (value: string) => void }) {
  return <fieldset className="intent-choice-field"><legend>{label}</legend><div className="intent-choice-grid">{options.map(([code, title]) => <button key={code} type="button" className={value === code ? "selected" : ""} aria-pressed={value === code} onClick={() => onChange(code)}><strong>{title}</strong><small>{code}</small></button>)}</div></fieldset>;
}

export function DirectorIntentEditor({ shotId, shotCode, currentRevision, targetDurationMs, shotType, blockers = [], canEdit = true, onSaved, onReloadRequested, keyboardShortcutsEnabled = true }: DirectorIntentEditorProps) {
  const initial = useMemo(() => normalizeDirectorIntent(currentRevision?.fields ?? {}, { shotType, targetDurationMs }), [currentRevision?.id, shotType, targetDurationMs]);
  const [draft, setDraft] = useState(initial);
  const [baseline, setBaseline] = useState(JSON.stringify(initial));
  const [freeze, setFreeze] = useState(false);
  const [saving, setSaving] = useState(false);
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

  const localMissing = useMemo(() => {
    const items: string[] = [];
    if (!draft.shot_type) items.push("请选择景别");
    if (!draft.composition.preset) items.push("请选择构图");
    if (!draft.subject_action.trim()) items.push("补充画面或主体动作");
    if (!draft.performance.emotion) items.push("补充表演情绪");
    if (!draft.camera_plan.movement) items.push("请选择运镜");
    if (!Number.isFinite(draft.target_duration_ms) || draft.target_duration_ms <= 0) items.push("时长必须大于 0 秒");
    if (!draft.creative_intent.trim()) items.push("补充创作意图");
    if (dirty) items.push("镜头意图尚未保存");
    return items;
  }, [draft, dirty]);
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
      setMessage(`已显式迁移 revision ${draftCandidate.stored.base_revision_no} 的本地草稿；保存前请复核差异。`);
    }
  };

  const save = useCallback(async () => {
    if (!canEdit || !dirty || saving || !currentRevision?.revision_no) return;
    setSaving(true); setMessage(null); setConflict(null);
    try {
      const revision = await saveDirectorIntentRevision({ shotId, fields: draft as unknown as Record<string, unknown>, expectedRevisionNo: currentRevision.revision_no, freeze });
      try { clearShotDrafts(shotId); setStorageError(null); }
      catch { setStorageError("服务端保存成功，但浏览器未能清理本地草稿记录，可在下次提示时丢弃。"); }
      setDraftCandidate(null);
      setBaseline(JSON.stringify(draft));
      setMessage(`已保存 revision ${revision.revision_no}${revision.is_frozen ? "（已冻结）" : ""}`);
      await onSaved?.(revision);
    } catch (error) {
      if (error instanceof DirectorIntentApiError && error.status === 409) {
        const current = error.details?.current_revision_no;
        setConflict({ message: error.message, currentRevisionNo: typeof current === "number" ? current : undefined });
      } else setMessage(`保存失败：${error instanceof Error ? error.message : String(error)}`);
    } finally { setSaving(false); }
  }, [canEdit, currentRevision?.revision_no, dirty, draft, freeze, onSaved, saving, shotId]);

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

  if (!currentRevision) return <section className="intent-editor intent-empty"><strong>{shotCode} 尚无可编辑 revision</strong><span>先创建镜头初始修订，再编辑导演意图。</span></section>;
  return <section className="intent-editor" aria-labelledby="director-intent-title">
    <header className="intent-editor-head"><div><span>DIRECTOR INTENT V3</span><h3 id="director-intent-title">{shotCode} · 单镜编辑</h3></div><div className="intent-revision"><span>revision {currentRevision.revision_no}</span>{currentRevision.is_frozen && <span>当前版本已冻结</span>}<strong className={dirty ? "dirty" : "saved"}>{dirty ? "未保存" : "已保存"}</strong></div></header>
    <div className="intent-editor-scroll">
      {draftCandidate && <aside className={`intent-draft-recovery ${draftCandidate.stale ? "stale" : ""}`} role="status">
        <div><strong>{draftCandidate.stale ? "发现过期的本地草稿" : "发现可恢复的本地草稿"}</strong><span>{draftCandidate.stale
          ? `草稿基于 revision ${draftCandidate.stored.base_revision_no}，当前为 revision ${currentRevision.revision_no}；不会自动套用。`
          : `保存于 ${new Date(draftCandidate.stored.saved_at).toLocaleString()}，尚未写入服务端。`}</span></div>
        <div><button type="button" onClick={applyStoredDraft}>{draftCandidate.stale ? "显式迁移并复核" : "恢复草稿"}</button><button type="button" className="intent-discard" onClick={discardStoredDraft}>丢弃本地草稿</button></div>
      </aside>}
      {storageError && <p className="intent-message error" role="alert">{storageError}</p>}
      <ChoiceGrid label="景别" value={draft.shot_type} options={SHOT_TYPES} onChange={(value) => { change("shot_type", value); changeCamera({ shot_type: value, mode: "UNSUPPORTED" }); }} />
      <ChoiceGrid label="构图" value={draft.composition.preset} options={COMPOSITIONS} onChange={(preset) => changeComposition({ preset })} />
      <div className="intent-section"><h4>画面与动作</h4><label>主体动作<textarea value={draft.subject_action} onChange={(event) => change("subject_action", event.target.value)} placeholder="谁在做什么？动作从哪里开始、在哪里结束？" /></label><label>画面创作意图<textarea value={draft.creative_intent} onChange={(event) => change("creative_intent", event.target.value)} placeholder="这一镜要让观众感受到什么？" /></label></div>
      <div className="intent-section"><h4>表演与情绪</h4><div className="intent-fields-2"><label>情绪<input value={draft.performance.emotion ?? ""} onChange={(event) => changePerformance({ emotion: event.target.value || null })} placeholder="克制、慌张、决绝…" /></label><label>强度 <output>{Math.round((draft.performance.intensity ?? .5) * 100)}%</output><input type="range" min="0" max="1" step="0.05" value={draft.performance.intensity ?? .5} onChange={(event) => changePerformance({ intensity: Number(event.target.value) })} /></label></div><label>身体动作<textarea value={draft.performance.body_action ?? ""} onChange={(event) => changePerformance({ body_action: event.target.value || null })} /></label><div className="intent-fields-2"><label>面部动作<input value={draft.performance.facial_action ?? ""} onChange={(event) => changePerformance({ facial_action: event.target.value || null })} /></label><label>视线<input value={draft.performance.eye_line ?? ""} onChange={(event) => changePerformance({ eye_line: event.target.value || null })} /></label></div></div>
      <details className="intent-section"><summary>2D 站位预演</summary><StagingBoard value={draft.staging ?? undefined} disabled={!canEdit || currentRevision.is_frozen} onChange={(staging, output) => setDraft((old) => ({ ...old, staging, performance: { ...old.performance, blocking_summary: output.blocking_summary }, camera_plan: { ...old.camera_plan, ...output.camera_plan, mode: "PROMPT_FALLBACK" } }))} /></details>
      <details className="intent-section"><summary>可选 3D 导演预演</summary><LazyDirector3DSpike value={draft.staging_3d ?? undefined} disabled={!canEdit || currentRevision.is_frozen} onChange={(staging_3d, output) => setDraft((old) => ({ ...old, staging_3d, composition: { ...old.composition, ...output.director_intent_patch.composition }, performance: { ...old.performance, ...output.director_intent_patch.performance }, camera_plan: { ...old.camera_plan, ...output.camera_plan, mode: "PROMPT_FALLBACK" } }))} /></details>
      <div className="intent-section"><h4>运镜</h4><ChoiceGrid label="运动方式" value={draft.camera_plan.movement} options={MOVEMENTS} onChange={(movement) => changeCamera({ movement, mode: "UNSUPPORTED" })} /><div className="intent-fields-2"><label>方向<select value={draft.camera_plan.direction} onChange={(event) => changeCamera({ direction: event.target.value, mode: "UNSUPPORTED" })}>{["FORWARD", "BACKWARD", "LEFT", "RIGHT", "UP", "DOWN", "CLOCKWISE", "COUNTERCLOCKWISE"].map((value) => <option key={value}>{value}</option>)}</select></label><label>强度 <output>{Math.round(draft.camera_plan.intensity * 100)}%</output><input type="range" min="0" max="1" step="0.05" value={draft.camera_plan.intensity} onChange={(event) => changeCamera({ intensity: Number(event.target.value), mode: "UNSUPPORTED" })} /></label></div><details><summary>高级参数 · 原始枚举</summary><div className="intent-fields-2"><label>Curve<select value={draft.camera_plan.curve} onChange={(event) => changeCamera({ curve: event.target.value, mode: "UNSUPPORTED" })}>{["LINEAR", "EASE_IN", "EASE_OUT", "EASE_IN_OUT"].map((value) => <option key={value}>{value}</option>)}</select></label><label>Mode<input readOnly value={draft.camera_plan.mode} /></label></div><label>Prompt fallback<textarea value={draft.camera_plan.prompt_text} onChange={(event) => changeCamera({ prompt_text: event.target.value, mode: "UNSUPPORTED" })} /></label></details></div>
      <div className="intent-section"><h4>时长与环境</h4><div className="intent-duration"><input aria-label="目标时长（秒）" type="number" min="0.1" max="600" step="0.1" value={draft.target_duration_ms / 1000} onChange={(event) => change("target_duration_ms", Math.round(Number(event.target.value) * 1000))} /><span>秒</span></div><label>环境<textarea value={draft.environment ?? ""} onChange={(event) => change("environment", event.target.value || null)} /></label><label>连续性<textarea value={draft.continuity ?? ""} onChange={(event) => change("continuity", event.target.value || null)} /></label></div>
      <aside className={`intent-ready ${missing.length ? "blocked" : "ready"}`}><div><strong>Production Ready</strong><span>{missing.length ? `还有 ${missing.length} 项需要处理` : "导演意图已满足当前生产门槛"}</span></div>{missing.length > 0 && <ul>{missing.map((item) => <li key={item}>{item}</li>)}</ul>}</aside>
      {conflict && <div className="intent-conflict" role="alert"><strong>保存冲突</strong><span>{conflict.message}{conflict.currentRevisionNo ? `（服务端已到 revision ${conflict.currentRevisionNo}）` : ""}</span><button type="button" onClick={onReloadRequested}>刷新最新版本</button></div>}
      {message && <p className={message.startsWith("保存失败") ? "intent-message error" : "intent-message"} role="status">{message}</p>}
    </div>
    <footer className="intent-editor-actions"><label><input type="checkbox" checked={freeze} disabled={!canEdit} onChange={(event) => setFreeze(event.target.checked)} />保存后冻结</label><button type="button" className="intent-discard" disabled={!dirty || saving} onClick={() => { setDraft(JSON.parse(baseline) as DirectorIntentV3); setMessage(null); try { if (storageKey) window.localStorage.removeItem(storageKey); } catch { setStorageError("修改已在表单中放弃，但浏览器未能清理本地草稿。"); } }}>放弃修改</button><button type="button" className="intent-save" disabled={!canEdit || !dirty || saving} onClick={() => void save()}>{saving ? "保存中…" : `保存 revision ${currentRevision.revision_no + 1}`}<kbd>Ctrl S</kbd></button></footer>
  </section>;
}
