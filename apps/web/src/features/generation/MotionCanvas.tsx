import { useId, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import type { MotionControlPoint } from "../../generated/api";

type Props = {
  mediaVersionId: string;
  value: MotionControlPoint[];
  disabled?: boolean;
  onChange: (points: MotionControlPoint[]) => void;
};

const clamp = (value: number) => Math.max(0, Math.min(1, value));
const round = (value: number) => Math.round(value * 10_000) / 10_000;

export function MotionCanvas({ mediaVersionId, value, disabled, onChange }: Props) {
  const [pressure, setPressure] = useState(1);
  const drawing = useRef(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const markerId = useId().replaceAll(":", "");
  const pointOf = (event: ReactPointerEvent<SVGSVGElement>): MotionControlPoint => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect?.width || !rect.height) return { x: 0.5, y: 0.5, pressure };
    const clientX = Number.isFinite(event.clientX) ? event.clientX : rect.left + rect.width / 2;
    const clientY = Number.isFinite(event.clientY) ? event.clientY : rect.top + rect.height / 2;
    return {
      x: round(clamp((clientX - rect.left) / rect.width)),
      y: round(clamp((clientY - rect.top) / rect.height)),
      pressure,
    };
  };
  const begin = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (disabled) return;
    drawing.current = true;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    onChange([pointOf(event)]);
  };
  const move = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!drawing.current || disabled) return;
    const next = pointOf(event);
    const previous = value.at(-1);
    if (previous && Math.hypot(next.x - previous.x, next.y - previous.y) < .006) return;
    onChange([...value, next].slice(-5_000));
  };
  const finish = () => { drawing.current = false; };
  const drawWithKeyboard = (event: KeyboardEvent<SVGSVGElement>) => {
    if (disabled) return;
    const delta = 0.02;
    const previous = value.at(-1) ?? { x: 0.5, y: 0.5, pressure };
    const offsets: Record<string, [number, number]> = {
      ArrowLeft: [-delta, 0], ArrowRight: [delta, 0], ArrowUp: [0, -delta], ArrowDown: [0, delta],
    };
    if (event.key === "Backspace" || event.key === "Delete") {
      event.preventDefault();
      onChange(value.slice(0, -1));
      return;
    }
    const offset = offsets[event.key];
    if (!offset) return;
    event.preventDefault();
    onChange([...value, { x: round(clamp(previous.x + offset[0])), y: round(clamp(previous.y + offset[1])), pressure }].slice(-5_000));
  };
  const points = value.map((point) => `${point.x * 1000},${point.y * 562}`).join(" ");

  return <section className="motion-canvas" aria-labelledby={`${markerId}-title`}>
    <div className="motion-canvas__head"><div><strong id={`${markerId}-title`}>运动轨迹画布</strong><span>在画面上按住并拖动，绘制主体运动方向。</span></div><div><button type="button" className="secondary" disabled={disabled || value.length === 0} onClick={() => onChange(value.slice(0, -1))}>撤销一点</button><button type="button" className="secondary" disabled={disabled || value.length === 0} onClick={() => onChange([])}>清空轨迹</button></div></div>
    <div className="motion-canvas__stage">
      <img src={`/api/v1/media-versions/${encodeURIComponent(mediaVersionId)}/thumbnail?size=medium&frame=poster`} alt="运动轨迹参考画面" draggable={false} />
      <span id={`${markerId}-keyboard-help`} className="sr-only">方向键逐点绘制，退格键或删除键撤销。</span>
      <svg ref={svgRef} viewBox="0 0 1000 562" role="application" aria-label="运动轨迹绘制区域" aria-describedby={`${markerId}-keyboard-help`} tabIndex={disabled ? -1 : 0} onKeyDown={drawWithKeyboard} onPointerDown={begin} onPointerMove={move} onPointerUp={finish} onPointerCancel={finish} onPointerLeave={finish}>
        <defs><marker id={markerId} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
        {value.length > 1 && <polyline points={points} markerEnd={`url(#${markerId})`} />}
        {value.map((point, index) => <circle key={`${point.x}-${point.y}-${index}`} cx={point.x * 1000} cy={point.y * 562} r={index === value.length - 1 ? 7 : 4} />)}
      </svg>
    </div>
    <label>笔刷力度 <output>{Math.round(pressure * 100)}%</output><input type="range" min="0.1" max="1" step="0.1" value={pressure} disabled={disabled} onChange={(event) => setPressure(Number(event.target.value))} /></label>
    <p className="motion-canvas__status" role="status">{value.length > 1 ? `已记录 ${value.length} 个轨迹点；箭头指向运动终点。` : "尚未绘制轨迹。"}</p>
    <details><summary>高级数据（只读）</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>
  </section>;
}
