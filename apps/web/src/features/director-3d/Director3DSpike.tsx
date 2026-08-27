import { useEffect, useId, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type WheelEvent } from "react";
import { buildDirector3DOutput, cloneDirector3DValue, DEFAULT_DIRECTOR_3D_VALUE } from "./model";
import type { Director3DParticipant, Director3DReferenceExport, Director3DValue, Director3DVector } from "./types";
import { CAMERA_MOVEMENTS } from "../shared/directorOptions";
import "./director-3d.css";

export type Director3DSpikeProps = {
  value?: Director3DValue;
  defaultValue?: Director3DValue;
  disabled?: boolean;
  participantOptions?: Array<{ id: string; label: string }>;
  onChange?: (value: Director3DValue, output: ReturnType<typeof buildDirector3DOutput>) => void;
  onReferenceExport?: (reference: Director3DReferenceExport) => void | Promise<void>;
};

type ScreenPoint = { x: number; y: number; depth: number; visible: boolean };
const add = (a: Director3DVector, b: Director3DVector) => ({ x: a.x + b.x, y: a.y + b.y, z: a.z + b.z });
const subtract = (a: Director3DVector, b: Director3DVector) => ({ x: a.x - b.x, y: a.y - b.y, z: a.z - b.z });
const multiply = (a: Director3DVector, value: number) => ({ x: a.x * value, y: a.y * value, z: a.z * value });
const dot = (a: Director3DVector, b: Director3DVector) => a.x * b.x + a.y * b.y + a.z * b.z;
const cross = (a: Director3DVector, b: Director3DVector) => ({ x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x });
const normalize = (a: Director3DVector) => { const length = Math.hypot(a.x, a.y, a.z) || 1; return multiply(a, 1 / length); };
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

function projector(value: Director3DValue, width: number, height: number) {
  const forward = normalize(subtract(value.camera.target, value.camera.position));
  const right = normalize(cross(forward, { x: 0, y: 1, z: 0 }));
  const up = normalize(cross(right, forward));
  const focal = height * .5 / Math.tan(value.camera.fov_degrees * Math.PI / 360);
  return (point: Director3DVector): ScreenPoint => { const relative = subtract(point, value.camera.position); const depth = dot(relative, forward); return { x: width / 2 + dot(relative, right) * focal / Math.max(.05, depth), y: height / 2 - dot(relative, up) * focal / Math.max(.05, depth), depth, visible: depth > .05 }; };
}

function drawLine(context: CanvasRenderingContext2D, project: (point: Director3DVector) => ScreenPoint, from: Director3DVector, to: Director3DVector, color: string, width = 1) {
  const a = project(from), b = project(to); if (!a.visible || !b.visible) return;
  context.beginPath(); context.moveTo(a.x, a.y); context.lineTo(b.x, b.y); context.strokeStyle = color; context.lineWidth = width; context.stroke();
}

function drawMannequin(context: CanvasRenderingContext2D, project: (point: Director3DVector) => ScreenPoint, participant: Director3DParticipant, selected: boolean) {
  const p = participant.position; const color = participant.id === "A" ? "#73d4b7" : "#f18a70";
  const hip = add(p, { x: 0, y: .85, z: 0 }), chest = add(p, { x: 0, y: 1.38, z: 0 }), head = add(p, { x: 0, y: 1.75, z: 0 });
  const shoulderL = add(chest, { x: -.28, y: 0, z: 0 }), shoulderR = add(chest, { x: .28, y: 0, z: 0 });
  const points: Array<[Director3DVector, Director3DVector]> = [[p, add(hip, { x: -.13, y: 0, z: 0 })], [p, add(hip, { x: .13, y: 0, z: 0 })], [hip, chest], [shoulderL, shoulderR], [shoulderL, add(shoulderL, { x: -.18, y: -.48, z: 0 })], [shoulderR, add(shoulderR, { x: .18, y: -.48, z: 0 })]];
  points.forEach(([a, b]) => drawLine(context, project, a, b, color, selected ? 5 : 3));
  const screenHead = project(head); if (!screenHead.visible) return; const radius = clamp(45 / screenHead.depth, 7, 18);
  context.beginPath(); context.arc(screenHead.x, screenHead.y, radius, 0, Math.PI * 2); context.fillStyle = color; context.fill(); context.strokeStyle = selected ? "#fff" : "#202522"; context.lineWidth = selected ? 3 : 2; context.stroke();
  const angle = participant.facing_degrees * Math.PI / 180; drawLine(context, project, chest, add(chest, { x: Math.cos(angle) * .65, y: 0, z: Math.sin(angle) * .65 }), "#fff", 2);
  context.fillStyle = "#fff"; context.font = "700 12px system-ui"; context.textAlign = "center"; context.fillText(participant.id, screenHead.x, screenHead.y + 4);
}

