import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { createShotRevision, markShotProductionReady, resolveProfileCameraPlan, type CameraPlan, type Profile } from "../../generated/api";

const labels: Record<string, string> = { shot_type: "景别", composition: "构图", subject_action: "主体动作", camera_plan: "镜头运动", target_duration_ms: "时长", dialogue: "对白", environment: "环境", continuity: "连续性", creative_intent: "创作意图" };
const requiredNonEmpty = ["shot_type", "composition", "subject_action", "camera_plan", "target_duration_ms", "continuity", "creative_intent"];

const emptyCamera: CameraPlan = { mode: "UNSUPPORTED", shot_type: "", movement: "", prompt_text: "", direction: "FORWARD", intensity: 0.5, curve: "LINEAR", profile_version_id: null };

function cameraFrom(value: unknown): CameraPlan {
  if (!value || typeof value !== "object") return emptyCamera;
  const item = value as Partial<CameraPlan>;
  return { ...emptyCamera, ...item, mode: item.mode ?? "UNSUPPORTED" };
}

export function DirectorShotEditor({ shot, profiles = [], onChanged }: { shot: Record<string, unknown> | undefined; profiles?: Profile[]; onChanged: () => void }) {
  const current = shot?.current_revision && typeof shot.current_revision === "object" ? shot.current_revision as Record<string, unknown> : {};
  const [fields, setFields] = useState<Record<string, string>>({});
  const [camera, setCamera] = useState<CameraPlan>(emptyCamera);
  const [freeze, setFreeze] = useState(true);
  const publishedProfiles = useMemo(() => profiles.filter((item) => item.status === "PUBLISHED"), [profiles]);
  const readiness = shot?.production_readiness && typeof shot.production_readiness === "object" ? shot.production_readiness as { state?: string; blockers?: string[] } : undefined;
  useEffect(() => {
    setFields(Object.fromEntries(Object.keys(labels).filter((key) => key !== "camera_plan").map((key) => [key, current[key] === undefined || current[key] === null ? "" : String(current[key])])))
    const restored = cameraFrom(current.camera_plan);
    setCamera({ ...restored, profile_version_id: restored.profile_version_id ?? publishedProfiles[0]?.version_id ?? null });
  }, [shot?.id, shot?.current_revision_id, publishedProfiles]);
  const cameraResolved = Boolean(camera.profile_version_id && camera.shot_type && camera.movement && camera.direction && camera.curve);
  const cameraRunnable = cameraResolved && camera.mode !== "UNSUPPORTED";
  const missing = useMemo(() => Object.keys(labels).filter((key) => key === "camera_plan"
    ? !cameraRunnable
    : requiredNonEmpty.includes(key) ? !fields[key]?.trim() || (key === "target_duration_ms" && Number(fields[key]) <= 0) : fields[key] === undefined), [cameraRunnable, fields]);
  const update = (key: string, value: string) => setFields((existing) => ({ ...existing, [key]: value }));
  const updateCamera = (change: Partial<CameraPlan>) => setCamera((existing) => ({ ...existing, ...change, mode: "UNSUPPORTED", prompt_text: change.prompt_text ?? existing.prompt_text }));
  const resolve = useMutation({
    mutationFn: () => resolveProfileCameraPlan(String(camera.profile_version_id), { shot_type: fields.shot_type, movement: camera.movement, direction: camera.direction, intensity: camera.intensity, curve: camera.curve, prompt_text: camera.prompt_text }),
    onSuccess: ({ resolution }) => setCamera(resolution.camera_plan),
  });
  const save = useMutation({ mutationFn: () => createShotRevision(String(shot?.id), { ...fields, target_duration_ms: Number(fields.target_duration_ms), ...(cameraResolved ? { camera_plan: camera } : {}) }, freeze), onSuccess: onChanged });
  const ready = useMutation({ mutationFn: () => markShotProductionReady(String(shot?.id)), onSuccess: onChanged });
  if (!shot) return <section className="panel director-editor"><p className="empty-state">选择镜头后编辑导演分镜。</p></section>;
  return <section className="panel director-editor" aria-labelledby="director-editor-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-WRT-003/005 · 不可变 revision</p><h3 id="director-editor-title">导演分镜字段</h3></div><span className="status-pill">{String(shot.code)} · {readiness?.state ?? String(shot.status)}</span></div>
    <div className="director-grid">
      <label>{labels.shot_type}<select value={fields.shot_type ?? ""} onChange={(event) => update("shot_type", event.target.value)}><option value="">请选择</option>{["ESTABLISHING", "WIDE", "MEDIUM", "CLOSEUP", "INSERT", "POV", "OTHER"].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>{labels.composition}<input value={fields.composition ?? ""} onChange={(event) => update("composition", event.target.value)} /></label>
      <label>{labels.subject_action}<input value={fields.subject_action ?? ""} onChange={(event) => update("subject_action", event.target.value)} /></label>
      <fieldset className="camera-plan-editor"><legend>{labels.camera_plan} · CameraPlan</legend>
        <label>已发布 Profile<select value={camera.profile_version_id ?? ""} onChange={(event) => updateCamera({ profile_version_id: event.target.value || null })}><option value="">请选择</option>{publishedProfiles.map((profile) => <option value={profile.version_id} key={profile.version_id}>{profile.title} · {profile.capability}</option>)}</select></label>
        <label>运动<select value={camera.movement} onChange={(event) => updateCamera({ movement: event.target.value })}><option value="">请选择</option>{["STATIC", "PUSH_IN", "PULL_OUT", "PAN", "TILT", "TRUCK", "PEDESTAL", "ORBIT", "ROLL"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>方向<input value={camera.direction} onChange={(event) => updateCamera({ direction: event.target.value })} /></label>
        <label>强度（0—1）<input type="number" min="0" max="1" step="0.1" value={camera.intensity} onChange={(event) => updateCamera({ intensity: Number(event.target.value) })} /></label>
        <label>运动曲线<select value={camera.curve} onChange={(event) => updateCamera({ curve: event.target.value })}>{["LINEAR", "EASE_IN", "EASE_OUT", "EASE_IN_OUT"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>Prompt 降级文本<textarea value={camera.prompt_text} onChange={(event) => updateCamera({ prompt_text: event.target.value })} placeholder="只有 Profile 明确声明 PROMPT_FALLBACK 时使用" /></label>
        <button type="button" className="secondary" disabled={resolve.isPending || !camera.profile_version_id || !fields.shot_type || !camera.movement} onClick={() => resolve.mutate()}>{resolve.isPending ? "裁决中…" : "按 Profile 裁决运镜能力"}</button>
        <p className={`camera-resolution ${cameraRunnable ? "ready" : "blocked"}`}><strong>{camera.mode}</strong>{camera.mode === "NATIVE" ? "原生参数映射，可进入生产" : camera.mode === "PROMPT_FALLBACK" ? "显式 Prompt 降级，可进入生产" : "当前未裁决或 Profile 不支持，禁止进入生产"}</p>
        {resolve.error && <p className="inline-error" role="alert">{resolve.error.message}</p>}
      </fieldset>
      <label>{labels.target_duration_ms}<input type="number" min="1" value={fields.target_duration_ms ?? ""} onChange={(event) => update("target_duration_ms", event.target.value)} /></label>
      <label>{labels.dialogue}<textarea value={fields.dialogue ?? ""} onChange={(event) => update("dialogue", event.target.value)} /></label>
      <label>{labels.environment}<textarea value={fields.environment ?? ""} onChange={(event) => update("environment", event.target.value)} /></label>
      <label>{labels.continuity}<textarea value={fields.continuity ?? ""} onChange={(event) => update("continuity", event.target.value)} /></label>
      <label>{labels.creative_intent}<textarea value={fields.creative_intent ?? ""} onChange={(event) => update("creative_intent", event.target.value)} /></label>
    </div>
    <div className="director-actions"><label className="checkbox-row"><input type="checkbox" checked={freeze} onChange={(event) => setFreeze(event.target.checked)} />保存时冻结 revision</label><button className="secondary" disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending ? "保存中…" : "保存新 revision"}</button><button className="primary-action" disabled={ready.isPending || missing.length > 0 || shot.status !== "DIRECTED"} onClick={() => ready.mutate()}>{ready.isPending ? "校验中…" : "标记 Production Ready"}</button></div>
    <p className={missing.length ? "review-guidance" : "review-success"} role="status">{missing.length ? `还缺 ${missing.length} 项：${missing.map((key) => labels[key]).join("、")}` : shot.status === "DIRECTED" ? "九项字段完整，结构化运镜已通过 Profile 裁决，可显式标记 Production Ready。" : `九项字段完整；当前状态 ${readiness?.state ?? String(shot.status)}。`}</p>
    {readiness?.blockers && readiness.blockers.length > 0 && <p className="muted">服务端阻塞：{readiness.blockers.join("、")}</p>}
    {(save.error || ready.error) && <p className="inline-error" role="alert">{String(save.error ?? ready.error)}</p>}
  </section>;
}
