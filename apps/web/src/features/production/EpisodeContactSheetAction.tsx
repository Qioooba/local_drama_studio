import { useMutation } from "@tanstack/react-query";
import { exportEpisodeContactSheet } from "../../generated/api";

export function EpisodeContactSheetAction({ episodeId }: { episodeId: string | null }) {
  const mutation = useMutation({ mutationFn: () => exportEpisodeContactSheet(episodeId as string) });
  const exported = mutation.data?.export;
  return (
    <div className="contact-sheet-action">
      <div>
        <strong>已选媒体联系表</strong>
        <p className="muted">复制当前集已选原文件并生成 320px WebP 缩略图；不修改 SQLite，也不接触运行时或网络。</p>
      </div>
      <button className="secondary" disabled={!episodeId || mutation.isPending} onClick={() => mutation.mutate()}>
        {mutation.isPending ? "校验并导出中…" : "导出联系表"}
      </button>
      {mutation.isError && <p className="inline-error" role="alert">{mutation.error instanceof Error ? mutation.error.message : String(mutation.error)}</p>}
      {exported && <p className="export-result" role="status">{exported.reused ? "已复验并复用" : "已导出"} {exported.item_count} 项：<code>{exported.contact_sheet_rel_path}</code></p>}
    </div>
  );
}
