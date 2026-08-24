import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  commitStagedProjectPackage,
  dryRunProjectPackage,
  exportProjectPackage,
  rebuildProjectThumbnails,
  requestJson,
  stageProjectPackage,
} from "../../generated/api";

type IdentityMode = "REBIND_EXISTING" | "IMPORT_AS_COPY_REWRITE_IDENTITY";
type InboxPackage = { name: string; byte_size: number; modified_at: string };

export function ProjectPackageAction({ projectId, onImported }: { projectId: string; onImported?: (projectId: string) => void }) {
  const [inboxName, setInboxName] = useState("");
  const [identityMode, setIdentityMode] = useState<IdentityMode>("IMPORT_AS_COPY_REWRITE_IDENTITY");
  const [code, setCode] = useState("");
  const [title, setTitle] = useState("");
  const inbox = useQuery({ queryKey: ["project-package-inbox"], queryFn: () => requestJson<{ items: InboxPackage[] }>("/api/v1/project-packages:inbox") });
  useEffect(() => {
    const items = inbox.data?.items ?? [];
    if (items.length === 1 && !inboxName) setInboxName(items[0].name);
    else if (inboxName && items.length > 0 && !items.some((item) => item.name === inboxName)) setInboxName("");
  }, [inbox.data?.items, inboxName]);
  const inspect = useMutation({ mutationFn: (relPath: string) => dryRunProjectPackage(projectId, relPath) });
  const exportPackage = useMutation({ mutationFn: () => exportProjectPackage(projectId), onSuccess: ({ package: item }) => inspect.mutate(item.rel_path) });
  const stage = useMutation({ mutationFn: () => stageProjectPackage(inboxName.trim()), onSuccess: ({ staging }) => { setCode(`${staging.dry_run.project_code}_copy_${Date.now().toString(36)}`); setTitle(`${staging.dry_run.project_code} 导入副本`); } });
  const commit = useMutation({
    mutationFn: () => {
      const token = stage.data?.staging.stage_token;
      if (!token) throw new Error("请先暂存并预检项目包");
      return commitStagedProjectPackage(token, identityMode === "REBIND_EXISTING"
        ? { identity_mode: identityMode }
        : { identity_mode: identityMode, code: code.trim(), title: title.trim() });
    },
    onSuccess: ({ commit: result }) => onImported?.(result.project_id),
  });
  const rebuild = useMutation({ mutationFn: () => rebuildProjectThumbnails(projectId) });
  const submitStage = (event: FormEvent) => { event.preventDefault(); if (inboxName.trim()) stage.mutate(); };
  const busy = exportPackage.isPending || inspect.isPending || stage.isPending || commit.isPending || rebuild.isPending;
  const error = exportPackage.error || inspect.error || stage.error || commit.error || rebuild.error;
  return <section className="project-package-action" aria-labelledby="project-package-title">
    <div><strong id="project-package-title">v2 标准项目包</strong><p className="muted">导出逐项记录 SHA-256。导入只读取固定本地 inbox，先暂存预检，再明确决定重写身份或仅恢复缺失目录；不会覆盖现有项目目录。</p></div>
    <div className="action-row"><button type="button" className="secondary" onClick={() => exportPackage.mutate()} disabled={busy}>{exportPackage.isPending ? "导出校验中…" : "导出并 dry-run"}</button><button type="button" className="secondary" onClick={() => rebuild.mutate()} disabled={busy}>{rebuild.isPending ? "重建缩略图中…" : "重建项目缩略图"}</button></div>
    {exportPackage.data && <p>包：{exportPackage.data.package.rel_path} · {exportPackage.data.package.entry_count} entries · SHA {String(exportPackage.data.package.sha256 ?? "").slice(0, 12) || "—"}…</p>}
    {inspect.data && <p><strong>{inspect.data.dry_run.status}</strong> · 展开 {inspect.data.dry_run.expanded_bytes} bytes · {inspect.data.dry_run.blockers.join("、") || "哈希 / 结构 / 磁盘 PASS"}</p>}
    <form className="package-import-form" onSubmit={submitStage}>
      <p className="muted">把 .ldspkg 放入本机 <code>data/imports/project-packages/inbox</code>，系统会自动列出可导入的包。</p>
      <label>待导入项目包<select value={inboxName} onChange={(event) => setInboxName(event.target.value)} disabled={inbox.isPending || inbox.isError || !inbox.data?.items.length} required><option value="">{inbox.isPending ? "正在读取 inbox…" : inbox.isError ? "读取失败，请刷新" : inbox.data?.items.length ? "请选择项目包" : "inbox 中没有 .ldspkg 文件"}</option>{inbox.data?.items.map((item) => <option value={item.name} key={item.name}>{item.name} · {Math.max(1, Math.round(item.byte_size / 1024))} KB</option>)}</select><small>不接受手写路径，避免输错文件名或越过固定目录。</small></label>
      <button className="secondary" type="button" onClick={() => void inbox.refetch()} disabled={inbox.isFetching}>刷新文件列表</button>
      <button className="secondary" type="submit" disabled={busy || !inboxName.trim()}>{stage.isPending ? "暂存校验中…" : "暂存并预检"}</button>
      {stage.data && <div className="package-commit-fields">
        <p><strong>{stage.data.staging.dry_run.status}</strong> · token {String(stage.data.staging.stage_token ?? "").slice(0, 12) || "—"}… · 原 inbox 文件已保留</p>
        <label>身份处理<select value={identityMode} onChange={(event) => setIdentityMode(event.target.value as IdentityMode)}>
          <option value="IMPORT_AS_COPY_REWRITE_IDENTITY">导入为副本并重写身份</option>
          <option value="REBIND_EXISTING">仅恢复身份匹配且缺失的目录</option>
        </select></label>
        {identityMode === "IMPORT_AS_COPY_REWRITE_IDENTITY" && <><div className="field-fact"><span>新项目标识</span><strong>{code}</strong><small>由包内原标识自动派生</small></div><label>新项目标题<input value={title} onChange={(event) => setTitle(event.target.value)} required /></label></>}
        <button type="button" onClick={() => commit.mutate()} disabled={busy || (identityMode === "IMPORT_AS_COPY_REWRITE_IDENTITY" && (!code.trim() || !title.trim()))}>{commit.isPending ? "原子提交中…" : "确认提交项目包"}</button>
      </div>}
    </form>
    {error && <p role="alert">{error.message}</p>}
    {rebuild.data && <div className="thumbnail-rebuild-result" role="status">
      <p>缩略图重建：{rebuild.data.rebuild.created}/{rebuild.data.rebuild.requested} 成功，{rebuild.data.rebuild.failed} 失败，待重试 {rebuild.data.rebuild.pending}。</p>
      {rebuild.data.rebuild.skipped > 0 && <><p className="muted">另跳过 {rebuild.data.rebuild.skipped} 条历史类型不一致记录；它们不是可生成缩略图的真实图片/视频，保留审计但不会反复作为缓存失败。</p><ul>{rebuild.data.rebuild.exclusions.map((item) => <li key={`${item.media_version_id}-${item.code}`}><code>{String(item.media_version_id ?? "未知版本").slice(0, 12)}</code> · {item.code}</li>)}</ul></>}
      {rebuild.data.rebuild.failures.length > 0 && <><p className="muted">失败项未修改源媒体；修复来源后可再次点击“重建项目缩略图”。</p><ul>{rebuild.data.rebuild.failures.map((failure) => <li key={`${failure.media_version_id}-${failure.code}`}><code>{String(failure.media_version_id ?? "未知版本").slice(0, 12)}</code> · {failure.code ?? "UNKNOWN_ERROR"}</li>)}</ul></>}
    </div>}
    {commit.data && <p role="status"><strong>{commit.data.commit.status}</strong> · {commit.data.commit.project_code}
      {commit.data.commit.counts && <> · 媒体 {commit.data.commit.counts.media_versions ?? 0} · 已生成缩略图 {commit.data.commit.counts.thumbnails_created ?? 0} · 失败待重试 {commit.data.commit.counts.thumbnails_failed ?? 0}</>} · staged 包保留，可核验追溯</p>}
  </section>;
}
