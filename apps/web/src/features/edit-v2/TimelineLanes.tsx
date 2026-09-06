import type { ReactNode } from "react";
import type { EditAudioClipV2, EditVideoClipV2 } from "../../generated/api";
import "../timeline-v2/timeline-v2.css";

export type TimelineVideoClip = EditVideoClipV2 & {
  start_us: number;
  end_us: number;
};

export function timelineTimeLabel(us: number) {
  const seconds = Math.max(0, us) / 1_000_000;
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;
}
const TRANSITION_LABELS: Record<string, string> = { CUT: "硬切", DISSOLVE: "叠化", FADE: "淡入" };

export function TimelineLanes({
  clips,
  audio,
  subtitle,
  durationUs,
  zoom,
  playheadUs,
  selectedShotId,
  includeDialogue,
  includeMusic,
  includeSubtitles,
  ariaLabel = "多轨编辑时间线",
  onSelect,
  onPlayheadChange,
}: {
  clips: TimelineVideoClip[];
  audio: EditAudioClipV2[];
  subtitle: { revision_no: number; cue_count: number } | null;
  durationUs: number;
  zoom: number;
  playheadUs: number;
  selectedShotId: string | null;
  includeDialogue: boolean;
  includeMusic: boolean;
  includeSubtitles: boolean;
  ariaLabel?: string;
  onSelect: (shotId: string, startUs: number) => void;
  onPlayheadChange?: (us: number) => void;
}) {
  const contentWidth = Math.max(900, (durationUs / 1_000_000) * 48 * zoom);
  const headerW = 100; // matches --lane-head-w default, mobile 72 handled via ratio still approx
  const railWidth = Math.max(200, contentWidth - headerW);
  const position = (start: number, end: number) => ({
    left: `${durationUs ? (start / durationUs) * 100 : 0}%`,
    width: `${durationUs ? Math.max(0.3, ((end - start) / durationUs) * 100) : 0}%`,
  });
  const audioLane = (lane: string) => audio.filter((item) => item.lane === lane);
  const resolveHeaderW = (element: HTMLElement) => {
    const header = element.querySelector(".edit-lane > header") as HTMLElement | null;
    const w = header ? Math.round(header.getBoundingClientRect().width + 12) : headerW;
    return Math.max(72, Math.min(112, w));
  };
  const scrubFromClientX = (clientX: number, element: HTMLElement) => {
    if (!durationUs || !onPlayheadChange) return;
    const rect = element.getBoundingClientRect();
    const hw = resolveHeaderW(element);
    const rw = Math.max(200, rect.width - hw);
    const railLeft = rect.left + hw;
    const ratio = Math.max(0, Math.min(1, (clientX - railLeft) / rw));
    onPlayheadChange(Math.round(ratio * durationUs));
  };
  const playheadLeftPx = (() => {
    // use headerW for initial render; actual scrub will use measured header
    if (!durationUs) return headerW;
    return headerW + (playheadUs / durationUs) * railWidth;
  })();
  const playheadLeft = `${playheadLeftPx}px`;

  return <section className="edit-timeline" aria-label={ariaLabel}>
    <div className="edit-timeline-scroll">
      <div
        className="edit-timeline-content"
        style={{ width: `${contentWidth}px` }}
        onPointerDown={(event) => {
          if (!onPlayheadChange) return;
          const target = event.target as HTMLElement;
          // allow clicks on empty rail / ruler to scrub; ignore direct clip button interactions handled by onSelect
          if (target.closest("button.edit-timeline-clip")) return;
          // header clicks should not scrub
          if (target.closest(".edit-lane > header")) return;
          const content = event.currentTarget as HTMLElement;
          scrubFromClientX(event.clientX, content);
          const onMove = (moveEvent: PointerEvent) => scrubFromClientX(moveEvent.clientX, content);
          const onUp = () => {
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onUp);
          };
          window.addEventListener("pointermove", onMove);
          window.addEventListener("pointerup", onUp, { once: true });
        }}
      >
        <div className="edit-ruler"><span>00:00</span><span>{timelineTimeLabel(durationUs / 2)}</span><span>{timelineTimeLabel(durationUs)}</span></div>
        <div
          className="edit-playhead"
          style={{ left: playheadLeft }}
          aria-hidden="true"
          role={onPlayheadChange ? "slider" : undefined}
          aria-valuenow={onPlayheadChange ? Math.round(playheadUs / 1000) : undefined}
          aria-valuemin={0}
          aria-valuemax={onPlayheadChange ? Math.round(durationUs / 1000) : undefined}
          aria-label={onPlayheadChange ? "播放头" : undefined}
          onPointerDown={onPlayheadChange ? (event) => {
            event.preventDefault();
            event.stopPropagation();
            const content = (event.currentTarget.parentElement as HTMLElement);
            const handleMove = (moveEvent: PointerEvent) => scrubFromClientX(moveEvent.clientX, content);
            const handleUp = () => {
              window.removeEventListener("pointermove", handleMove);
              window.removeEventListener("pointerup", handleUp);
            };
            window.addEventListener("pointermove", handleMove);
            window.addEventListener("pointerup", handleUp, { once: true });
            (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
          } : undefined}
        />
        <div
          className="edit-playhead-hit"
          aria-hidden="true"
          style={{ left: playheadLeft }}
          onPointerDown={onPlayheadChange ? (event) => {
            event.preventDefault();
            const content = (event.currentTarget.parentElement as HTMLElement);
            const handleMove = (moveEvent: PointerEvent) => scrubFromClientX(moveEvent.clientX, content);
            const handleUp = () => {
              window.removeEventListener("pointermove", handleMove);
              window.removeEventListener("pointerup", handleUp);
            };
            window.addEventListener("pointermove", handleMove);
            window.addEventListener("pointerup", handleUp, { once: true });
          } : undefined}
        />
        <Lane label="V1" title="视频"><div className="edit-lane-rail" onClick={onPlayheadChange ? (event) => {
          if ((event.target as HTMLElement).closest("button")) return;
          const rail = event.currentTarget as HTMLElement;
          // map click within rail to global timeline
          const content = rail.closest(".edit-timeline-content") as HTMLElement | null;
          if (!content) return;
          scrubFromClientX(event.clientX, content);
        } : undefined}>{clips.map((item) => <button type="button" key={item.shot_id} className={`edit-timeline-clip video${selectedShotId === item.shot_id ? " is-selected" : ""}`} style={position(item.start_us, item.end_us)} onClick={() => onSelect(item.shot_id, item.start_us)} title={`${item.shot_code} · ${timelineTimeLabel(item.end_us - item.start_us)}`}><b>{item.shot_code}</b><small>{TRANSITION_LABELS[item.transition_in] ?? "转场待确认"}</small></button>)}</div></Lane>
        <AudioLane label="A1" title="对白" items={audioLane("DIALOGUE")} disabled={!includeDialogue} position={position} />
        <AudioLane label="A2" title="BGM" items={audioLane("BGM")} disabled={!includeMusic} position={position} />
        <AudioLane label="A3" title="SFX / 环境" items={[...audioLane("SFX"), ...audioLane("ENVIRONMENT"), ...audioLane("LEGACY")]} disabled={!includeMusic} position={position} />
        <Lane label="T1" title="字幕" disabled={!includeSubtitles}><div className="edit-lane-rail">{subtitle ? <span className="edit-timeline-clip subtitle" style={position(0, durationUs)}><b>字幕 v{subtitle.revision_no}</b><small>{subtitle.cue_count} 条</small></span> : <em>暂无字幕版本</em>}</div></Lane>
      </div>
    </div>
  </section>;
}

function Lane({ label, title, disabled = false, children }: { label: string; title: string; disabled?: boolean; children: ReactNode }) {
  return <div className={`edit-lane${disabled ? " is-disabled" : ""}`}><header><strong>{label}</strong><span>{title}</span></header>{children}</div>;
}

function AudioLane({ label, title, items, disabled, position }: { label: string; title: string; items: EditAudioClipV2[]; disabled: boolean; position: (start: number, end: number) => { left: string; width: string } }) {
  return <Lane label={label} title={title} disabled={disabled}><div className="edit-lane-rail">{items.map((item) => <span key={`${item.lane}:${item.id}`} className="edit-timeline-clip audio" style={position(item.start_us, item.end_us)} title={`${item.source_name} · ${item.gain_db} dB`}><b>{item.source_name}</b><small>{item.gain_db} dB</small></span>)}{items.length === 0 ? <em>暂无轨道</em> : null}</div></Lane>;
}
