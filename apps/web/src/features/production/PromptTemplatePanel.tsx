import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createPrompt, listPrompts, type Profile } from "../../generated/api";

export function PromptTemplatePanel({ projectId, shot, profiles }: { projectId: string | null; shot: Record<string, unknown> | undefined; profiles: Profile[] }) {
  const queryClient = useQueryClient();
  const shotId = shot ? String(shot.id) : null;
  const prompts = useQuery({ queryKey: ["prompts", projectId, shotId], queryFn: () => listPrompts(projectId as string, "SHOT", shotId as string), enabled: Boolean(projectId && shotId) });
  const [title, setTitle] = useState("");
  const [templateText, setTemplateText] = useState("");
  const [expandedText, setExpandedText] = useState("");
  const [negativeText, setNegativeText] = useState("");
  const [language, setLanguage] = useState("");
  const [profileVersionId, setProfileVersionId] = useState("");
  const sourceFields = shot?.current_revision && typeof shot.current_revision === "object" ? shot.current_revision as Record<string, unknown> : {};
  const create = useMutation({
    mutationFn: () => createPrompt({ project_id: projectId as string, owner_type: "SHOT", owner_id: shotId as string, purpose: "GENERATION_TEMPLATE", title, content_text: expandedText, structured: { source_fields: sourceFields, template_text: templateText, expanded_text: expandedText, negative_text: negativeText, language, model_profile_version_id: profileVersionId } }),
    onSuccess: () => { setTitle(""); setTemplateText(""); setExpandedText(""); setNegativeText(""); void queryClient.invalidateQueries({ queryKey: ["prompts", projectId, shotId] }); },
  });
  const valid = Boolean(projectId && shotId && title.trim() && templateText.trim() && expandedText.trim() && language.trim() && profileVersionId);
  return <section className="panel prompt-template-panel" aria-labelledby="prompt-template-title">
    <div className="panel-heading"><div><p className="eyebrow">FR-WRT-004 · FROZEN</p><h3 id="prompt-template-title">关键词与提示词模板</h3></div><span className="status-pill">{prompts.data?.items.length ?? 0} 个 Prompt</span></div>
    {!shot ? <p className="empty-state">选择镜头后创建冻结提示词。</p> : <>
      <details><summary>查看原始镜头字段</summary><pre className="prompt-source-fields">{JSON.stringify(sourceFields, null, 2)}</pre></details>
      <div className="prompt-template-grid">
        <label>标题<input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        <label>语言<input value={language} onChange={(event) => setLanguage(event.target.value)} placeholder="例如 zh-CN / en" /></label>
        <label>模型 Profile<select value={profileVersionId} onChange={(event) => setProfileVersionId(event.target.value)}><option value="">请选择</option>{profiles.map((profile) => <option value={profile.version_id} key={profile.version_id}>{profile.title} · {profile.capability}</option>)}</select></label>
        <label className="wide">模板<textarea value={templateText} onChange={(event) => setTemplateText(event.target.value)} placeholder="使用原始字段编排模板" /></label>
        <label className="wide">展开结果<textarea value={expandedText} onChange={(event) => setExpandedText(event.target.value)} placeholder="冻结后作为 content_text" /></label>
        <label className="wide">负向词<textarea value={negativeText} onChange={(event) => setNegativeText(event.target.value)} /></label>
      </div>
      <div className="prompt-template-actions"><span>保存会创建内容 hash 固定的 PromptRevision，不覆盖历史。</span><button className="primary-action" disabled={!valid || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "冻结中…" : "冻结展开结果"}</button></div>
      {create.error && <p className="inline-error" role="alert">{String(create.error)}</p>}
      <div className="prompt-revision-list">{prompts.data?.items.map((item) => { const structured = item.structured as Record<string, unknown>; return <article key={String(item.id)}><strong>{String(item.title)} · revision {String(item.revision_no)}</strong><span>{String(structured.language)} · profile {String(structured.model_profile_version_id).slice(0, 12)}</span><p>{String(item.content_text)}</p><small>negative: {String(structured.negative_text || "（空）")} · FROZEN · {String(item.content_hash).slice(0, 12)}</small></article>; })}</div>
    </>}
  </section>;
}
