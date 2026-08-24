import type { CSSProperties } from "react";
import type { AudioBinding, TimelineStatus } from "../../generated/api";
import type { TimelineShotDraft } from "./types";

type EnrichedAudioBinding = AudioBinding & { duration_ms?: number | null; source_name?: string | null; purpose?: string; waveform_ready?: boolean };

function audioLane(item: EnrichedAudioBinding): "A1" | "A2" | "A3" {
  if (item.track_type === "DIALOGUE") return "A1";
  const purpose = String(item.purpose ?? "").toUpperCase();
  if (item.track_type === "ENVIRONMENT" || purpose.includes("ENVIRONMENT") || purpose.includes("AMBIENCE")) return "A3";
  return "A2";
}

const laneTitles = { A1: "对白", A2: "BGM / SFX", A3: "环境音" };

export function TimelineTracks({ shots, audio, subtitles, includeAudio, includeSubtitles }: { shots: TimelineShotDraft[]; audio: AudioBinding[]; subtitles: TimelineStatus["subtitles"]; includeAudio: boolean; includeSubtitles: boolean }) {
  const enriched = audio as EnrichedAudioBinding[];
  const videoDuration = shots.reduce((sum, shot) => sum + shot.durationUs, 0);
  const audioEnd = enriched.reduce((max, item) => Math.max(max, item.end_us), 0);
  const duration = Math.max(1, videoDuration, audioEnd);
  let cursor = 0;
  const videoClips = shots.map((shot) => { const start = cursor; cursor += shot.durationUs; return { shot, start, end: cursor }; });
  const position = (start: number, end: number) => ({ left: `${(start / duration) * 100}%`, width: `${Math.max(0.8, ((end - start) / duration) * 100)}%` });

  return <section className="timeline-multitrack" aria-label="多轨时间线事实" style={{ "--timeline-content-width": `${Math.max(1, shots.length) * 80}px` } as CSSProperties}>
    <div className="timeline-time-ruler"><span>00:00</span><span>{(duration / 2_000_000).toFixed(1)}s</span><span>{(duration / 1_000_000).toFixed(1)}s</span></div>
    <div className="timeline-lane"><header><strong>V1</strong><span>视频</span></header><div className="timeline-lane-rail">{videoClips.map(({ shot, start, end }) => <span className={`timeline-fact-clip video${shot.continuityStatus === "STALE" ? " stale" : ""}`} style={position(start, end)} key={shot.shotId} title={`${shot.code} · ${(shot.durationUs / 1_000_000).toFixed(2)}s`}>{shot.code}</span>)}</div></div>
    {(["A1", "A2", "A3"] as const).map((lane) => <div className={`timeline-lane${includeAudio ? "" : " disabled"}`} key={lane}><header><strong>{lane}</strong><span>{laneTitles[lane]}</span></header><div className="timeline-lane-rail">{enriched.filter((item) => audioLane(item) === lane).map((item) => <span className="timeline-fact-clip audio" style={position(item.start_us, item.end_us)} key={item.id} title={`${item.source_name || item.track_type} · ${item.gain_db} dB`}>
      {item.waveform_ready ? <img src={`/api/v1/media-versions/${encodeURIComponent(item.media_version_id)}/waveform`} alt="已缓存的真实音频波形" loading="lazy" decoding="async" /> : <i aria-hidden="true" />}
      <b>{item.source_name || item.track_type}</b><small>{((item.end_us - item.start_us) / 1_000_000).toFixed(1)}s · {item.gain_db} dB</small>
    </span>)}{enriched.filter((item) => audioLane(item) === lane).length === 0 && <em>没有持久化 binding</em>}</div></div>)}
    <div className={`timeline-lane subtitle${includeSubtitles ? "" : " disabled"}`}><header><strong>SUB</strong><span>Subtitle</span></header><div className="timeline-lane-rail">{subtitles.latest ? <span className="timeline-fact-clip subtitle" style={position(0, videoDuration || duration)}><b>字幕 revision {String(subtitles.latest.revision_no ?? "—")}</b><small>{String(subtitles.latest.cue_count ?? 0)} cues · {String(subtitles.latest.authority_status ?? "authority unknown")}</small></span> : <em>没有字幕 revision</em>}</div></div>
    <footer className="timeline-track-policy"><strong>Ducking / 混音策略</strong><span>当前后端只持久化真实 binding 的 gain、fade、loop 与范围；未持久化 ducking automation，因此这里不提供虚假开关。对白优先混音请在声音页通过现有 binding policy 调整。</span></footer>
  </section>;
}
