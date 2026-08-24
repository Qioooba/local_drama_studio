import { useEffect, useState } from "react";
import { createMotionControl, listMotionControls, type MotionControl, type MotionControlPoint, type MotionControlRequest } from "../../generated/api";
import { ProjectMediaVersionSelect } from "../media-picker/ProjectMediaVersionSelect";
import { SUBJECT_ROLE_OPTIONS } from "../shared/formOptions";
import { MotionCanvas } from "./MotionCanvas";

type Props = {
  sourceMediaVersionId: string;
  profileVersionId: string;
  projectId?: string;
};

/** Local motion brush/inpaint/outpaint control surface. Source media is read-only. */
export function MotionControlPanel({ sourceMediaVersionId, profileVersionId, projectId }: Props) {
  const [kind, setKind] = useState<MotionControlRequest["control_kind"]>("MOTION_MASK");
  const [operation, setOperation] = useState<MotionControlRequest["operation"]>("MOTION_BRUSH");
  const [subjectRole, setSubjectRole] = useState("subject");
  const [maskMediaVersionId, setMaskMediaVersionId] = useState("");
  const [keyframeMediaVersionId, setKeyframeMediaVersionId] = useState("");
  const [vectorPath, setVectorPath] = useState<MotionControlPoint[]>([]);
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
        vector_path: vectorPath,
        coordinate_space: "NORMALIZED",
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
      <label htmlFor="motion-control-kind">控制类型<select id="motion-control-kind" aria-label="控制类型" value={kind} onChange={(event) => setKind(event.target.value as MotionControlRequest["control_kind"])}><option value="MOTION_MASK">运动遮罩</option><option value="VECTOR">轨迹箭头</option><option value="KEYFRAME">关键帧控制</option></select></label>
      <label htmlFor="motion-control-operation">编辑操作<select id="motion-control-operation" aria-label="编辑操作" value={operation} onChange={(event) => setOperation(event.target.value as MotionControlRequest["operation"])}><option value="MOTION_BRUSH">运动笔刷</option><option value="INPAINT">局部重绘</option><option value="OUTPAINT">画面扩展</option></select></label>
      <label htmlFor="motion-control-subject">主体区域<select id="motion-control-subject" aria-label="主体区域" value={subjectRole} onChange={(event) => setSubjectRole(event.target.value)}>{SUBJECT_ROLE_OPTIONS.map((value) => <option key={value} value={value}>{({ subject: "主体", face: "面部", body: "身体", foreground: "前景", background: "背景" } as Record<string, string>)[value] ?? value}</option>)}</select></label>
      {kind === "MOTION_MASK" && (projectId ? <ProjectMediaVersionSelect projectId={projectId} label="现有遮罩素材（可选）" mediaKinds={["IMAGE"]} value={maskMediaVersionId} onChange={setMaskMediaVersionId} /> : <label>现有遮罩素材（可选）<select disabled><option>请先选择项目</option></select></label>)}
      {kind === "KEYFRAME" && (projectId ? <ProjectMediaVersionSelect projectId={projectId} label="关键帧素材" mediaKinds={["IMAGE"]} value={keyframeMediaVersionId} onChange={setKeyframeMediaVersionId} required /> : <label>关键帧素材<select disabled><option>请先选择项目</option></select></label>)}
    </div>
    {kind === "VECTOR" && <MotionCanvas mediaVersionId={sourceMediaVersionId} value={vectorPath} onChange={setVectorPath} disabled={busy} />}
    <button type="button" onClick={() => void submit()} disabled={busy || !sourceMediaVersionId || !profileVersionId || !subjectRole.trim() || (kind === "VECTOR" && vectorPath.length < 2) || (kind === "KEYFRAME" && !keyframeMediaVersionId.trim())}>{busy ? "保存中…" : "保存不可变控制"}</button>
    {message && <p role="status" className="muted">{message}</p>}
    <ul aria-label="已保存运动控制">{controls.map((control) => <li key={control.id}>{({ MOTION_MASK: "运动遮罩", VECTOR: "轨迹箭头", KEYFRAME: "关键帧控制" } as Record<string, string>)[control.control_kind] ?? control.control_kind} · {({ MOTION_BRUSH: "运动笔刷", INPAINT: "局部重绘", OUTPAINT: "画面扩展" } as Record<string, string>)[control.operation] ?? control.operation} · {control.subject_role}</li>)}</ul>
  </section>;
}
