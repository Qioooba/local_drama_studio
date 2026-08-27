import { useEffect, useRef, useState } from "react";
import { searchAll } from "../../generated/api";

export type NavigableSearchResult = {
  project_id: string;
  subject_type: string;
  subject_id: string;
  label: string;
  context: string;
  route: string;
  snippet: string;
};

export type SearchState =
  | { status: "idle"; items: NavigableSearchResult[]; error: null }
  | { status: "loading"; items: NavigableSearchResult[]; error: null }
  | { status: "success"; items: NavigableSearchResult[]; error: null }
  | { status: "error"; items: NavigableSearchResult[]; error: string };

const SUBJECT_LABELS: Record<string, string> = {
  PROJECT: "项目",
  SEASON: "季度",
  EPISODE: "分集",
  SCENE: "场景",
  SHOT: "镜头",
  STORY_ASSET: "资产",
  ASSET: "资产",
  SOURCE_TEXT: "源文本",
  SOURCE_DOCUMENT: "源文本",
  MEDIA_VERSION: "候选",
  CANDIDATE: "候选",
  GENERATION_VARIANT: "候选",
  REVIEW: "审核",
  JOB: "任务",
};

export function searchSubjectLabel(subjectType: string) {
  return SUBJECT_LABELS[subjectType.toUpperCase()] ?? "结果";
}

function fallbackRoute(item: Pick<NavigableSearchResult, "project_id" | "subject_type" | "subject_id">) {
  const project = `/projects/${encodeURIComponent(item.project_id)}`;
  switch (item.subject_type.toUpperCase()) {
    case "PROJECT": return `/projects/${encodeURIComponent(item.subject_id)}`;
    case "EPISODE": return `${project}/episodes/${encodeURIComponent(item.subject_id)}/plan`;
    case "STORY_ASSET":
    case "ASSET":
    case "SCENE": return `${project}/assets`;
    case "SOURCE_TEXT":
    case "SOURCE_DOCUMENT": return `${project}/story`;
    case "JOB": return `/jobs?project=${encodeURIComponent(item.project_id)}&job=${encodeURIComponent(item.subject_id)}`;
    default: return project;
  }
}

export function normalizeSearchResult(raw: unknown): NavigableSearchResult | null {
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Record<string, unknown>;
  const projectId = typeof value.project_id === "string" ? value.project_id : "";
  const subjectType = typeof value.subject_type === "string" ? value.subject_type : "";
  const subjectId = typeof value.subject_id === "string" ? value.subject_id : "";
  if (!projectId || !subjectType || !subjectId) return null;
  const proposedRoute = typeof value.route === "string" ? value.route.trim() : "";
  const safeRoute = proposedRoute.startsWith("/") && !proposedRoute.startsWith("//") && !/\/(content|original)(?:[/?#]|$)/i.test(proposedRoute)
    ? proposedRoute
    : fallbackRoute({ project_id: projectId, subject_type: subjectType, subject_id: subjectId });
  return {
    project_id: projectId,
    subject_type: subjectType,
    subject_id: subjectId,
    label: typeof value.label === "string" && value.label.trim() ? value.label.trim() : `${searchSubjectLabel(subjectType)}搜索结果`,
    context: typeof value.context === "string" ? value.context.trim() : "",
    route: safeRoute,
    snippet: typeof value.snippet === "string" ? value.snippet.trim() : "",
  };
}

export function useNavigableSearch(query: string, projectId?: string | null, debounceMs = 250): SearchState {
  const [state, setState] = useState<SearchState>({ status: "idle", items: [], error: null });
  const requestSequence = useRef(0);

  useEffect(() => {
    const normalizedQuery = query.trim();
    const sequence = ++requestSequence.current;
    if (normalizedQuery.length < 2) {
      setState({ status: "idle", items: [], error: null });
      return;
    }
    setState({ status: "loading", items: [], error: null });
    const controller = new AbortController();
    let requestTimeout: number | undefined;
    let timedOut = false;
    const timer = window.setTimeout(() => {
      requestTimeout = window.setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, 15_000);
      void searchAll(normalizedQuery, projectId ?? undefined, 50, "", controller.signal)
        .then((response) => {
          if (sequence !== requestSequence.current) return;
          const items = (response.items as unknown[]).map(normalizeSearchResult).filter((item): item is NavigableSearchResult => item !== null);
          setState({ status: "success", items, error: null });
        })
        .catch((caught: unknown) => {
          if (sequence !== requestSequence.current) return;
          if (controller.signal.aborted && !timedOut) return;
          if (timedOut) {
            setState({ status: "error", items: [], error: "搜索超时，请稍后重试。" });
            return;
          }
          setState({ status: "error", items: [], error: caught instanceof Error ? caught.message : String(caught) });
        })
        .finally(() => {
          if (requestTimeout !== undefined) window.clearTimeout(requestTimeout);
        });
    }, debounceMs);
    return () => {
      window.clearTimeout(timer);
      if (requestTimeout !== undefined) window.clearTimeout(requestTimeout);
      controller.abort();
    };
  }, [debounceMs, projectId, query]);

  return state;
}
