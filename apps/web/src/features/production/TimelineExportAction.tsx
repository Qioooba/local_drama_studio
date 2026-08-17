import { useMutation } from "@tanstack/react-query";
import { exportJianyingTimeline, exportTimelineRevision } from "../../generated/api";

export function TimelineExportAction({ timelineRevisionId }: { timelineRevisionId: string | null }) {
  const standard = useMutation({ mutationFn: () => exportTimelineRevision(timelineRevisionId as string) });
  const jianying = useMutation({ mutationFn: () => exportJianyingTimeline(timelineRevisionId as string) });
  const standardExported = standard.data?.export;
  const jianyingExported = jianying.data?.export;
  return (
    <div className="local-export-action">
      <div>
        <strong>专业剪辑交换文件</strong>
        <p className="muted">从冻结 TimelineRevision 导出 OTIO、CMX 3600 EDL 与校验 manifest；失败不会修改时间线或数据库。</p>
      </div>
      <button className="secondary" disabled={!timelineRevisionId || standard.isPending} onClick={() => standard.mutate()}>
        {standard.isPending ? "校验并导出中…" : "导出 OTIO / EDL"}
      </button>
      {standard.isError && <p className="inline-error" role="alert">{standard.error instanceof Error ? standard.error.message : String(standard.error)}</p>}
      {standardExported && <p className="export-result" role="status">{standardExported.reused ? "已复验并复用" : "已导出"} {standardExported.files.length} 个文件：<code>{standardExported.rel_path}</code></p>}
      <div>
        <strong>剪映草稿</strong>
        <p className="muted">导出可导入剪映（CapCut）的 draft_content.json 草稿（best-effort 社区逆向格式）并随包复制媒体；失败不会修改时间线或数据库。</p>
      </div>
      <button className="secondary" disabled={!timelineRevisionId || jianying.isPending} onClick={() => jianying.mutate()}>
        {jianying.isPending ? "生成剪映草稿中…" : "导出剪映草稿"}
      </button>
      {jianying.isError && <p className="inline-error" role="alert">{jianying.error instanceof Error ? jianying.error.message : String(jianying.error)}</p>}
      {jianyingExported && <p className="export-result" role="status">{jianyingExported.reused ? "已复验并复用" : "已导出"} {jianyingExported.files.length} 个文件：<code>{jianyingExported.rel_path}</code></p>}
    </div>
  );
}
