import { useEffect, useState } from "react";
import { createMotionControl, listMotionControls, type MotionControl, type MotionControlRequest } from "../../generated/api";

type Props = {
  sourceMediaVersionId: string;
  profileVersionId: string;
};

/** Local motion brush/inpaint/outpaint control surface. Source media is read-only. */
export function MotionControlPanel({ sourceMediaVersionId, profileVersionId }: Props) {
  const [kind, setKind] = useState<MotionControlRequest["control_kind"]>("MOTION_MASK");
  const [operation, setOperation] = useState<MotionControlRequest["operation"]>("MOTION_BRUSH");
  const [subjectRole, setSubjectRole] = useState("subject");
  const [maskMediaVersionId, setMaskMediaVersionId] = useState("");
  const [keyframeMediaVersionId, setKeyframeMediaVersionId] = useState("");
  const [vectorJson, setVectorJson] = useState('[{"x":0.2,"y":0.3,"pressure":1}]');
  const [controls, setControls] = useState<MotionControl[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () => {
    if (!sourceMediaVersionId || typeof listMotionControls !== "function") return;
    void listMotionControls(sourceMediaVersionId).then((response) => setControls(response.items));
  };
  useEffect(refresh, [sourceMediaVersionId]);

  const submit = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const payload: MotionControlRequest = {
        control_kind: kind,
        operation,
        subject_role: subjectRole.trim(),
        profile_version_id: profileVersionId,
        mask_media_version_id: maskMediaVersionId.trim() || null,
        keyframe_media_version_id: keyframeMediaVersionId.trim() || null,
        vector_path: JSON.parse(vectorJson) as MotionControlRequest["vector_path"],
      };
      const response = await createMotionControl(sourceMediaVersionId, payload);
      setMessage(response.motion_control.duplicate ? "已复用同一不可变控制版本" : "已保存不可变控制媒体；源媒体未修改");
      refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "运动控制保存失败");
    } finally {
      setBusy(false);
    }
  };

  return <section className="panel motion-control-panel" aria-labelledby="motion-control-title">
    <div className="section-title"><span id="motion-control-title">运动区域与局部编辑</span><small>不可变 mask / vector / keyframe · 不覆盖源媒体</small></div>
    <div className="field-grid">
      <label>控制类型<select value={kind} onChange={(event) => setKind(event.target.value as MotionControlRequest["control_kind"])}><option value="MOTION_MASK">MOTION_MASK</option><option value="VECTOR">VECTOR</option><option value="KEYFRAME">KEYFRAME</option></select></label>
      <label>能力槽<select value={operation} onChange={(event) => setOperation(event.target.value as MotionControlRequest["operation"])}><option value="MOTION_BRUSH">MOTION_BRUSH</option><option value="INPAINT">INPAINT</option><option value="OUTPAINT">OUTPAINT</option></select></label>
      <label>主体角色<input value={subjectRole} onChange={(event) => setSubjectRole(event.target.value)} /></label>
      {kind === "MOTION_MASK" && <label>现有 mask MediaVersion（可选）<input value={maskMediaVersionId} onChange={(event) => setMaskMediaVersionId(event.target.value)} placeholder="仅引用本机已注册版本" /></label>}
      {kind === "KEYFRAME" && <label>keyframe MediaVersion<input value={keyframeMediaVersionId} onChange={(event) => setKeyframeMediaVersionId(event.target.value)} placeholder="仅引用本机已注册 IMAGE 版本" required /></label>}
      <label>运动笔刷 vector JSON<textarea value={vectorJson} onChange={(event) => setVectorJson(event.target.value)} spellCheck={false} /></label>
    </div>
    <button type="button" onClick={() => void submit()} disabled={busy || !sourceMediaVersionId || !profileVersionId || !subjectRole.trim() || (kind === "KEYFRAME" && !keyframeMediaVersionId.trim())}>{busy ? "保存中…" : "保存不可变控制"}</button>
    {message && <p role="status" className="muted">{message}</p>}
    <ul aria-label="已保存运动控制">{controls.map((control) => <li key={control.id}>{control.control_kind} · {control.operation} · {control.subject_role} · {control.control_media_version_id.slice(0, 12)}</li>)}</ul>
  </section>;
}
