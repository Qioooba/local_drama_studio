import { useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { getJob, submitMediaDerivative } from "../../generated/api";
import { ProjectMediaVersionSelect } from "../media-picker/ProjectMediaVersionSelect";

export function EvidenceImageInputs({ projectId, roles, values, confirmed, onChange, onConfirm }: {
  projectId?: string; roles: string[]; values: Record<string, string>; confirmed: Record<string, boolean>;
  onChange: (role: string, value: string) => void; onConfirm: (role: string, value: boolean) => void;
}) {
  return <fieldset className="profile-evidence-keyframe"><legend>工作流人物参考输入</legend>
    <p>为每个输入选择当前项目的真实图片，并逐张查看和确认。图片按下列顺序分别传入模型。</p>
    {roles.map((role, index) => <EvidenceImageInput key={role} projectId={projectId} role={role} index={index + 1}
      value={values[role] ?? ""} confirmed={confirmed[role] ?? false}
      onChange={(value) => onChange(role, value)} onConfirm={(value) => onConfirm(role, value)} />)}
  </fieldset>;
}

function EvidenceImageInput({ projectId, role, index, value, confirmed, onChange, onConfirm }: {
  projectId?: string; role: string; index: number; value: string; confirmed: boolean;
  onChange: (value: string) => void; onConfirm: (value: boolean) => void;
}) {
  const selected = useRef(value); selected.current = value;
  const [jobId, setJobId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const prepare = useMutation({ mutationFn: (id: string) => submitMediaDerivative(id, "THUMBNAIL", { size: "medium", frame: "poster" }),
    onSuccess: ({ job }, id) => { if (selected.current === id) setJobId(job.id); } });
  const job = useQuery({ queryKey: ["evidence-reference-preview", jobId], queryFn: () => getJob(jobId!), enabled: !!jobId,
    refetchInterval: (query) => ["SUCCEEDED", "FAILED", "CANCELLED"].includes(query.state.data?.job.state ?? "") ? false : 1000 });
  return <div className="profile-reference-input">
    <ProjectMediaVersionSelect projectId={projectId} value={value} mediaKinds={["IMAGE"]} required label={`人物参考图 ${index}（${role}）`}
      onChange={(id) => { selected.current = id; onChange(id); setLoaded(false); setJobId(null); if (id) prepare.mutate(id); }} />
    {value && job.data?.job.state === "SUCCEEDED" && <figure className="profile-evidence-keyframe__preview">
      <img key={value} src={`/api/v1/media-versions/${encodeURIComponent(value)}/thumbnail?size=medium&frame=poster`}
        alt={`人物参考图 ${index} 真实预览`} width="240" height="320" onLoad={() => setLoaded(true)} onError={() => setLoaded(false)} />
      <figcaption>{role} · 媒体 {value}</figcaption>
    </figure>}
    {prepare.error && <p role="alert">参考图预览准备失败：{String(prepare.error)}</p>}
    {value && !loaded && <p role="status">{job.data?.job.state === "FAILED" ? "预览失败，请重新选择图片重试。" : "正在准备参考图预览…"}</p>}
    <label className="profile-evidence-keyframe__confirmation"><input type="checkbox" checked={confirmed} disabled={!loaded} onChange={(event) => onConfirm(event.target.checked)} /><span>我已查看参考图 {index}，确认人物身份和服装正确。</span></label>
  </div>;
}
