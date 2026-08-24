import { useId, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { CAMERA_MOVEMENTS } from "../shared/directorOptions";
import "./staging-board.css";

export type StagingPoint = { x: number; y: number };
export type StagingParticipantId = "A" | "B";

export type StagingParticipant = {
  id: StagingParticipantId;
  asset_id?: string | null;
  label: string;
  position: StagingPoint;
  facing_degrees: number;
  movement_target: StagingPoint;
};

export type StagingCamera = {
  position: StagingPoint;
  target: StagingPoint;
  movement_target: StagingPoint;
  movement: string;
  intensity: number;
};

export type StagingBoardValue = {
  schema_version: "staging-board.v1";
  scene: { label: string; width_m: number; depth_m: number };
  participants: [StagingParticipant, StagingParticipant];
  camera: StagingCamera;
  axis: { start: StagingPoint; end: StagingPoint };
};

export type StagingBoardOutput = {
  blocking_summary: string;
  camera_plan: {
    movement: string;
    direction: string;
    intensity: number;
    prompt_text: string;
  };
  prompt_context: string;
  staging: StagingBoardValue;
  director_intent_patch: {
    performance: { blocking_summary: string };
    camera_plan: StagingBoardOutput["camera_plan"];
  };
};

export type StagingEyeLineSuggestion = {
  value: "LOOK_LEFT" | "LOOK_RIGHT";
  subject_label: string;
  target_label: string;
  reason: string;
  source_fingerprint: string;
};

export type StagingBoardProps = {
  value?: StagingBoardValue;
  defaultValue?: StagingBoardValue;
  disabled?: boolean;
  participantOptions?: Array<{ id: string; label: string }>;
  onChange?: (value: StagingBoardValue, output: StagingBoardOutput) => void;
};

const DEFAULT_VALUE: StagingBoardValue = {
  schema_version: "staging-board.v1",
  scene: { label: "场景平面", width_m: 8, depth_m: 5 },
  participants: [
    { id: "A", label: "角色 A", position: { x: 34, y: 31 }, facing_degrees: 12, movement_target: { x: 45, y: 31 } },
    { id: "B", label: "角色 B", position: { x: 65, y: 34 }, facing_degrees: 192, movement_target: { x: 57, y: 34 } },
  ],
  camera: { position: { x: 49, y: 55 }, target: { x: 50, y: 32 }, movement_target: { x: 49, y: 48 }, movement: "PUSH_IN", intensity: 0.45 },
  axis: { start: { x: 21, y: 32 }, end: { x: 79, y: 32 } },
};

const cloneValue = (value: StagingBoardValue): StagingBoardValue => JSON.parse(JSON.stringify(value)) as StagingBoardValue;
const clamp = (value: number, min = 3, max = 97) => Math.min(max, Math.max(min, value));
const round = (value: number) => Math.round(value * 10) / 10;
const describePoint = (point: StagingPoint) => `${round(point.x)}%,${round(point.y)}%`;

function cardinalDirection(from: StagingPoint, to: StagingPoint) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (Math.abs(dx) > Math.abs(dy)) return dx >= 0 ? "RIGHT" : "LEFT";
  return dy >= 0 ? "BACKWARD" : "FORWARD";
}

export function suggestEyeLineFromStaging(value: StagingBoardValue): StagingEyeLineSuggestion | null {
  const distance = (left: StagingPoint, right: StagingPoint) => Math.hypot(right.x - left.x, right.y - left.y);
  const [first, second] = value.participants;
  const subject = distance(value.camera.target, first.position) <= distance(value.camera.target, second.position) ? first : second;
  const target = subject.id === first.id ? second : first;
  const targetAngle = Math.atan2(target.position.y - subject.position.y, target.position.x - subject.position.x) * 180 / Math.PI;
  const angleDelta = Math.abs((((targetAngle - subject.facing_degrees) % 360) + 540) % 360 - 180);
  if (angleDelta > 75) return null;
  const viewX = subject.position.x - value.camera.position.x;
  const viewY = subject.position.y - value.camera.position.y;
  const targetX = target.position.x - subject.position.x;
  const targetY = target.position.y - subject.position.y;
  const screenOffset = (-viewY * targetX) + (viewX * targetY);
  if (Math.abs(screenOffset) < 1) return null;
  return {
    value: screenOffset > 0 ? "LOOK_RIGHT" : "LOOK_LEFT",
    subject_label: subject.label,
    target_label: target.label,
    reason: `${subject.label}的朝向与${target.label}夹角 ${Math.round(angleDelta)}°，按当前摄影机视角位于画面${screenOffset > 0 ? "右" : "左"}侧。`,
    source_fingerprint: JSON.stringify(value),
  };
}

