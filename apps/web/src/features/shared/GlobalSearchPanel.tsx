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
    <div className="panel-heading"><div><p className="eyebrow">快速定位</p><h3 id="global-search-title">全局搜索</h3></div><span className="status-pill neutral">{projectId ? "当前项目" : "所有项目"}</span></div>
    <label htmlFor="global-search-input">搜索项目、分集、场景、镜头、资产、源文本、候选、审核或任务</label>
    <input id="global-search-input" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="至少输入 2 个字符，例如“EP03 S12”" autoComplete="off" aria-controls="global-search-results" />
    {!eligible && query.length === 0 && <p className="empty-state">输入关键词（至少 2 个字符）开始检索项目、分集、镜头或资产…</p>}
    {!eligible && query.length > 0 && <p className="search-hint" role="status">再输入一个字符即可搜索。</p>}
    {eligible && <div id="global-search-results" className="search-results" role="list" aria-live="polite" aria-busy={search.status === "loading"}>
      {search.status === "loading" ? <p className="empty-state" role="status">搜索中…</p>
        : search.status === "error" ? <div className="inline-error" role="alert"><strong>搜索失败</strong><span>{search.error}。请修改关键词后重试。</span></div>
          : search.items.length > 0 ? search.items.map((item) => <div role="listitem" key={`${item.subject_type}:${item.subject_id}`}><button type="button" className="search-result" onClick={() => navigate(item.route)} aria-label={`打开${item.label}`}><SearchResultContent item={item} /></button></div>)
            : <p className="empty-state">没有匹配结果。</p>}
    </div>}
  </section>;
}
