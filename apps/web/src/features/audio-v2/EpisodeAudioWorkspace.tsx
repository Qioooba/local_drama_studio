import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import { Dialog } from "../../components/ui";
import {
  createEpisodeAudioTrackV2,
  getEpisodeAudioWorkspaceV2,
  removeEpisodeAudioTrackV2,
  updateEpisodeAudioTrackV2,
  type AudioTrackV2,
} from "../../generated/api";
import { uploadProjectMediaFile } from "../media-picker/mediaPickerClient";
import { ProjectLocalResourceSelect } from "../shared/ProjectLocalResourceSelect";

type AudioFocus = "dialogue-reference" | "music-sfx" | "gaps";

const FOCUS_ITEMS: Array<{ id: AudioFocus; label: string }> = [
  { id: "dialogue-reference", label: "对白引用" },
  { id: "music-sfx", label: "音乐与音效" },
  { id: "gaps", label: "缺口与证据" },
];
const TRACK_LABELS: Record<string, string> = { BGM: "背景音乐", SFX: "音效" };
const GAP_LABELS: Record<string, string> = {
  DIALOGUE_TTS_MISSING: "缺少已采用的对白语音",
  DIALOGUE_TTS_STALE: "对白语音已过期",
  AUDIO_LICENSE_EVIDENCE_MISSING: "缺少声音授权证据",
};

function commandKey(prefix: string) {
  return `${prefix}:${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`}`;
}

function seconds(us: number) {
  return (us / 1_000_000).toFixed(2);
}

function Summary({ label, value, detail, tone = "neutral" }: { label: string; value: number | string; detail: string; tone?: string }) {
  return <article className={`post-review-summary tone-${tone}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

export function EpisodeAudioWorkspace({ projectId, episodeId }: { projectId: string; episodeId: string }) {
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const requestedFocus = params.get("focus") as AudioFocus | null;
  const focus: AudioFocus = FOCUS_ITEMS.some((item) => item.id === requestedFocus) ? requestedFocus! : "dialogue-reference";
  const workspace = useQuery({ queryKey: ["post-audio-v2", episodeId], queryFn: () => getEpisodeAudioWorkspaceV2(episodeId) });
  const data = workspace.data?.workspace;
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);
  const selectedTrack = data?.tracks.find((track) => track.id === selectedTrackId) ?? null;
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["post-audio-v2", episodeId] });

  useEffect(() => {
    if (!data) return;
    if (selectedTrackId && data.tracks.some((track) => track.id === selectedTrackId)) return;
    setSelectedTrackId(data.tracks[0]?.id ?? null);
  }, [data, selectedTrackId]);

  const selectFocus = (next: AudioFocus) => setParams((current) => {
    const result = new URLSearchParams(current);
    if (next === "dialogue-reference") result.delete("focus");
    else result.set("focus", next);
    return result;
  }, { replace: true });

  if (workspace.isPending) return <p className="empty-state" role="status">正在读取本集声音工作区…</p>;
  if (workspace.isError || !data) return <p className="inline-error" role="alert">声音工作区读取失败：{String(workspace.error)} <button type="button" className="secondary" onClick={() => void workspace.refetch()}>重试</button></p>;

  return <div className="episode-audio-workspace-v2">
    <section className="post-review-summary-grid" aria-label="声音摘要">
      <Summary label="对白" value={`${data.summary.adopted_dialogue_count ?? 0}/${data.summary.dialogue_count ?? 0}`} detail="已准备工作语音" tone={(data.summary.adopted_dialogue_count ?? 0) === (data.summary.dialogue_count ?? 0) ? "ok" : "warning"} />
      <Summary label="背景音乐" value={data.summary.bgm_count ?? 0} detail="当前混音草稿" />
      <Summary label="音效" value={data.summary.sfx_count ?? 0} detail="含环境与动作声音" />
      <Summary label="待处理" value={data.summary.gap_count ?? 0} detail={`混音 revision ${data.mix_revision}`} tone={(data.summary.gap_count ?? 0) ? "warning" : "ok"} />
    </section>
    <nav className="post-audio-focus" aria-label="本集声音任务">
      {FOCUS_ITEMS.map((item) => <button key={item.id} type="button" className={focus === item.id ? "is-active" : ""} aria-pressed={focus === item.id} onClick={() => selectFocus(item.id)}>{item.label}</button>)}
    </nav>

    {focus === "dialogue-reference" ? <DialogueReferences items={data.dialogue_references} projectId={projectId} episodeId={episodeId} /> : null}
    {focus === "music-sfx" ? <section className="post-audio-layout">
      <div className="post-audio-track-list">
        <header><div><p className="eyebrow">混音素材</p><h3>音乐与音效</h3></div><span>revision {data.mix_revision}</span></header>
        {data.tracks.length === 0 ? <p className="empty-state">尚未添加背景音乐或音效。</p> : data.tracks.map((track) => <button key={track.id} type="button" className={`post-audio-track${track.id === selectedTrackId ? " is-selected" : ""}`} aria-pressed={track.id === selectedTrackId} onClick={() => setSelectedTrackId(track.id)}><strong>{TRACK_LABELS[track.track_kind] ?? track.track_kind}</strong><span>{track.source_name}</span><small>{seconds(track.start_us)}–{seconds(track.end_us)} 秒 · {track.gain_db.toFixed(1)} dB</small></button>)}
        <AddTrackForm projectId={projectId} episodeId={episodeId} mixRevision={data.mix_revision} onSaved={refresh} />
      </div>
      <div className="post-audio-inspector">
        {selectedTrack ? <TrackInspector track={selectedTrack} mixRevision={data.mix_revision} onSaved={refresh} /> : <p className="empty-state">添加或选择一条音轨，试听并调整混音。</p>}
      </div>
    </section> : null}
    {focus === "gaps" ? <section className="post-audio-gaps"><header><div><p className="eyebrow">进入编辑前</p><h3>声音缺口与证据</h3></div><span>{data.gaps.length} 项</span></header>{data.gaps.length === 0 ? <p className="empty-state">当前没有已知声音缺口。</p> : data.gaps.map((gap) => <article key={`${gap.code}:${gap.subject_id ?? gap.message}`}><strong>{gap.message}</strong><small>{GAP_LABELS[gap.code] ?? "需要处理的声音证据"}</small>{gap.owner_route === "SHOT_STUDIO" ? <Link className="secondary" to={routes.shotStudio(projectId, episodeId)}>返回镜头对白</Link> : gap.owner_route === "REVIEW" ? <Link className="secondary" to={routes.postReview(projectId, episodeId)}>前往审核</Link> : null}</article>)}</section> : null}
  </div>;
}