function renderScene(canvas: HTMLCanvasElement, value: Director3DValue, selected: "A" | "B" | "CAMERA") {
  const rect = canvas.getBoundingClientRect(); const ratio = Math.min(window.devicePixelRatio || 1, 2); const width = Math.max(360, Math.round(rect.width * ratio)); const height = Math.max(260, Math.round(rect.height * ratio));
  if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
  const context = canvas.getContext("2d"); if (!context) return;
  const project = projector(value, width, height); const gradient = context.createLinearGradient(0, 0, 0, height); gradient.addColorStop(0, "#19201e"); gradient.addColorStop(1, "#30352f"); context.fillStyle = gradient; context.fillRect(0, 0, width, height);
  const halfW = value.scene.width_m / 2, halfD = value.scene.depth_m / 2;
  context.lineCap = "round"; for (let x = Math.ceil(-halfW); x <= halfW; x += 1) drawLine(context, project, { x, y: 0, z: -halfD }, { x, y: 0, z: halfD }, x === 0 ? "#68726b" : "#46504a", ratio);
  for (let z = Math.ceil(-halfD); z <= halfD; z += 1) drawLine(context, project, { x: -halfW, y: 0, z }, { x: halfW, y: 0, z }, z === 0 ? "#68726b" : "#46504a", ratio);
  drawLine(context, project, { x: -halfW, y: 0, z: -halfD }, { x: halfW, y: 0, z: -halfD }, "#a0a69e", 2 * ratio); drawLine(context, project, { x: halfW, y: 0, z: -halfD }, { x: halfW, y: 0, z: halfD }, "#a0a69e", 2 * ratio); drawLine(context, project, { x: halfW, y: 0, z: halfD }, { x: -halfW, y: 0, z: halfD }, "#a0a69e", 2 * ratio); drawLine(context, project, { x: -halfW, y: 0, z: halfD }, { x: -halfW, y: 0, z: -halfD }, "#a0a69e", 2 * ratio);
  [...value.participants].sort((a, b) => project(b.position).depth - project(a.position).depth).forEach((participant) => drawMannequin(context, project, participant, selected === participant.id));
  context.fillStyle = "rgba(12,15,14,.72)"; context.fillRect(12 * ratio, 12 * ratio, 190 * ratio, 43 * ratio); context.fillStyle = "#d9ded9"; context.font = `${11 * ratio}px system-ui`; context.textAlign = "left"; context.fillText(`${value.camera.fov_degrees}° FOV · ${value.camera.movement}`, 23 * ratio, 31 * ratio); context.fillStyle = "#929c95"; context.font = `${9 * ratio}px system-ui`; context.fillText("Canvas 3D projection · no source image loaded", 23 * ratio, 46 * ratio);
}