export function buildStagingBoardOutput(value: StagingBoardValue): StagingBoardOutput {
  const [a, b] = value.participants;
  const cameraDirection = cardinalDirection(value.camera.position, value.camera.movement_target);
  const blocking = `${a.label}站位(${describePoint(a.position)})，朝向${round(a.facing_degrees)}°，移动至(${describePoint(a.movement_target)})；${b.label}站位(${describePoint(b.position)})，朝向${round(b.facing_degrees)}°，移动至(${describePoint(b.movement_target)})。180°轴线从(${describePoint(value.axis.start)})到(${describePoint(value.axis.end)})。`;
  const promptContext = `Top-down staging in ${value.scene.label} (${value.scene.width_m}m × ${value.scene.depth_m}m). ${blocking} Camera at (${describePoint(value.camera.position)}), aimed at (${describePoint(value.camera.target)}), ${value.camera.movement} ${cameraDirection} to (${describePoint(value.camera.movement_target)}). Keep camera on the established side of the 180-degree axis.`;
  const cameraPlan = {
    movement: value.camera.movement,
    direction: cameraDirection,
    intensity: value.camera.intensity,
    prompt_text: promptContext,
  };
  return {
    blocking_summary: blocking,
    camera_plan: cameraPlan,
    prompt_context: promptContext,
    staging: cloneValue(value),
    director_intent_patch: { performance: { blocking_summary: blocking }, camera_plan: cameraPlan },
  };
}

type Selection = StagingParticipantId | "A_TARGET" | "B_TARGET" | "CAMERA" | "CAMERA_TARGET" | "CAMERA_MOVE" | "AXIS_START" | "AXIS_END";

