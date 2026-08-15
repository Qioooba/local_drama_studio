import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { searchAll } from "../../generated/api";

export function GlobalSearchPanel({ projectId }: { projectId?: string | null }) {
  const [query, setQuery] = useState("");
  const search = useQuery({ queryKey: ["global-search", query, projectId], queryFn: () => searchAll(query.trim(), projectId ?? undefined), enabled: query.trim().length >= 2 });
  return <section className="panel global-search-panel" aria-labelledby="global-search-title"><div className="panel-heading"><div><p className="eyebrow">FR-SRC-001 · SEARCH</p><h3 id="global-search-title">全局搜索</h3></div><span className="status-pill neutral">项目范围</span></div><label>搜索项目、集、镜头、资产、任务、媒体或关键词<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="至少输入 2 个字符" /></label>{query.trim().length >= 2 && <div className="search-results" aria-live="polite">{search.isPending ? <p className="empty-state">搜索中…</p> : search.error ? <p className="inline-error" role="alert">搜索失败：{String(search.error)}</p> : search.data?.items.length ? search.data.items.map((item) => <article className="search-result" key={`${item.subject_type}:${item.subject_id}`}><strong>{item.subject_type}</strong><code>{item.subject_id.slice(0, 16)}</code><span>{item.snippet}</span></article>) : <p className="empty-state">没有匹配结果。</p>}</div>}</section>;
}
