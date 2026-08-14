import { FormEvent, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  commitStagedProjectPackage,
  dryRunProjectPackage,
  exportProjectPackage,
  stageProjectPackage,
} from "../../generated/api";

type IdentityMode = "REBIND_EXISTING" | "IMPORT_AS_COPY_REWRITE_IDENTITY";

export function ProjectPackageAction({ projectId, onImported }: { projectId: string; onImported?: (projectId: string) => void }) {
  const [inboxName, setInboxName] = useState("");
  const [identityMode, setIdentityMode] = useState<IdentityMode>("IMPORT_AS_COPY_REWRITE_IDENTITY");
  const [code, setCode] = useState("");
  const [title, setTitle] = useState("");
  const inspect = useMutation({ mutationFn: (relPath: string) => dryRunProjectPackage(projectId, relPath) });
  const exportPackage = useMutation({ mutationFn: () => exportProjectPackage(projectId), onSuccess: ({ package: item }) => inspect.mutate(item.rel_path) });
  const stage = useMutation({ mutationFn: () => stageProjectPackage(inboxName.trim()) });
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
  const submitStage = (event: FormEvent) => { event.preventDefault(); if (inboxName.trim()) stage.mutate(); };
  const busy = exportPackage.isPending || inspect.isPending || stage.isPending || commit.isPending;
  const error = exportPackage.error || inspect.error || stage.error || commit.error;
  return <section className="project-package-action" aria-labelledby="project-package-title">
    <div><strong id="project-package-title">v2 标准项目包</strong><p className="muted">导出逐项记录 SHA-256。导入只读取固定本地 inbox，先暂存预检，再明确决定重写身份或仅恢复缺失目录；不会覆盖现有项目目录。</p></div>
    <button className="secondary" onClick={() => exportPackage.mutate()} disabled={busy}>{exportPackage.isPending ? "导出校验中…" : "导出并 dry-run"}</button>
    {exportPackage.data && <p>包：{exportPackage.data.package.rel_path} · {exportPackage.data.package.entry_count} entries · SHA {exportPackage.data.package.sha256.slice(0, 12)}…</p>}
    {inspect.data && <p><strong>{inspect.data.dry_run.status}</strong> · 展开 {inspect.data.dry_run.expanded_bytes} bytes · {inspect.data.dry_run.blockers.join("、") || "hash/schema/disk PASS"}</p>}
    <form className="package-import-form" onSubmit={submitStage}>
      <p className="muted">把 .ldspkg 放入本机 <code>data/imports/project-packages/inbox</code>，这里只填写文件名，不接受任意路径。</p>
      <label>Inbox 文件名<input value={inboxName} onChange={(event) => setInboxName(event.target.value)} placeholder="my-project.ldspkg" required /></label>
      <button className="secondary" type="submit" disabled={busy || !inboxName.trim()}>{stage.isPending ? "暂存校验中…" : "暂存并预检"}</button>
      {stage.data && <div className="package-commit-fields">
        <p><strong>{stage.data.staging.dry_run.status}</strong> · token {stage.data.staging.stage_token.slice(0, 12)}… · 原 inbox 文件已保留</p>
        <label>身份处理<select value={identityMode} onChange={(event) => setIdentityMode(event.target.value as IdentityMode)}>
          <option value="IMPORT_AS_COPY_REWRITE_IDENTITY">导入为副本并重写身份</option>
          <option value="REBIND_EXISTING">仅恢复身份匹配且缺失的目录</option>
        </select></label>
        {identityMode === "IMPORT_AS_COPY_REWRITE_IDENTITY" && <><label>新项目 code<input value={code} onChange={(event) => setCode(event.target.value)} required /></label><label>新项目标题<input value={title} onChange={(event) => setTitle(event.target.value)} required /></label></>}
        <button type="button" onClick={() => commit.mutate()} disabled={busy || (identityMode === "IMPORT_AS_COPY_REWRITE_IDENTITY" && (!code.trim() || !title.trim()))}>{commit.isPending ? "原子提交中…" : "确认提交项目包"}</button>
      </div>}
    </form>
    {error && <p role="alert">{error.message}</p>}
    {commit.data && <p role="status"><strong>{commit.data.commit.status}</strong> · {commit.data.commit.project_code} · staged 包保留，可核验追溯</p>}
  </section>;
}
