import { useEffect, useState, type ReactNode } from "react";
import { listStoryAssets, type StoryAsset } from "../../generated/api";
import { ProjectMediaVersionSelect } from "../media-picker/ProjectMediaVersionSelect";

type EditorProps = { value: string; onChange: (value: string) => void; projectId?: string };

type TimedDirection = { time_us: number; direction: string; strength: number };
type PerformanceBinding = { actor_id: string; action: string; start_us: number; end_us: number; binding_type: string; source_role: string };
type ReferenceBinding = { role: string; media_version_id: string; ordinal: number; weight: number };
type MotionMask = { media_version_id: string; subject_role: string; invert: boolean };

const DIRECTIONS = ["PAN_LEFT", "PAN_RIGHT", "TILT_UP", "TILT_DOWN", "DOLLY_IN", "DOLLY_OUT", "ZOOM_IN", "ZOOM_OUT", "ORBIT_LEFT", "ORBIT_RIGHT", "STATIC"] as const;
const BINDING_TYPES = ["CHARACTER_DRIVING", "POSE", "ACTION", "LIP_SYNC", "FACE_DRIVING", "DRIVING_VIDEO", "AUDIO_GUIDE"] as const;
const PERFORMANCE_ACTIONS = ["WALK", "RUN", "TURN_HEAD", "TALK", "LIP_SYNC", "HOLD_POSE", "FACIAL_EXPRESSION"] as const;
const REFERENCE_ROLES = ["DRIVING_VIDEO", "CHARACTER_REFERENCE", "STYLE_REFERENCE", "POSE_REFERENCE", "AUDIO_GUIDE", "FIRST_FRAME", "LAST_FRAME"] as const;
const SUBJECT_ROLES = ["subject", "face", "body", "foreground", "background"] as const;
const DIRECTION_LABELS: Record<string, string> = { PAN_LEFT: "左摇", PAN_RIGHT: "右摇", TILT_UP: "上摇", TILT_DOWN: "下摇", DOLLY_IN: "推进", DOLLY_OUT: "拉远", ZOOM_IN: "变焦放大", ZOOM_OUT: "变焦缩小", ORBIT_LEFT: "向左环绕", ORBIT_RIGHT: "向右环绕", STATIC: "固定" };
const BINDING_LABELS: Record<string, string> = { CHARACTER_DRIVING: "角色驱动", POSE: "姿态", ACTION: "动作", LIP_SYNC: "口型同步", FACE_DRIVING: "面部驱动", DRIVING_VIDEO: "驱动视频", AUDIO_GUIDE: "音频引导" };
const ACTION_LABELS: Record<string, string> = { WALK: "行走", RUN: "奔跑", TURN_HEAD: "转头", TALK: "说话", LIP_SYNC: "口型同步", HOLD_POSE: "保持姿态", FACIAL_EXPRESSION: "面部表情" };
const ROLE_LABELS: Record<string, string> = { DRIVING_VIDEO: "驱动视频", CHARACTER_REFERENCE: "角色参考", STYLE_REFERENCE: "风格参考", POSE_REFERENCE: "姿态参考", AUDIO_GUIDE: "音频引导", FIRST_FRAME: "首帧", LAST_FRAME: "尾帧" };
const SUBJECT_LABELS: Record<string, string> = { subject: "主体", face: "面部", body: "身体", foreground: "前景", background: "背景" };
const secondsOf = (microseconds: number) => Math.round((microseconds / 1_000_000) * 10) / 10;
const microsecondsOf = (seconds: string) => Math.round(Math.max(0, Number(seconds) || 0) * 1_000_000);
const TIME_STEP_SECONDS = 0.1;

