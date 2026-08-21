import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { copyProjectTemplate, type Project } from "../../generated/api";

export function ProjectTemplateCopyAction({ project, onCopied }: { project: Project; onCopied: (project: Project) => void }) {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState(`${project.code}_copy`);
  const [title, setTitle] = useState(`${project.title} 副本`);
  const copy = useMutation({
    mutationFn: () => copyProjectTemplate(project.id, { code: code.trim(), title: title.trim() }),
    onSuccess: ({ project: copied }) => onCopied(copied),
  });
  if (!open) return <button type="button" className="secondary" onClick={() => setOpen(true)}>复制为新剧模板</button>;
  return <section className="template-copy" aria-labelledby="template-copy-title">
    <div><strong id="template-copy-title">复制“{project.title}”的可复用模板</strong><p className="muted">仅复制季/集/镜头结构、制作配置、Published Profile 与本地交付规格；不复制媒体、角色授权资产、BrandKit、任务、审核或交付历史。</p></div>
    <label>新项目 code<input value={code} onChange={(event) => setCode(event.target.value)} /></label>
    <label>新项目标题<input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
    {copy.error && <p role="alert">{copy.error.message}</p>}
    <div className="action-row"><button type="button" onClick={() => copy.mutate()} disabled={!code.trim() || !title.trim() || copy.isPending}>{copy.isPending ? "复制中…" : "确认复制"}</button><button type="button" className="secondary" onClick={() => setOpen(false)} disabled={copy.isPending}>取消</button></div>
  </section>;
}