export function StagingBoard({ value, defaultValue, disabled = false, participantOptions = [], onChange }: StagingBoardProps) {
  const [localValue, setLocalValue] = useState(() => cloneValue(defaultValue ?? DEFAULT_VALUE));
  const [selection, setSelection] = useState<Selection>("A");
  const board = value ?? localValue;
  const output = useMemo(() => buildStagingBoardOutput(board), [board]);
  const titleId = useId();
  const svgRef = useRef<SVGSVGElement>(null);
  const dragging = useRef<Selection | null>(null);

  const commit = (next: StagingBoardValue) => {
    if (!value) setLocalValue(next);
    onChange?.(next, buildStagingBoardOutput(next));
  };
  const updateSelection = (selected: Selection, point: StagingPoint) => {
    const next = cloneValue(board);
    if (selected === "CAMERA") next.camera.position = point;
    else if (selected === "CAMERA_TARGET") next.camera.target = point;
    else if (selected === "CAMERA_MOVE") next.camera.movement_target = point;
    else if (selected === "AXIS_START") next.axis.start = point;
    else if (selected === "AXIS_END") next.axis.end = point;
    else if (selected === "A_TARGET" || selected === "B_TARGET") next.participants[selected === "A_TARGET" ? 0 : 1].movement_target = point;
    else next.participants[selected === "A" ? 0 : 1].position = point;
    commit(next);
  };
  const pointFromEvent = (event: ReactPointerEvent<SVGSVGElement>): StagingPoint => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return { x: 50, y: 32 };
    return { x: round(clamp(((event.clientX - rect.left) / rect.width) * 100)), y: round(clamp(((event.clientY - rect.top) / rect.height) * 64, 3, 61)) };
  };
  const beginDrag = (selected: Selection) => (event: ReactPointerEvent<SVGGElement>) => {
    if (disabled) return;
    event.stopPropagation();
    dragging.current = selected;
    setSelection(selected);
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const keyboardMove = (selected: Selection) => (event: KeyboardEvent<SVGGElement>) => {
    if (disabled || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const current = selected === "CAMERA" ? board.camera.position
      : selected === "CAMERA_TARGET" ? board.camera.target
        : selected === "CAMERA_MOVE" ? board.camera.movement_target
          : selected === "AXIS_START" ? board.axis.start
            : selected === "AXIS_END" ? board.axis.end
              : selected === "A_TARGET" || selected === "B_TARGET" ? board.participants[selected === "A_TARGET" ? 0 : 1].movement_target
                : board.participants[selected === "A" ? 0 : 1].position;
    const step = event.shiftKey ? 5 : 1;
    updateSelection(selected, { x: clamp(current.x + (event.key === "ArrowRight" ? step : event.key === "ArrowLeft" ? -step : 0)), y: clamp(current.y + (event.key === "ArrowDown" ? step : event.key === "ArrowUp" ? -step : 0), 3, 61) });
  };
  const updateParticipant = (id: StagingParticipantId, patch: Partial<StagingParticipant>) => {
    const next = cloneValue(board);
    const index = id === "A" ? 0 : 1;
    next.participants[index] = { ...next.participants[index], ...patch };
    commit(next);
  };
  const selectedParticipant = selection === "A" || selection === "B" || selection === "A_TARGET" || selection === "B_TARGET"
    ? board.participants[selection === "A" || selection === "A_TARGET" ? 0 : 1]
    : null;

  return <section className="staging-board" aria-labelledby={titleId}>
    <header className="staging-board__header">
      <div><span>2D STAGING BOARD</span><h3 id={titleId}>{board.scene.label}</h3></div>
      <p>拖动站位与摄影机，方向键可微调；输出会同步为 DirectorIntent V3 补丁。</p>
    </header>

    <div className="staging-board__workspace">
      <div className="staging-board__canvas-wrap">
        <svg ref={svgRef} className="staging-board__canvas" viewBox="0 0 100 64" role="img" aria-label="场景俯视平面，可编辑角色、摄影机与180度轴线"
          onPointerDown={(event) => { if (!disabled) updateSelection(selection, pointFromEvent(event)); }}
          onPointerMove={(event) => { if (dragging.current) updateSelection(dragging.current, pointFromEvent(event)); }}
          onPointerUp={() => { dragging.current = null; }} onPointerCancel={() => { dragging.current = null; }}>
          <defs><pattern id={`${titleId}-grid`} width="5" height="5" patternUnits="userSpaceOnUse"><path d="M 5 0 L 0 0 0 5" fill="none" stroke="currentColor" strokeWidth=".18" /></pattern><marker id={`${titleId}-arrow`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 0L8 4L0 8Z" /></marker></defs>
          <rect className="staging-board__room" x="1" y="1" width="98" height="62" rx="1.2" />
          <rect className="staging-board__grid" x="1" y="1" width="98" height="62" rx="1.2" fill={`url(#${titleId}-grid)`} />
          <line className="staging-board__axis" x1={board.axis.start.x} y1={board.axis.start.y} x2={board.axis.end.x} y2={board.axis.end.y} />
          <text className="staging-board__axis-label" x={(board.axis.start.x + board.axis.end.x) / 2} y={(board.axis.start.y + board.axis.end.y) / 2 - 1.4}>180° AXIS</text>
          {(["AXIS_START", "AXIS_END"] as const).map((id, index) => { const point = index ? board.axis.end : board.axis.start; return <g key={id} className={`staging-board__axis-handle${selection === id ? " is-selected" : ""}`} role="button" tabIndex={disabled ? -1 : 0} aria-label={`180度轴线${index ? "终点" : "起点"}`} transform={`translate(${point.x} ${point.y})`} onPointerDown={beginDrag(id)} onKeyDown={keyboardMove(id)}><circle className="staging-board__hit-target" r="4.2" /><circle r="1.55" /><path d="M-1 0H1M0-1V1" /></g>; })}
          {board.participants.map((participant) => <g key={participant.id}>
            <line className={`staging-board__motion staging-board__motion--${participant.id.toLowerCase()}`} x1={participant.position.x} y1={participant.position.y} x2={participant.movement_target.x} y2={participant.movement_target.y} markerEnd={`url(#${titleId}-arrow)`} />
            <g className={`staging-board__movement-target staging-board__movement-target--${participant.id.toLowerCase()}${selection === `${participant.id}_TARGET` ? " is-selected" : ""}`} role="button" tabIndex={disabled ? -1 : 0} aria-label={`${participant.label}运动终点，位置 ${describePoint(participant.movement_target)}`} transform={`translate(${participant.movement_target.x} ${participant.movement_target.y})`} onPointerDown={beginDrag(`${participant.id}_TARGET` as Selection)} onKeyDown={keyboardMove(`${participant.id}_TARGET` as Selection)}><circle className="staging-board__hit-target" r="4.2" /><circle r="1.8" /><path d="M-1.1 0H1.1M0-1.1V1.1" /></g>
            <g className={`staging-board__participant staging-board__participant--${participant.id.toLowerCase()}${selection === participant.id ? " is-selected" : ""}`} role="button" tabIndex={disabled ? -1 : 0} aria-label={`${participant.label}，站位 ${describePoint(participant.position)}`} transform={`translate(${participant.position.x} ${participant.position.y})`} onPointerDown={beginDrag(participant.id)} onKeyDown={keyboardMove(participant.id)}>
              <circle className="staging-board__hit-target" r="4.2" /><circle r="3.25" /><text y="1.2">{participant.id}</text><line className="staging-board__facing" x1="0" y1="0" x2={Math.cos(participant.facing_degrees * Math.PI / 180) * 5.3} y2={Math.sin(participant.facing_degrees * Math.PI / 180) * 5.3} />
            </g>
          </g>)}
          <line className="staging-board__camera-view" x1={board.camera.position.x} y1={board.camera.position.y} x2={board.camera.target.x} y2={board.camera.target.y} />
          <g className={`staging-board__camera-target${selection === "CAMERA_TARGET" ? " is-selected" : ""}`} role="button" tabIndex={disabled ? -1 : 0} aria-label={`摄影机朝向目标，位置 ${describePoint(board.camera.target)}`} transform={`translate(${board.camera.target.x} ${board.camera.target.y})`} onPointerDown={beginDrag("CAMERA_TARGET")} onKeyDown={keyboardMove("CAMERA_TARGET")}><circle className="staging-board__hit-target" r="4.2" /><circle r="1.7" /><path d="M-2.8 0H2.8M0-2.8V2.8" /></g>
          <line className="staging-board__motion staging-board__motion--camera" x1={board.camera.position.x} y1={board.camera.position.y} x2={board.camera.movement_target.x} y2={board.camera.movement_target.y} markerEnd={`url(#${titleId}-arrow)`} />
          <g className={`staging-board__movement-target staging-board__movement-target--camera${selection === "CAMERA_MOVE" ? " is-selected" : ""}`} role="button" tabIndex={disabled ? -1 : 0} aria-label={`摄影机运动终点，位置 ${describePoint(board.camera.movement_target)}`} transform={`translate(${board.camera.movement_target.x} ${board.camera.movement_target.y})`} onPointerDown={beginDrag("CAMERA_MOVE")} onKeyDown={keyboardMove("CAMERA_MOVE")}><circle className="staging-board__hit-target" r="4.2" /><circle r="1.8" /><path d="M-1.1 0H1.1M0-1.1V1.1" /></g>
          <g className={`staging-board__camera${selection === "CAMERA" ? " is-selected" : ""}`} role="button" tabIndex={disabled ? -1 : 0} aria-label={`摄影机，位置 ${describePoint(board.camera.position)}`} transform={`translate(${board.camera.position.x} ${board.camera.position.y})`} onPointerDown={beginDrag("CAMERA")} onKeyDown={keyboardMove("CAMERA")}><circle className="staging-board__hit-target" r="4.2" /><path d="M0-3.5L3.6 3H-3.6Z" /><text y="6">CAM</text></g>
        </svg>
        <div className="staging-board__legend" aria-label="图例"><span><i className="actor-a" />角色 A</span><span><i className="actor-b" />角色 B</span><span><i className="camera" />摄影机</span><span><i className="axis" />180°轴线</span></div>
      </div>

      <aside className="staging-board__controls" aria-label="站位参数">
        <div className="staging-board__tabs" role="group" aria-label="选择编辑对象">{(["A", "B", "CAMERA"] as const).map((id) => { const selected = selection === id || (id === "A" && selection === "A_TARGET") || (id === "B" && selection === "B_TARGET") || (id === "CAMERA" && ["CAMERA_TARGET", "CAMERA_MOVE"].includes(selection)); return <button key={id} type="button" className={selected ? "is-selected" : ""} aria-pressed={selected} disabled={disabled} onClick={() => setSelection(id)}>{id === "CAMERA" ? "摄影机" : `角色 ${id}`}</button>; })}</div>
        {selectedParticipant ? <>
          {participantOptions.length ? <label>本镜出场角色<select aria-label={`角色 ${selectedParticipant.id} 资产`} value={selectedParticipant.asset_id ?? ""} disabled={disabled} onChange={(event) => { const option = participantOptions.find((item) => item.id === event.target.value); updateParticipant(selectedParticipant.id, { asset_id: option?.id ?? null, label: option?.label ?? `角色 ${selectedParticipant.id}` }); }}><option value="">请选择已绑定角色</option>{participantOptions.map((option) => <option key={option.id} value={option.id} disabled={board.participants.some((participant) => participant.id !== selectedParticipant.id && participant.asset_id === option.id)}>{option.label}</option>)}</select></label> : <p className="staging-board__hint">本镜尚未绑定角色资产。请先在“资产”标签绑定出场角色，再回到站位板选择。</p>}
          <label>朝向 <output>{round(selectedParticipant.facing_degrees)}°</output><input type="range" min="0" max="359" value={selectedParticipant.facing_degrees} disabled={disabled} onChange={(event) => updateParticipant(selectedParticipant.id, { facing_degrees: Number(event.target.value) })} /></label>
          <p className="staging-board__hint">在画布中拖动角色移动终点；方向键微调，Shift + 方向键大步移动。</p>
          <details><summary>高级坐标</summary><div className="staging-board__fields"><label>运动终点 X<input type="number" min="3" max="97" value={selectedParticipant.movement_target.x} disabled={disabled} onChange={(event) => updateParticipant(selectedParticipant.id, { movement_target: { ...selectedParticipant.movement_target, x: clamp(Number(event.target.value)) } })} /></label><label>运动终点 Y<input type="number" min="3" max="61" value={selectedParticipant.movement_target.y} disabled={disabled} onChange={(event) => updateParticipant(selectedParticipant.id, { movement_target: { ...selectedParticipant.movement_target, y: clamp(Number(event.target.value), 3, 61) } })} /></label></div></details>
        </> : selection === "CAMERA" || selection === "CAMERA_TARGET" ? <>
          <label>运镜<select value={board.camera.movement} disabled={disabled} onChange={(event) => { const next = cloneValue(board); next.camera.movement = event.target.value; commit(next); }}>{CAMERA_MOVEMENTS.filter(([value]) => ["STATIC", "PUSH_IN", "PULL_OUT", "PAN", "TRUCK", "ORBIT"].includes(value)).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>强度 <output>{Math.round(board.camera.intensity * 100)}%</output><input type="range" min="0" max="1" step="0.05" value={board.camera.intensity} disabled={disabled} onChange={(event) => { const next = cloneValue(board); next.camera.intensity = Number(event.target.value); commit(next); }} /></label>
          <p className="staging-board__hint">在画布中分别拖动摄影机、朝向目标和运动终点。</p>
          <details><summary>高级坐标</summary><div className="staging-board__fields"><label>朝向目标 X<input type="number" min="3" max="97" value={board.camera.target.x} disabled={disabled} onChange={(event) => { const next = cloneValue(board); next.camera.target.x = clamp(Number(event.target.value)); commit(next); }} /></label><label>朝向目标 Y<input type="number" min="3" max="61" value={board.camera.target.y} disabled={disabled} onChange={(event) => { const next = cloneValue(board); next.camera.target.y = clamp(Number(event.target.value), 3, 61); commit(next); }} /></label></div><div className="staging-board__fields"><label>运动终点 X<input type="number" min="3" max="97" value={board.camera.movement_target.x} disabled={disabled} onChange={(event) => { const next = cloneValue(board); next.camera.movement_target.x = clamp(Number(event.target.value)); commit(next); }} /></label><label>运动终点 Y<input type="number" min="3" max="61" value={board.camera.movement_target.y} disabled={disabled} onChange={(event) => { const next = cloneValue(board); next.camera.movement_target.y = clamp(Number(event.target.value), 3, 61); commit(next); }} /></label></div></details>
        </> : <p className="staging-board__hint">拖动轴线端点以建立 180° 规则。</p>}
        <details><summary>结构化输出</summary><dl><div><dt>Blocking</dt><dd>{output.blocking_summary}</dd></div><div><dt>Camera</dt><dd>{output.camera_plan.movement} · {output.camera_plan.direction} · {Math.round(output.camera_plan.intensity * 100)}%</dd></div></dl></details>
      </aside>
    </div>
  </section>;
}
