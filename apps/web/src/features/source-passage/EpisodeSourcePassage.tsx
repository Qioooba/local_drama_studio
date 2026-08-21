import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listEpisodeSceneRanges, listScriptBreakdownDrafts } from "../../generated/api";
import { SourcePassagePanel } from "./SourcePassagePanel";
import { queryKeys } from "../../query/queryKeys";

type Props = { projectId: string; episodeId: string };

export function EpisodeSourcePassage({ projectId, episodeId }: Props) {
  const [selectedRangeId, setSelectedRangeId] = useState("");
  const drafts = useQuery({ queryKey: queryKeys.sourcePassage.drafts(projectId), queryFn: () => listScriptBreakdownDrafts(projectId) });
  const ranges = useQuery({ queryKey: queryKeys.sourcePassage.sceneRanges(episodeId), queryFn: () => listEpisodeSceneRanges(episodeId) });
  const sourceVersionId = useMemo(() => {
    const items = drafts.data?.items ?? [];
    return items.find((item) => item.application_status === "APPLIED")?.source_document_version_id ?? null;
  }, [drafts.data]);
  const sortedRanges = useMemo(() => [...(ranges.data?.items ?? [])].sort((a, b) => a.ordinal - b.ordinal), [ranges.data]);
  const selectedRange = sortedRanges.find((item) => item.id === selectedRangeId) ?? sortedRanges[0];

  return (
    <section className="source-passage-card" aria-labelledby="episode-source-title">
      <div className="source-passage-heading">
        <div><p className="eyebrow">只读证据</p><h3 id="episode-source-title">分集原文</h3></div>
        {sortedRanges.length > 0 && <label>定位场景<select value={selectedRange?.id ?? ""} onChange={(event) => setSelectedRangeId(event.target.value)}>{sortedRanges.map((item) => <option key={item.id} value={item.id}>{item.scene_code} · {item.scene_title}</option>)}</select></label>}
      </div>
      {(drafts.isLoading || ranges.isLoading) && <p role="status">正在定位原文版本与场景范围…</p>}
      {(drafts.isError || ranges.isError) && <div className="source-passage-error" role="alert"><span>原文索引读取失败。</span><button type="button" onClick={() => { void drafts.refetch(); void ranges.refetch(); }}>重试</button></div>}
      {!drafts.isLoading && !ranges.isLoading && !drafts.isError && !ranges.isError && (
        <SourcePassagePanel sourceDocumentVersionId={sourceVersionId} initialStart={selectedRange?.source_start ?? 0} initialEnd={selectedRange?.source_end} label="分集原文片段" />
      )}
    </section>
  );
}
