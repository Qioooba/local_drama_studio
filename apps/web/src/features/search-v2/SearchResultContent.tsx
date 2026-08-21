import { searchSubjectLabel, type NavigableSearchResult } from "./searchNavigation";

export function SearchResultContent({ item }: { item: NavigableSearchResult }) {
  return <>
    <span className="search-result-copy">
      <strong>{item.label}</strong>
      {(item.context || item.snippet) && <small>{[item.context, item.snippet].filter(Boolean).join(" · ")}</small>}
    </span>
    <span className="search-result-kind">{searchSubjectLabel(item.subject_type)}</span>
  </>;
}
