import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { SearchResultContent } from "../search-v2/SearchResultContent";
import { useNavigableSearch } from "../search-v2/searchNavigation";

export function GlobalSearchPanel({ projectId }: { projectId?: string | null }) {
  const [query, setQuery] = useState("");
  const navigate = useNavigate();
  const search = useNavigableSearch(query, projectId);
  const eligible = query.trim().length >= 2;

  return <section className="panel global-search-panel" aria-labelledby="global-search-title">
    <div className="panel-heading"><div><p className="eyebrow">按名称或生产编号定位</p><h3 id="global-search-title">跨项目查找</h3></div><span className="status-pill neutral">{projectId ? "所选项目" : "所有项目"}</span></div>
    <label htmlFor="global-search-input">搜索项目、分集、场景、镜头、资产、文本、审核或任务</label>
    <div className="global-search-field">
      <input id="global-search-input" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：北方小院、EP03、S12、失败任务" autoComplete="off" aria-controls="global-search-results" />
      {query ? <button type="button" className="secondary" onClick={() => setQuery("")} aria-label="清空查找关键词">清空</button> : null}
    </div>
    {!eligible && query.length === 0 && <p className="empty-state">输入至少 2 个字符开始查找。</p>}
    {!eligible && query.length > 0 && <p className="search-hint" role="status">再输入一个字符即可搜索。</p>}
    {eligible && <div id="global-search-results" className="search-results" role="list" aria-live="polite" aria-busy={search.status === "loading"}>
      {search.status === "loading" ? <p className="empty-state" role="status">搜索中…</p>
        : search.status === "error" ? <div className="inline-error" role="alert"><strong>查找失败</strong><span>{search.error}。请稍后重试。</span></div>
          : search.items.length > 0 ? search.items.map((item) => <div role="listitem" key={`${item.subject_type}:${item.subject_id}`}><button type="button" className="search-result" onClick={() => navigate(item.route)} aria-label={`打开${item.label}`}><SearchResultContent item={item} /></button></div>)
            : <p className="empty-state">没有匹配结果。</p>}
    </div>}
  </section>;
}