export function Director3DSpike({ value, defaultValue, disabled = false, participantOptions = [], onChange, onReferenceExport }: Director3DSpikeProps) {
  const [local, setLocal] = useState(() => cloneDirector3DValue(defaultValue ?? DEFAULT_DIRECTOR_3D_VALUE)); const [selected, setSelected] = useState<"A" | "B" | "CAMERA">("CAMERA"); const [exporting, setExporting] = useState(false);
  const current = value ?? local; const output = useMemo(() => buildDirector3DOutput(current), [current]); const canvasRef = useRef<HTMLCanvasElement>(null); const drag = useRef<{ x: number; y: number } | null>(null); const titleId = useId();
  const commit = (next: Director3DValue) => { if (!value) setLocal(next); onChange?.(next, buildDirector3DOutput(next)); };
  useEffect(() => { const canvas = canvasRef.current; if (!canvas) return; const draw = () => renderScene(canvas, current, selected); draw(); const observer = new ResizeObserver(draw); observer.observe(canvas); return () => observer.disconnect(); }, [current, selected]);
  const orbit = (dx: number, dy: number) => { const next = cloneDirector3DValue(current); const relative = subtract(next.camera.position, next.camera.target); const radius = Math.max(1, Math.hypot(relative.x, relative.y, relative.z)); const yaw = Math.atan2(relative.z, relative.x) - dx * .008; const pitch = clamp(Math.asin(relative.y / radius) + dy * .006, -.15, 1.25); next.camera.position = add(next.camera.target, { x: Math.cos(pitch) * Math.cos(yaw) * radius, y: Math.sin(pitch) * radius, z: Math.cos(pitch) * Math.sin(yaw) * radius }); commit(next); };
  const pointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => { if (!drag.current || disabled) return; orbit(event.clientX - drag.current.x, event.clientY - drag.current.y); drag.current = { x: event.clientX, y: event.clientY }; };
  const wheel = (event: WheelEvent<HTMLCanvasElement>) => { if (disabled) return; event.preventDefault(); const next = cloneDirector3DValue(current); next.camera.fov_degrees = clamp(next.camera.fov_degrees + Math.sign(event.deltaY) * 2, 20, 100); commit(next); };
  const updateParticipant = (id: "A" | "B", patch: Partial<Director3DParticipant>) => { const next = cloneDirector3DValue(current); const index = id === "A" ? 0 : 1; next.participants[index] = { ...next.participants[index], ...patch }; commit(next); };
  const updatePosition = (id: "A" | "B", axis: "x" | "z", coordinate: number) => { const participant = current.participants[id === "A" ? 0 : 1]; updateParticipant(id, { position: { ...participant.position, [axis]: coordinate } }); };
  const exportReference = async () => { const canvas = canvasRef.current; if (!canvas || exporting) return; setExporting(true); try { const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob((result) => result ? resolve(result) : reject(new Error("Canvas export failed")), "image/png")); const filename = `director-3d-reference-${Date.now()}.png`; const reference = { blob, filename, width: canvas.width, height: canvas.height, mime_type: "image/png" as const, staging_3d: cloneDirector3DValue(current), prompt_context: output.prompt_context }; await onReferenceExport?.(reference); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 0); } finally { setExporting(false); } };
  const selectedParticipant = selected === "CAMERA" ? null : current.participants[selected === "A" ? 0 : 1];

  return <section className="director-3d" aria-labelledby={titleId}><header className="director-3d__head"><div><span>三维导演预演</span><h3 id={titleId}>{current.scene.label}</h3></div><div><span className="director-3d__capability">透视预览</span><button type="button" onClick={() => void exportReference()} disabled={exporting}>{exporting ? "导出中…" : "导出截图参考"}</button></div></header><div className="director-3d__workspace"><div className="director-3d__viewport"><canvas ref={canvasRef} aria-label="可交互三维导演预演，拖动旋转摄影机，滚轮调整视场角" tabIndex={0} onPointerDown={(event) => { if (!disabled) { drag.current = { x: event.clientX, y: event.clientY }; event.currentTarget.setPointerCapture(event.pointerId); } }} onPointerMove={pointerMove} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onWheel={wheel} /><p>拖动环绕摄影机 · 滚轮调整视场角 · 不读取任何素材原图</p></div><aside className="director-3d__controls"><div className="director-3d__tabs" role="group" aria-label="编辑对象">{(["CAMERA", "A", "B"] as const).map((id) => <button type="button" key={id} aria-pressed={selected === id} className={selected === id ? "selected" : ""} onClick={() => setSelected(id)}>{id === "CAMERA" ? "摄影机" : `角色 ${id}`}</button>)}</div>{selectedParticipant ? <>{participantOptions.length ? <label>本镜出场角色<select aria-label={`三维预演角色 ${selectedParticipant.id}`} value={selectedParticipant.asset_id ?? ""} disabled={disabled} onChange={(event) => { const option = participantOptions.find((item) => item.id === event.target.value); updateParticipant(selectedParticipant.id, { asset_id: option?.id ?? null, label: option?.label ?? `角色 ${selectedParticipant.id}` }); }}><option value="">请选择已绑定角色</option>{participantOptions.map((option) => <option key={option.id} value={option.id} disabled={current.participants.some((participant) => participant.id !== selectedParticipant.id && participant.asset_id === option.id)}>{option.label}</option>)}</select></label> : <p>本镜尚未绑定角色资料，请先在“资产”标签绑定。</p>}<div className="director-3d__fields"><label>横向位置<input type="number" min={-current.scene.width_m / 2} max={current.scene.width_m / 2} step="0.1" value={selectedParticipant.position.x} disabled={disabled} onChange={(e) => updatePosition(selectedParticipant.id, "x", Number(e.target.value))} /></label><label>纵深位置<input type="number" min={-current.scene.depth_m / 2} max={current.scene.depth_m / 2} step="0.1" value={selectedParticipant.position.z} disabled={disabled} onChange={(e) => updatePosition(selectedParticipant.id, "z", Number(e.target.value))} /></label></div><label>朝向 <output>{selectedParticipant.facing_degrees}°</output><input type="range" min="0" max="359" value={selectedParticipant.facing_degrees} disabled={disabled} onChange={(e) => updateParticipant(selectedParticipant.id, { facing_degrees: Number(e.target.value) })} /></label></> : <><label>视场角 <output>{current.camera.fov_degrees}°</output><input type="range" min="20" max="100" value={current.camera.fov_degrees} disabled={disabled} onChange={(e) => { const next = cloneDirector3DValue(current); next.camera.fov_degrees = Number(e.target.value); commit(next); }} /></label><label>运镜<select value={current.camera.movement} disabled={disabled} onChange={(e) => { const next = cloneDirector3DValue(current); next.camera.movement = e.target.value; commit(next); }}>{CAMERA_MOVEMENTS.filter(([value]) => ["STATIC", "PUSH_IN", "PULL_OUT", "PAN", "TRUCK", "ORBIT"].includes(value)).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>强度 <output>{Math.round(current.camera.intensity * 100)}%</output><input type="range" min="0" max="1" step="0.05" value={current.camera.intensity} disabled={disabled} onChange={(e) => { const next = cloneDirector3DValue(current); next.camera.intensity = Number(e.target.value); commit(next); }} /></label></>}<details><summary>高级：生成上下文</summary><p>{output.prompt_context}</p><code>{output.camera_plan.movement} · {output.camera_plan.direction} · 视场角 {current.camera.fov_degrees}°</code></details></aside></div></section>;
}