export function TimeRangeSlider({ startUs, endUs, disabled = false, onChange }: {
  startUs: number;
  endUs: number;
  disabled?: boolean;
  onChange: (range: { start_us: number; end_us: number }) => void;
}) {
  const startSeconds = Math.max(0, secondsOf(startUs));
  const endSeconds = Math.max(startSeconds + TIME_STEP_SECONDS, secondsOf(endUs));
  const maxSeconds = Math.max(10, Math.ceil(endSeconds));
  const commitStart = (next: number) => onChange({
    start_us: Math.round(Math.max(0, Math.min(next, endSeconds - TIME_STEP_SECONDS)) * 1_000_000),
    end_us: Math.round(endSeconds * 1_000_000),
  });
  const commitEnd = (next: number) => onChange({
    start_us: Math.round(startSeconds * 1_000_000),
    end_us: Math.round(Math.max(startSeconds + TIME_STEP_SECONDS, next) * 1_000_000),
  });
  return <fieldset className="time-range-slider">
    <legend>表演时间范围</legend>
    <div className="time-range-slider__summary"><output>{startSeconds.toFixed(1)} 秒</output><span aria-hidden="true">—</span><output>{endSeconds.toFixed(1)} 秒</output></div>
    <div className="time-range-slider__tracks">
      <label><span>开始位置</span><input aria-label="表演开始位置" type="range" min="0" max={maxSeconds} step={TIME_STEP_SECONDS} value={startSeconds} disabled={disabled} onChange={(event) => commitStart(Number(event.target.value))} /></label>
      <label><span>结束位置</span><input aria-label="表演结束位置" type="range" min="0" max={maxSeconds} step={TIME_STEP_SECONDS} value={endSeconds} disabled={disabled} onChange={(event) => commitEnd(Number(event.target.value))} /></label>
    </div>
    <div className="time-range-slider__precise">
      <label>精确开始（秒）<input type="number" min="0" max={Math.max(0, endSeconds - TIME_STEP_SECONDS)} step={TIME_STEP_SECONDS} value={startSeconds} disabled={disabled} onChange={(event) => commitStart(Number(event.target.value))} /></label>
      <label>精确结束（秒）<input type="number" min={startSeconds + TIME_STEP_SECONDS} step={TIME_STEP_SECONDS} value={endSeconds} disabled={disabled} onChange={(event) => commitEnd(Number(event.target.value))} /></label>
    </div>
  </fieldset>;
}

function parseItems<T>(value: string): { items: T[]; error: boolean } {
  try {
    const parsed: unknown = JSON.parse(value || "[]");
    return Array.isArray(parsed) ? { items: parsed as T[], error: false } : { items: [], error: true };
  } catch {
    return { items: [], error: true };
  }
}

function writeItems<T>(items: T[], onChange: (value: string) => void) {
  onChange(JSON.stringify(items));
}

function EditorShell({ title, hint, error, onReset, onAdd, addLabel, children }: { title: string; hint: string; error: boolean; onReset: () => void; onAdd: () => void; addLabel: string; children: ReactNode }) {
  return <fieldset className="structured-control-editor">
    <legend>{title}</legend>
    <small>{hint}</small>
    {error && <p className="inline-error" role="alert">已有值不是有效对象数组。请重置后使用结构化编辑器。</p>}
    {error ? <button type="button" className="secondary" onClick={onReset}>重置为空列表</button> : children}
    {!error && <button type="button" className="secondary" onClick={onAdd}>{addLabel}</button>}
  </fieldset>;
}

