import { useMutation } from "@tanstack/react-query";
import { exportJianyingTimeline, exportTimelineRevision } from "../../generated/api";

export function TimelineExportPanel({ timelineRevisionId, subtitleRevisionId }: { timelineRevisionId: string | null; subtitleRevisionId: string | null }) {
  const standard = useMutation({ mutationFn: () => exportTimelineRevision(timelineRevisionId as string, "standard") });
  const jianying = useMutation({ mutationFn: () => exportJianyingTimeline(timelineRevisionId as string, subtitleRevisionId ?? undefined) });
  return <div className="timeline-v2-export">
    <article><div><strong>标准交换包 · OTIO + CMX 3600 EDL</strong><p>从冻结 revision 生成两种专业剪辑交换文件和校验 manifest。</p></div><button type="button" className="secondary" disabled={!timelineRevisionId || standard.isPending} onClick={() => standard.mutate()}>{standard.isPending ? "校验并导出中…" : "导出标准 / OTIO / EDL"}</button>{standard.isError && <p className="inline-error" role="alert">{standard.error instanceof Error ? standard.error.message : String(standard.error)}</p>}{standard.data && <p className="export-result" role="status">{standard.data.export.reused ? "已复验并复用" : "已导出"} {standard.data.export.files.length} 个文件：<code>{standard.data.export.rel_path}</code></p>}</article>
    <article><div><strong>剪映 / CapCut 草稿</strong><p>复制引用媒体并生成 draft_content.json{subtitleRevisionId ? "，同时写入当前字幕 revision" : "；当前没有可附带的字幕 revision"}。</p></div><button type="button" className="secondary" disabled={!timelineRevisionId || jianying.isPending} onClick={() => jianying.mutate()}>{jianying.isPending ? "生成草稿中…" : "导出剪映草稿"}</button>{jianying.isError && <p className="inline-error" role="alert">{jianying.error instanceof Error ? jianying.error.message : String(jianying.error)}</p>}{jianying.data && <p className="export-result" role="status">{jianying.data.export.reused ? "已复验并复用" : "已导出"} {jianying.data.export.files.length} 个文件：<code>{jianying.data.export.rel_path}</code></p>}</article>
    {!timelineRevisionId && <p className="timeline-policy-note">先冻结一个 TimelineRevision，导出按钮才会开放；导出失败不会修改时间线或数据库。</p>}
  </div>;
}
