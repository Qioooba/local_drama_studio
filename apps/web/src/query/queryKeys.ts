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
    overview: (projectId: string) => ["projects", "detail", projectId, "overview-v2"] as const,
    health: (projectId: string) => ["projects", "detail", projectId, "health"] as const,
    creatorSetup: (projectId: string) => ["projects", "detail", projectId, "creator-setup"] as const,
  },
  seasons: {
    all: ["seasons"] as const,
    lists: () => ["seasons", "list"] as const,
    list: (projectId: string) => ["seasons", "list", { projectId }] as const,
    catalog: (projectId: string) => ["seasons", "catalog", { projectId }] as const,
    detail: (seasonId: string) => ["seasons", "detail", seasonId] as const,
  },
  episodes: {
    all: ["episodes"] as const,
    lists: () => ["episodes", "list"] as const,
    list: (seasonId: string, limit?: number) => ["episodes", "list", { seasonId, ...(limit === undefined ? {} : { limit }) }] as const,
    detail: (episodeId: string) => ["episodes", "detail", episodeId] as const,
    cockpit: (episodeId: string) => ["episodes", "detail", episodeId, "cockpit"] as const,
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
  adaptationPlanning: {
    all: ["adaptation-planning"] as const,
    project: (projectId: string) => ["adaptation-planning", "project", projectId] as const,
    sources: (projectId: string) => ["adaptation-planning", "project", projectId, "sources"] as const,
    plans: (projectId: string) => ["adaptation-planning", "project", projectId, "plans"] as const,
    preflight: (projectId: string, sourceVersionId: string, targetDurationMs: number) =>
      ["adaptation-planning", "project", projectId, "preflight", { sourceVersionId, targetDurationMs }] as const,
    workspace: (planId: string) => ["adaptation-planning", "workspace", planId] as const,
  },
  /**
   * Explainer factory keys.  The shape is the documented
   * `["explainers", projectId, resource, editionId?, revision?]` contract, so a
   * language or edition switch cancels superseded responses instead of leaving a
   * stale payload on screen.
   */
  explainers: {
    all: ["explainers"] as const,
    lists: () => ["explainers", "list"] as const,
    list: (value: KeyFilters = {}) => ["explainers", "list", filters(value)] as const,
    workspace: (projectId: string) => ["explainers", projectId, "workspace"] as const,
    script: (projectId: string, revision?: ScopeId) => ["explainers", projectId, "script", scopeId(revision)] as const,
    segments: (projectId: string, revision?: ScopeId) => ["explainers", projectId, "segments", scopeId(revision)] as const,
    assets: (projectId: string) => ["explainers", projectId, "assets"] as const,
    beats: (projectId: string, editionId?: ScopeId) => ["explainers", projectId, "beats", scopeId(editionId)] as const,
    /**
     * Candidate lists are scoped by owner (beat or entity), purpose and edition so
     * that adopting a reference cannot invalidate a beat's keyframe list, and so a
     * language/edition switch never leaves another edition's candidates on screen
     * (spec D2.2/D7: purpose and edition are part of the scope, not a UI filter).
     */
    candidates: (projectId: string, ownerKind: string, ownerId: string, purpose?: ScopeId, editionId?: ScopeId) =>
      ["explainers", projectId, "candidates", ownerKind, ownerId, scopeId(purpose), scopeId(editionId)] as const,
    beatCandidates: (projectId: string, beatId: string, purpose?: ScopeId, editionId?: ScopeId) =>
      ["explainers", projectId, "candidates", "BEAT", beatId, scopeId(purpose), scopeId(editionId)] as const,
    entityCandidates: (projectId: string, entityId: string) =>
      ["explainers", projectId, "candidates", "ENTITY", entityId, "REFERENCE", scopeId(null)] as const,
    beatImpact: (projectId: string, beatId: string) => ["explainers", projectId, "beat", beatId, "impact"] as const,
    /** Six-step readiness projection that drives the numeric step bar (spec F2.1). */
    readiness: (projectId: string) => ["explainers", projectId, "readiness"] as const,
    generatedClips: (projectId: string, editionId?: ScopeId) => ["explainers", projectId, "clips", scopeId(editionId)] as const,
    claimEvidence: (projectId: string, claimId: string) => ["explainers", projectId, "claim", claimId, "evidence"] as const,
    /**
     * The program-compiled reference-design prompt for one entity (design §C4.4).  It is
     * deterministic and read-only, so it is keyed per entity rather than per candidate.
     */
    referenceDesign: (projectId: string, entityId: string) =>
      ["explainers", projectId, "assets", entityId, "reference-design"] as const,
    clipCandidates: (projectId: string, beatId: string, editionId?: ScopeId) =>
      ["explainers", projectId, "clips", beatId, scopeId(editionId)] as const,
    editions: (projectId: string) => ["explainers", projectId, "editions"] as const,
    narration: (editionId: string, locale?: ScopeId) => ["explainers", "edition", editionId, "narration", scopeId(locale)] as const,
    subtitles: (editionId: string, locale?: ScopeId, format?: string) =>
      ["explainers", "edition", editionId, "subtitles", scopeId(locale), format ?? "JSON"] as const,
    qc: (editionId: string, renderId?: ScopeId) => ["explainers", "edition", editionId, "qc", scopeId(renderId)] as const,
    run: (runId: string) => ["explainers", "run", runId] as const,
    schedules: (value: KeyFilters = {}) => ["explainers", "schedules", filters(value)] as const,
    schedule: (scheduleId: string) => ["explainers", "schedule", scheduleId] as const,
  },
} as const;
