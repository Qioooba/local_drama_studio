import { useMutation } from "@tanstack/react-query";
import { exportTimelineRevision } from "../../generated/api";

export function TimelineExportAction({ timelineRevisionId }: { timelineRevisionId: string | null }) {
  const mutation = useMutation({ mutationFn: () => exportTimelineRevision(timelineRevisionId as string) });
  const exported = mutation.data?.export;
  return (
    <div className="local-export-action">
      <div>
        <strong>专业剪辑交换文件</strong>
        <p className="muted">从冻结 TimelineRevision 导出 OTIO、CMX 3600 EDL 与校验 manifest；失败不会修改时间线或数据库。</p>
      </div>
      <button className="secondary" disabled={!timelineRevisionId || mutation.isPending} onClick={() => mutation.mutate()}>
        {mutation.isPending ? "校验并导出中…" : "导出 OTIO / EDL"}
      </button>
      {mutation.isError && <p className="inline-error" role="alert">{mutation.error instanceof Error ? mutation.error.message : String(mutation.error)}</p>}
      {exported && <p className="export-result" role="status">{exported.reused ? "已复验并复用" : "已导出"} {exported.files.length} 个文件：<code>{exported.rel_path}</code></p>}
    </div>
  );
}
