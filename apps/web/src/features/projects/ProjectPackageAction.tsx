import { useMutation } from "@tanstack/react-query";
import { dryRunProjectPackage, exportProjectPackage } from "../../generated/api";

export function ProjectPackageAction({ projectId }: { projectId: string }) {
  const inspect = useMutation({ mutationFn: (relPath: string) => dryRunProjectPackage(projectId, relPath) });
  const exportPackage = useMutation({ mutationFn: () => exportProjectPackage(projectId), onSuccess: ({ package: item }) => inspect.mutate(item.rel_path) });
  return <section className="project-package-action" aria-labelledby="project-package-title">
    <div><strong id="project-package-title">v2 标准项目包</strong><p className="muted">导出结构状态与项目文件，逐项记录 SHA-256；dry-run 只校验 schema、hash、磁盘和 identity，不导入或修改数据库。</p></div>
    <button className="secondary" onClick={() => exportPackage.mutate()} disabled={exportPackage.isPending || inspect.isPending}>{exportPackage.isPending ? "导出校验中…" : "导出并 dry-run"}</button>
    {(exportPackage.error || inspect.error) && <p role="alert">{(exportPackage.error ?? inspect.error)?.message}</p>}
    {exportPackage.data && <p>包：{exportPackage.data.package.rel_path} · {exportPackage.data.package.entry_count} entries · SHA {exportPackage.data.package.sha256.slice(0, 12)}…</p>}
    {inspect.data && <p><strong>{inspect.data.dry_run.status}</strong> · 展开 {inspect.data.dry_run.expanded_bytes} bytes · {inspect.data.dry_run.blockers.join("、") || "hash/schema/disk PASS"}</p>}
  </section>;
}