function DialogueReferences({ items, projectId, episodeId }: { items: Awaited<ReturnType<typeof getEpisodeAudioWorkspaceV2>>["workspace"]["dialogue_references"]; projectId: string; episodeId: string }) {
  return <section className="post-audio-dialogue"><header><div><p className="eyebrow">只读引用</p><h3>Shot Studio 已准备的对白声音</h3></div><Link className="secondary" to={routes.shotStudio(projectId, episodeId)}>编辑对白与 TTS</Link></header>{items.length === 0 ? <p className="empty-state">本集还没有对白。</p> : items.map((line) => <article key={line.line_id}><div><strong>{line.line_code} · {line.speaker}</strong><p>{line.text}</p><small>{line.shot_code ?? "未绑定镜头"} · 文本 v{line.text_revision_no}{line.selection_stale ? " · 工作语音已过期" : ""}</small></div>{line.selected_media_version_id ? <audio controls preload="none" src={`/api/v1/media-versions/${encodeURIComponent(line.selected_media_version_id)}/content`} /> : <span className="status-pill warning">缺少工作语音</span>}</article>)}</section>;
}

function AddTrackForm({ projectId, episodeId, mixRevision, onSaved }: { projectId: string; episodeId: string; mixRevision: number; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [evidence, setEvidence] = useState("");
  const [kind, setKind] = useState<"BGM" | "SFX">("BGM");
  const [endSeconds, setEndSeconds] = useState("30");
  const create = useMutation({
    mutationFn: async () => {
      if (!file || !evidence) throw new Error("请选择音频文件和项目内授权证据");
      const mediaVersionId = await uploadProjectMediaFile(projectId, file);
      return createEpisodeAudioTrackV2(episodeId, {
        media_version_id: mediaVersionId, track_kind: kind, start_us: 0,
        end_us: Math.round(Number(endSeconds) * 1_000_000), gain_db: 0,
        license_status: "USER_OWNED", license_evidence_path_rel: evidence,
        loop_enabled: true, fade_in_us: 500_000, fade_out_us: 500_000,
        expected_mix_revision: mixRevision, idempotency_key: commandKey("audio-create"),
      });
    },
    onSuccess: () => { setOpen(false); setFile(null); onSaved(); },
  });
  return <div className="post-audio-add"><button type="button" className="secondary" aria-expanded={open} onClick={() => setOpen((value) => !value)}>{open ? "收起添加" : "添加本地音乐或音效"}</button>{open ? <form onSubmit={(event) => { event.preventDefault(); create.mutate(); }}><label>用途<select value={kind} onChange={(event) => setKind(event.target.value as "BGM" | "SFX")}><option value="BGM">背景音乐</option><option value="SFX">音效</option></select></label><label>本地音频<input type="file" accept="audio/*" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label><ProjectLocalResourceSelect projectId={projectId} kind="LICENSE_EVIDENCE" value={evidence} onChange={setEvidence} label="项目内授权证据" required emptyLabel="请选择授权证据" /><label>覆盖时长（秒）<input type="number" min="1" step="0.1" value={endSeconds} onChange={(event) => setEndSeconds(event.target.value)} /></label><p className="muted">导入后创建不可变音频源，并在当前混音 revision 上新增轨道；默认循环并使用 0.5 秒淡入淡出。</p><button type="submit" className="primary-action" disabled={!file || !evidence || create.isPending}>{create.isPending ? "正在校验并添加…" : "校验并添加"}</button>{create.error ? <p className="inline-error" role="alert">添加失败：{String(create.error)}。已导入媒体会保留为未绑定素材。</p> : null}</form> : null}</div>;
}

function TrackInspector({ track, mixRevision, onSaved }: { track: AudioTrackV2; mixRevision: number; onSaved: () => void }) {
  const [start, setStart] = useState(seconds(track.start_us));
  const [end, setEnd] = useState(seconds(track.end_us));
  const [gain, setGain] = useState(String(track.gain_db));
  const [fadeIn, setFadeIn] = useState(String(track.fade_in_us / 1000));
  const [fadeOut, setFadeOut] = useState(String(track.fade_out_us / 1000));
  const [loop, setLoop] = useState(track.loop_enabled);
  const [removeOpen, setRemoveOpen] = useState(false);
  useEffect(() => { setStart(seconds(track.start_us)); setEnd(seconds(track.end_us)); setGain(String(track.gain_db)); setFadeIn(String(track.fade_in_us / 1000)); setFadeOut(String(track.fade_out_us / 1000)); setLoop(track.loop_enabled); }, [track]);
  const values = useMemo(() => ({ start_us: Math.round(Number(start) * 1_000_000), end_us: Math.round(Number(end) * 1_000_000), gain_db: Number(gain), fade_in_us: Math.round(Number(fadeIn) * 1000), fade_out_us: Math.round(Number(fadeOut) * 1000) }), [end, fadeIn, fadeOut, gain, start]);
  const update = useMutation({ mutationFn: () => updateEpisodeAudioTrackV2(track.id, { ...values, loop_enabled: loop, expected_revision: track.revision, expected_mix_revision: mixRevision, idempotency_key: commandKey("audio-update") }), onSuccess: onSaved });
  const remove = useMutation({ mutationFn: () => removeEpisodeAudioTrackV2(track.id, { expected_revision: track.revision, expected_mix_revision: mixRevision, reason: "从当前混音草稿移除", idempotency_key: commandKey("audio-remove") }), onSuccess: () => { setRemoveOpen(false); onSaved(); } });
  return <><header><div><p className="eyebrow">混音 Inspector</p><h3>{TRACK_LABELS[track.track_kind] ?? track.track_kind}</h3></div><span className="status-pill">{track.authorization_status === "VERIFIED_EVIDENCE" ? "授权已验证" : "证据不完整"}</span></header><audio controls preload="none" loop={loop} src={`/api/v1/media-versions/${encodeURIComponent(track.media_version_id)}/content`} /><p>{track.source_name}</p><div className="post-audio-mix-form"><label>开始（秒）<input type="number" min="0" step="0.01" value={start} onChange={(event) => setStart(event.target.value)} /></label><label>结束（秒）<input type="number" min="0.01" step="0.01" value={end} onChange={(event) => setEnd(event.target.value)} /></label><label>音量（dB）<input type="number" min="-60" max="24" step="0.1" value={gain} onChange={(event) => setGain(event.target.value)} /></label><label>淡入（ms）<input type="number" min="0" step="10" value={fadeIn} onChange={(event) => setFadeIn(event.target.value)} /></label><label>淡出（ms）<input type="number" min="0" step="10" value={fadeOut} onChange={(event) => setFadeOut(event.target.value)} /></label><label className="checkbox-label"><input type="checkbox" checked={loop} onChange={(event) => setLoop(event.target.checked)} />循环源音频</label></div>{update.error ? <p className="inline-error" role="alert">保存失败：{String(update.error)}</p> : null}<div className="post-review-actions"><button type="button" className="secondary" onClick={() => setRemoveOpen(true)}>移除轨道</button><button type="button" className="primary-action" disabled={update.isPending || Object.values(values).some((value) => !Number.isFinite(value))} onClick={() => update.mutate()}>{update.isPending ? "正在保存…" : "保存混音调整"}</button></div><Dialog open={removeOpen} title="从当前混音移除这条轨道？" onClose={() => !remove.isPending && setRemoveOpen(false)} footer={<><button type="button" onClick={() => setRemoveOpen(false)}>取消</button><button type="button" className="secondary" disabled={remove.isPending} onClick={() => remove.mutate()}>{remove.isPending ? "正在移除…" : "确认移除"}</button></>}>原始音频、授权证据和旧冻结时间线不会被删除；只更新当前混音草稿。</Dialog></>;
}
