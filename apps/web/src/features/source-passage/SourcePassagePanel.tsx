import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getSourcePassage, SOURCE_PASSAGE_MAX_CHARACTERS } from "./sourcePassageApi";
import { queryKeys } from "../../query/queryKeys";
import "./source-passage.css";

type Props = {
  sourceDocumentVersionId: string | null;
  initialStart?: number;
  initialEnd?: number;
  fallbackExcerpt?: string | null;
  label?: string;
};

function initialPageStart(start: number): number {
  return Math.max(0, Math.floor(start) - 1_000);
}

export function SourcePassagePanel({
  sourceDocumentVersionId,
  initialStart = 0,
  initialEnd,
  fallbackExcerpt,
  label = "原文片段",
}: Props) {
  const [pageStart, setPageStart] = useState(() => initialPageStart(initialStart));

  useEffect(() => {
    setPageStart(initialPageStart(initialStart));
  }, [initialStart, initialEnd, sourceDocumentVersionId]);

  const passage = useQuery({
    queryKey: queryKeys.sourcePassage.page(sourceDocumentVersionId ?? "missing", pageStart),
    queryFn: () => getSourcePassage(sourceDocumentVersionId!, pageStart, pageStart + SOURCE_PASSAGE_MAX_CHARACTERS),
    enabled: Boolean(sourceDocumentVersionId),
    retry: false,
  });

  if (!sourceDocumentVersionId) {
    return (
      <section className="source-passage" aria-label={label}>
        <p className="source-passage-notice">尚未找到已应用拆解对应的原文版本。</p>
        {fallbackExcerpt && <blockquote>{fallbackExcerpt}</blockquote>}
      </section>
    );
  }

  if (passage.isLoading) return <div className="source-passage source-passage-notice" role="status">正在读取最多 8,000 个字符…</div>;
  if (passage.isError) {
    return (
      <div className="source-passage source-passage-error" role="alert">
        <span>{passage.error instanceof Error ? passage.error.message : "原文片段读取失败"}</span>
        <button type="button" onClick={() => passage.refetch()}>重试</button>
      </div>
    );
  }
  if (!passage.data) return null;

  const data = passage.data;
  const truncated = data.source_end < data.requested_end && data.has_more;
  const previousStart = Math.max(0, data.source_start - data.maximum_character_count);
  return (
    <section className="source-passage" aria-label={label} aria-busy={passage.isFetching}>
      <div className="source-passage-toolbar">
        <button type="button" disabled={data.source_start <= 0 || passage.isFetching} onClick={() => setPageStart(previousStart)}>← 前一片段</button>
        <span>
          字符 {data.source_start.toLocaleString()}–{data.source_end.toLocaleString()}
          {data.total_character_count === null ? "" : ` / ${data.total_character_count.toLocaleString()}`}
        </span>
        <button type="button" disabled={!data.has_more || passage.isFetching} onClick={() => setPageStart(data.source_end)}>后一片段 →</button>
      </div>
      <pre className="source-passage-text">{data.text}</pre>
      <div className="source-passage-meta">
        <span>偏移单位：Unicode 字符</span>
        <span>单次上限：{data.maximum_character_count.toLocaleString()}</span>
        {truncated && <strong>已按服务端上限截断</strong>}
        {passage.isFetching && <span role="status">正在切换片段…</span>}
      </div>
    </section>
  );
}
