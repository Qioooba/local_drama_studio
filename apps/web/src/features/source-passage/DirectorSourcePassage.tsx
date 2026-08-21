import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { listScriptBreakdownDrafts } from "../../generated/api";
import { SourcePassagePanel } from "./SourcePassagePanel";
import { queryKeys } from "../../query/queryKeys";

type Props = {
  projectId: string;
  sourceContext: { source_range?: Record<string, unknown> | null };
  fallbackExcerpt?: string | null;
};

function finiteOffset(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
}

export function DirectorSourcePassage({ projectId, sourceContext, fallbackExcerpt }: Props) {
  const drafts = useQuery({ queryKey: queryKeys.sourcePassage.drafts(projectId), queryFn: () => listScriptBreakdownDrafts(projectId) });
  const sourceVersionId = useMemo(() => {
    const items = drafts.data?.items ?? [];
    return items.find((item) => item.application_status === "APPLIED")?.source_document_version_id ?? null;
  }, [drafts.data]);
  const range = sourceContext.source_range;
  const start = finiteOffset(range?.source_start) ?? 0;
  const end = finiteOffset(range?.source_end);

  if (drafts.isLoading) return <p role="status">正在定位镜头原文…</p>;
  if (drafts.isError) return <div className="source-passage-error" role="alert"><span>无法定位原文版本。</span><button type="button" onClick={() => drafts.refetch()}>重试</button></div>;
  return <SourcePassagePanel sourceDocumentVersionId={sourceVersionId} initialStart={start} initialEnd={end} fallbackExcerpt={fallbackExcerpt} label="镜头来源片段" />;
}