export function TimedDirectionEditor({ value, onChange }: EditorProps) {
  const parsed = parseItems<TimedDirection>(value);
  const update = (index: number, patch: Partial<TimedDirection>) => writeItems(parsed.items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item), onChange);
  const remove = (index: number) => writeItems(parsed.items.filter((_, itemIndex) => itemIndex !== index), onChange);
  return <EditorShell title="分时运镜" hint="按秒设置运镜时间点；系统在提交时自动转换为精确内部时间。" error={parsed.error} onReset={() => onChange("[]")} onAdd={() => writeItems([...parsed.items, { time_us: 0, direction: "PAN_LEFT", strength: 0.5 }], onChange)} addLabel="添加运镜节点">
    {parsed.items.map((item, index) => <div className="structured-control-row" key={index}>
      <label>时间（秒）<input type="number" min="0" step="0.1" value={secondsOf(item.time_us)} onChange={(event) => update(index, { time_us: microsecondsOf(event.target.value) })} /></label>
      <label>方向<select value={item.direction} onChange={(event) => update(index, { direction: event.target.value })}>{DIRECTIONS.map((direction) => <option key={direction} value={direction}>{DIRECTION_LABELS[direction]}</option>)}</select></label>
      <label>强度<input type="number" min="0" max="1" step="0.05" value={item.strength} onChange={(event) => update(index, { strength: Number(event.target.value) })} /></label>
      <button type="button" className="secondary" aria-label={`删除运镜节点 ${index + 1}`} onClick={() => remove(index)}>删除</button>
    </div>)}
  </EditorShell>;
}

export function PerformanceBindingEditor({ value, onChange, projectId }: EditorProps) {
  const parsed = parseItems<PerformanceBinding>(value);
  const [characters, setCharacters] = useState<StoryAsset[]>([]);
  const [charactersLoading, setCharactersLoading] = useState(false);
  useEffect(() => {
    if (!projectId || parsed.items.length === 0) { setCharacters([]); return; }
    let cancelled = false;
    setCharactersLoading(true);
    void listStoryAssets(projectId, "CHARACTER").then(({ items }) => { if (!cancelled) setCharacters(items); }).finally(() => { if (!cancelled) setCharactersLoading(false); });
    return () => { cancelled = true; };
  }, [parsed.items.length, projectId]);
  const update = (index: number, patch: Partial<PerformanceBinding>) => writeItems(parsed.items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item), onChange);
  const remove = (index: number) => writeItems(parsed.items.filter((_, itemIndex) => itemIndex !== index), onChange);
  return <EditorShell title="表演绑定" hint="角色、动作、时间范围与驱动来源均以受控值保存。" error={parsed.error} onReset={() => onChange("[]")} onAdd={() => writeItems([...parsed.items, { actor_id: "", action: "WALK", start_us: 0, end_us: 2_000_000, binding_type: "CHARACTER_DRIVING", source_role: "DRIVING_VIDEO" }], onChange)} addLabel="添加表演绑定">
    {parsed.items.map((item, index) => <div className="structured-control-row wide" key={index}>
      <label>角色<select value={item.actor_id} onChange={(event) => update(index, { actor_id: event.target.value })} disabled={!projectId || charactersLoading}><option value="">{projectId ? "选择项目角色" : "请先选择项目"}</option>{characters.filter((asset) => asset.status === "ACTIVE").map((asset) => <option key={asset.id} value={asset.id}>{asset.name}（{asset.code}）</option>)}</select></label>
      <label>动作<select value={item.action} onChange={(event) => update(index, { action: event.target.value })}>{PERFORMANCE_ACTIONS.map((action) => <option key={action} value={action}>{ACTION_LABELS[action]}</option>)}</select></label>
      <label>绑定类型<select value={item.binding_type} onChange={(event) => update(index, { binding_type: event.target.value })}>{BINDING_TYPES.map((type) => <option key={type} value={type}>{BINDING_LABELS[type]}</option>)}</select></label>
      <label>驱动来源<select value={item.source_role} onChange={(event) => update(index, { source_role: event.target.value })}>{REFERENCE_ROLES.map((role) => <option key={role} value={role}>{ROLE_LABELS[role]}</option>)}</select></label>
      <TimeRangeSlider startUs={item.start_us} endUs={item.end_us} onChange={(range) => update(index, range)} />
      <button type="button" className="secondary" aria-label={`删除表演绑定 ${index + 1}`} onClick={() => remove(index)}>删除</button>
    </div>)}
  </EditorShell>;
}

