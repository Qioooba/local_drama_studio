import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createPrompt, listPrompts, type Profile } from "../../generated/api";
import { LANGUAGE_OPTIONS } from "../shared/formOptions";
import { canonicalCapabilityLabel } from "../preferences-v2/canonicalCapabilities";
import { ProfileExecutionDetailButton } from "../model-config/ProfileExecutionDetailButton";

export function PromptTemplatePanel({ projectId, shot, profiles }: { projectId: string | null; shot: Record<string, unknown> | undefined; profiles: Profile[] }) {
  const queryClient = useQueryClient();
  const shotId = shot ? String(shot.id) : null;
  const prompts = useQuery({ queryKey: ["prompts", projectId, shotId], queryFn: () => listPrompts(projectId as string, "SHOT", shotId as string), enabled: Boolean(projectId && shotId) });
  const projectPrompts = useQuery({ queryKey: ["prompts", projectId, "reusable"], queryFn: () => listPrompts(projectId as string), enabled: Boolean(projectId && shotId) });
  const [title, setTitle] = useState("");
  const [templateText, setTemplateText] = useState("");
  const [expandedText, setExpandedText] = useState("");
  const [negativeText, setNegativeText] = useState("");
  const [language, setLanguage] = useState("");
  const [profileVersionId, setProfileVersionId] = useState("");
  const sourceFields = shot?.current_revision && typeof shot.current_revision === "object" ? shot.current_revision as Record<string, unknown> : {};
  const reusable = (projectPrompts.data?.items ?? []).filter((item) => String(item.owner_type) === "SHOT" && String(item.owner_id) !== shotId && String(item.purpose) === "GENERATION_TEMPLATE").slice(0, 6);
  const applyReusable = (item: Record<string, unknown>) => {
    const structured = item.structured && typeof item.structured === "object" ? item.structured as Record<string, unknown> : {};
    setTitle(`${String(item.title ?? "提示词")} · 复用`);
    setTemplateText(String(structured.template_text ?? item.content_text ?? ""));
    setExpandedText(String(structured.expanded_text ?? item.content_text ?? ""));
    setNegativeText(String(structured.negative_text ?? ""));
    setLanguage(String(structured.language ?? ""));
    setProfileVersionId(String(structured.model_profile_version_id ?? ""));
  };
  const create = useMutation({
    mutationFn: () => createPrompt({ project_id: projectId as string, owner_type: "SHOT", owner_id: shotId as string, purpose: "GENERATION_TEMPLATE", title, content_text: expandedText, structured: { source_fields: sourceFields, template_text: templateText, expanded_text: expandedText, negative_text: negativeText, language, model_profile_version_id: profileVersionId } }),
    onSuccess: () => { setTitle(""); setTemplateText(""); setExpandedText(""); setNegativeText(""); void queryClient.invalidateQueries({ queryKey: ["prompts", projectId] }); },
  });
  const valid = Boolean(projectId && shotId && title.trim() && templateText.trim() && expandedText.trim() && language.trim() && profileVersionId);
  return <section className="panel prompt-template-panel" aria-labelledby="prompt-template-title">
    <div className="panel-heading"><div><p className="eyebrow">版本冻结</p><h3 id="prompt-template-title">关键词与提示词模板</h3></div><span className="status-pill">{prompts.data?.items.length ?? 0} 个模板</span></div>
    {!shot ? <p className="empty-state">选择镜头后创建冻结提示词。</p> : <>
      <details><summary>查看原始镜头字段</summary><pre className="prompt-source-fields">{JSON.stringify(sourceFields, null, 2)}</pre></details>
      <details className="prompt-reuse" open={reusable.length > 0}><summary>从项目其他镜头复用（{reusable.length}）</summary>
        {projectPrompts.isLoading ? <p className="muted" role="status">正在读取项目提示词历史…</p> : reusable.length > 0 ? <div className="prompt-revision-list">{reusable.map((item) => <article key={`reuse-${String(item.id)}`}><strong>{String(item.title)} · 镜头历史</strong><p>{String(item.content_text)}</p><button type="button" className="secondary" onClick={() => applyReusable(item)}>采用为当前草稿</button></article>)}</div> : <p className="muted">项目中还没有其他镜头的可复用提示词；保存首个模板后会出现在这里。</p>}
        <p className="muted">采用只填入当前表单，不会覆盖或冻结；你仍可修改并显式保存新修订。</p>
      </details>
      <div className="prompt-template-grid">
        <label>标题<input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        <label>语言<select value={language} onChange={(event) => setLanguage(event.target.value)}><option value="">请选择语言</option>{LANGUAGE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
        <label>模型配置<select value={profileVersionId} onChange={(event) => setProfileVersionId(event.target.value)}><option value="">请选择</option>{profiles.map((profile) => <option value={profile.version_id} key={profile.version_id}>{profile.title} · {canonicalCapabilityLabel(profile.capability)} · 第 {profile.version_no ?? "?"} 版</option>)}</select></label><ProfileExecutionDetailButton profileVersionId={profileVersionId} />
        <label className="wide">模板<textarea value={templateText} onChange={(event) => setTemplateText(event.target.value)} placeholder="使用原始字段编排模板" /></label>
        <label className="wide">最终提示词<textarea value={expandedText} onChange={(event) => setExpandedText(event.target.value)} placeholder="系统展开变量后的完整内容；冻结后用于生成" /></label>
        <label className="wide">负向词<textarea value={negativeText} onChange={(event) => setNegativeText(event.target.value)} /></label>
      </div>
      <div className="prompt-template-actions"><span>保存会创建内容不可变的新版本，不会覆盖历史。</span><button type="button" className="primary-action" disabled={!valid || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "冻结中…" : "冻结最终提示词"}</button></div>
      {(create.error || projectPrompts.error) && <p className="inline-error" role="alert">{String(create.error ?? projectPrompts.error)}</p>}
      <div className="prompt-revision-list">{prompts.data?.items.map((item) => { const structured = item.structured as Record<string, unknown>; const profile = profiles.find((candidate) => candidate.version_id === structured.model_profile_version_id); const languageLabel = typeof structured.language === "string" && structured.language.trim() ? structured.language : "语言未记录"; return <article key={String(item.id)}><strong>{String(item.title)} · 第 {String(item.revision_no)} 版</strong><span>{languageLabel} · {profile ? `${profile.title} · 第 ${profile.version_no ?? "已发布"} 版` : "历史生成配置"}</span><p>{String(item.content_text)}</p><small>负向提示词：{String(structured.negative_text || "（空）")} · 已冻结</small><details><summary>高级：查看校验信息</summary><code>内容校验指纹 {String(item.content_hash).slice(0, 12)}</code>{!profile && <><small>历史生成配置技术标识</small><code>{typeof structured.model_profile_version_id === "string" && structured.model_profile_version_id ? structured.model_profile_version_id : "未记录"}</code></>}</details></article>; })}</div>
    </>}
  </section>;
}
