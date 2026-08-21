type KeyFilters = Readonly<Record<string, unknown>>;
type ScopeId = string | null | undefined;

const scopeId = (value: ScopeId) => value || "all";
const filters = (value: KeyFilters = {}) => ({ ...value });

/** Central TanStack Query key authority. Prefix functions are safe invalidation boundaries. */
export const queryKeys = {
  projects: {
    all: ["projects"] as const,
    lists: () => ["projects", "list"] as const,
    list: (value: KeyFilters = {}) => ["projects", "list", filters(value)] as const,
    details: () => ["projects", "detail"] as const,
    detail: (projectId: string) => ["projects", "detail", projectId] as const,
    health: (projectId: string) => ["projects", "detail", projectId, "health"] as const,
  },
  seasons: {
    all: ["seasons"] as const,
    lists: () => ["seasons", "list"] as const,
    list: (projectId: string) => ["seasons", "list", { projectId }] as const,
    detail: (seasonId: string) => ["seasons", "detail", seasonId] as const,
  },
  episodes: {
    all: ["episodes"] as const,
    lists: () => ["episodes", "list"] as const,
    list: (seasonId: string, limit?: number) => ["episodes", "list", { seasonId, ...(limit === undefined ? {} : { limit }) }] as const,
    detail: (episodeId: string) => ["episodes", "detail", episodeId] as const,
  },
  assetBible: {
    all: ["asset-bible"] as const,
    project: (projectId: string) => ["asset-bible", "project", projectId] as const,
    overview: (projectId: string) => ["asset-bible", "project", projectId, "overview"] as const,
    assets: (projectId: string, kind = "all") => ["asset-bible", "project", projectId, "assets", kind] as const,
    storyAssets: (projectId: string) => ["story-assets", projectId] as const,
  },
  productionSettings: {
    all: ["production-settings"] as const,
    project: (projectId: string) => ["production-settings", "project", projectId] as const,
    section: (projectId: string, section: string) => ["production-settings", "project", projectId, section] as const,
  },
  jobs: {
    all: ["jobs"] as const,
    scope: (scope?: ScopeId) => ["jobs", "scope", scopeId(scope)] as const,
    list: (scope?: ScopeId) => ["jobs", "scope", scopeId(scope), "list"] as const,
    detail: (jobId: string) => ["jobs", "detail", jobId] as const,
  },
  capacity: {
    all: ["capacity"] as const,
    scope: (scope?: ScopeId) => ["capacity", "scope", scopeId(scope)] as const,
  },
  diagnostics: {
    all: ["diagnostics"] as const,
    current: () => ["diagnostics", "current"] as const,
  },
  audit: {
    all: ["audit"] as const,
    events: () => ["audit", "events"] as const,
    eventList: (value: KeyFilters = {}) => ["audit", "events", filters(value)] as const,
  },
  profiles: {
    all: ["profiles"] as const,
    list: () => ["profiles", "list"] as const,
    version: (versionId?: ScopeId) => ["profiles", "version", scopeId(versionId)] as const,
  },
  workflows: {
    all: ["workflows"] as const,
    versions: () => ["workflows", "versions"] as const,
  },
  freshness: {
    all: ["freshness"] as const,
    scope: (scopeType: string, scopeIdValue: string) => ["freshness", "scope", scopeType, scopeIdValue] as const,
    report: (scopeType: string, scopeIdValue: string, limit: number) => ["freshness", "scope", scopeType, scopeIdValue, "report", { limit }] as const,
  },
  scriptBreakdown: {
    all: (projectId: string) => ["projects", projectId, "script-breakdown-drafts"] as const,
    jobs: (projectId: string) => ["projects", projectId, "script-breakdown-jobs"] as const,
  },
  sourcePassage: {
    all: ["source-passage"] as const,
    document: (versionId: string) => ["source-passage", "document", versionId] as const,
    page: (versionId: string, start: number) => ["source-passage", "document", versionId, "page", { start }] as const,
    drafts: (projectId: string) => ["projects", projectId, "script-breakdown-drafts"] as const,
    sceneRanges: (episodeId: string) => ["source-passage", "scene-ranges", episodeId] as const,
  },
} as const;
