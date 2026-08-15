import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { createShotRevision, markShotProductionReady } from "../../generated/api";

const labels: Record<string, string> = { shot_type: "景别", composition: "构图", subject_action: "主体动作", camera_plan: "镜头运动", target_duration_ms: "时长", dialogue: "对白", environment: "环境", continuity: "连续性", creative_intent: "创作意图" };
const requiredNonEmpty = ["shot_type", "composition", "subject_action", "camera_plan", "target_duration_ms", "continuity", "creative_intent"];

export function DirectorShotEditor({ shot, onChanged }: { shot: Record<string, unknown> | undefined; onChanged: () => void }) {
  const current = shot?.current_revision && typeof shot.current_revision === "object" ? shot.current_revision as Record<string, unknown> : {};
  const [fields, setFields] = useState<Record<string, string>>({});
  const [freeze, setFreeze] = useState(true);
  const readiness = shot?.production_readiness && typeof shot.production_readiness === "object" ? shot.production_readiness as { state?: string; blockers?: string[] } : undefined;
  useEffect(() => {
    setFields(Object.fromEntries(Object.keys(labels).map((key) => [key, current[key] === undefined || current[key] === null ? "" : String(current[key])])))
  }, [shot?.id, shot?.current_revision_id]);
  const missing = useMemo(() => Object.keys(labels).filter((key) => requiredNonEmpty.includes(key) ? !fields[key]?.trim() || (key === "target_duration_ms" && Number(fields[key]) <= 0) : fields[key] === undefined), [fields]);
  const update = (key: string, value: string) => setFields((existing) => ({ ...existing, [key]: value }));
  const save = useMutation({ mutationFn: () => createShotRevision(String(shot?.id), { ...fields, target_duration_ms: Number(fields.target_duration_ms) }, freeze), onSuccess: onChanged });
  const ready = useMutation({ mutationFn: () => markShotProductionReady(String(shot?.id)), onSuccess: onChanged });
  if (!shot) return <section className="panel director-editor"><p className="empty-state">选择镜头后编辑导演分镜。</p></section>;
  return <section className="panel director-editor" aria-labelledby="director-editor-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-WRT-003/005 · 不可变 revision</p><h3 id="director-editor-title">导演分镜字段</h3></div><span className="status-pill">{String(shot.code)} · {readiness?.state ?? String(shot.status)}</span></div>
    <div className="director-grid">
      <label>{labels.shot_type}<select value={fields.shot_type ?? ""} onChange={(event) => update("shot_type", event.target.value)}><option value="">请选择</option>{["ESTABLISHING", "WIDE", "MEDIUM", "CLOSEUP", "INSERT", "POV", "OTHER"].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>{labels.composition}<input value={fields.composition ?? ""} onChange={(event) => update("composition", event.target.value)} /></label>
      <label>{labels.subject_action}<input value={fields.subject_action ?? ""} onChange={(event) => update("subject_action", event.target.value)} /></label>
      <label>{labels.camera_plan}<input value={fields.camera_plan ?? ""} onChange={(event) => update("camera_plan", event.target.value)} /></label>
      <label>{labels.target_duration_ms}<input type="number" min="1" value={fields.target_duration_ms ?? ""} onChange={(event) => update("target_duration_ms", event.target.value)} /></label>
      <label>{labels.dialogue}<textarea value={fields.dialogue ?? ""} onChange={(event) => update("dialogue", event.target.value)} /></label>
      <label>{labels.environment}<textarea value={fields.environment ?? ""} onChange={(event) => update("environment", event.target.value)} /></label>
      <label>{labels.continuity}<textarea value={fields.continuity ?? ""} onChange={(event) => update("continuity", event.target.value)} /></label>
      <label>{labels.creative_intent}<textarea value={fields.creative_intent ?? ""} onChange={(event) => update("creative_intent", event.target.value)} /></label>
    </div>
    <div className="director-actions"><label className="checkbox-row"><input type="checkbox" checked={freeze} onChange={(event) => setFreeze(event.target.checked)} />保存时冻结 revision</label><button className="secondary" disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending ? "保存中…" : "保存新 revision"}</button><button className="primary-action" disabled={ready.isPending || missing.length > 0 || shot.status !== "DIRECTED"} onClick={() => ready.mutate()}>{ready.isPending ? "校验中…" : "标记 Production Ready"}</button></div>
    <p className={missing.length ? "review-guidance" : "review-success"} role="status">{missing.length ? `还缺 ${missing.length} 项：${missing.map((key) => labels[key]).join("、")}` : shot.status === "DIRECTED" ? "九项字段完整，可显式标记 Production Ready。" : `九项字段完整；当前状态 ${readiness?.state ?? String(shot.status)}。`}</p>
    {readiness?.blockers && readiness.blockers.length > 0 && <p className="muted">服务端阻塞：{readiness.blockers.join("、")}</p>}
    {(save.error || ready.error) && <p className="inline-error" role="alert">{String(save.error ?? ready.error)}</p>}
  </section>;
}