function mediaKindsForRole(role: string): Array<"IMAGE" | "VIDEO" | "AUDIO"> {
  if (role === "AUDIO_GUIDE") return ["AUDIO"];
  if (role === "DRIVING_VIDEO") return ["VIDEO"];
  return ["IMAGE", "VIDEO"];
}

export function ReferenceBindingEditor({ value, onChange, projectId }: EditorProps) {
  const parsed = parseItems<ReferenceBinding>(value);
  const update = (index: number, patch: Partial<ReferenceBinding>) => writeItems(parsed.items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item), onChange);
  const remove = (index: number) => writeItems(parsed.items.filter((_, itemIndex) => itemIndex !== index), onChange);
  return <EditorShell title="驱动与参考媒体" hint="从当前项目媒体库选择不可变版本，无需复制或手输 ID。" error={parsed.error} onReset={() => onChange("[]")} onAdd={() => writeItems([...parsed.items, { role: "CHARACTER_REFERENCE", media_version_id: "", ordinal: parsed.items.length, weight: 1 }], onChange)} addLabel="添加媒体绑定">
    {parsed.items.map((item, index) => <div className="structured-control-row wide" key={index}>
      <label>媒体用途<select value={item.role} onChange={(event) => update(index, { role: event.target.value, media_version_id: "" })}>{REFERENCE_ROLES.map((role) => <option key={role} value={role}>{ROLE_LABELS[role]}</option>)}</select></label>
      <ProjectMediaVersionSelect projectId={projectId} value={item.media_version_id} onChange={(mediaVersionId) => update(index, { media_version_id: mediaVersionId })} label="项目媒体版本" mediaKinds={mediaKindsForRole(item.role)} required />
      <label>顺序<input type="number" min="0" step="1" value={item.ordinal} onChange={(event) => update(index, { ordinal: Number(event.target.value) })} /></label>
      <label>权重<input type="number" min="0" max="1" step="0.05" value={item.weight} onChange={(event) => update(index, { weight: Number(event.target.value) })} /></label>
      <button type="button" className="secondary" aria-label={`删除媒体绑定 ${index + 1}`} onClick={() => remove(index)}>删除</button>
    </div>)}
  </EditorShell>;
}

export function MotionMaskEditor({ value, onChange, projectId }: EditorProps) {
  const parsed = parseItems<MotionMask>(value);
  const update = (index: number, patch: Partial<MotionMask>) => writeItems(parsed.items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item), onChange);
  const remove = (index: number) => writeItems(parsed.items.filter((_, itemIndex) => itemIndex !== index), onChange);
  return <EditorShell title="运动遮罩" hint="选择项目内已验证遮罩并指定主体区域。" error={parsed.error} onReset={() => onChange("[]")} onAdd={() => writeItems([...parsed.items, { media_version_id: "", subject_role: "subject", invert: false }], onChange)} addLabel="添加运动遮罩">
    {parsed.items.map((item, index) => <div className="structured-control-row" key={index}>
      <ProjectMediaVersionSelect projectId={projectId} value={item.media_version_id} onChange={(mediaVersionId) => update(index, { media_version_id: mediaVersionId })} label="遮罩媒体版本" mediaKinds={["IMAGE"]} required />
      <label>主体区域<select value={item.subject_role} onChange={(event) => update(index, { subject_role: event.target.value })}>{SUBJECT_ROLES.map((role) => <option key={role} value={role}>{SUBJECT_LABELS[role]}</option>)}</select></label>
      <label className="check-label"><input type="checkbox" checked={item.invert} onChange={(event) => update(index, { invert: event.target.checked })} />反转遮罩</label>
      <button type="button" className="secondary" aria-label={`删除运动遮罩 ${index + 1}`} onClick={() => remove(index)}>删除</button>
    </div>)}
  </EditorShell>;
}
