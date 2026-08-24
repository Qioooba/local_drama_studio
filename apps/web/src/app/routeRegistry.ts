/**
 * Typed Route Registry & Navigation Primitives for LocalDramaStudio V2.
 * Authoritative single source of truth for V2 URLs, route metadata, and context extraction.
 */

export type RouteScope = "GLOBAL" | "PROJECT" | "EPISODE";

export interface RouteMetadata {
  id: string;
  scope: RouteScope;
  title: string;
  pathPattern: string;
  featureFlag?: "ASSET_BIBLE_V2" | "DIRECTOR_DESK_V2" | "EPISODE_AGENT_RUN_V2";
  parentRouteId?: string;
}

export interface BreadcrumbItem {
  label: string;
  to?: string;
  isCurrent?: boolean;
}

export interface RouteContext {
  routeId: string | null;
  scope: RouteScope | null;
  projectId: string | null;
  episodeId: string | null;
  shotId: string | null;
}

const encode = (val: string) => encodeURIComponent(val.trim());

/** Type-safe URL builders for all V2 application routes. */
export const routes = {
  projects: () => "/projects",
  models: (projectId?: string | null) => (projectId ? `/models?project=${encode(projectId)}` : "/models"),
  jobs: (projectId?: string | null) => (projectId ? `/jobs?project=${encode(projectId)}` : "/jobs"),
  diagnostics: (projectId?: string | null) => (projectId ? `/diagnostics?project=${encode(projectId)}` : "/diagnostics"),
  mediaLab: (projectId?: string | null) => (projectId ? `/lab?project=${encode(projectId)}` : "/lab"),

  projectHome: (projectId: string) => `/projects/${encode(projectId)}`,
  story: (projectId: string) => `/projects/${encode(projectId)}/story`,
  storyWorkspace: (projectId: string) => `/projects/${encode(projectId)}/story`,
  assets: (projectId: string) => `/projects/${encode(projectId)}/assets`,
  qcPolicies: (projectId: string) => `/projects/${encode(projectId)}/qc-policies`,
  directorRecipes: (projectId: string) => `/projects/${encode(projectId)}/director-recipes`,
  productionSettings: (projectId: string) => `/projects/${encode(projectId)}/production-settings`,
  settings: (projectId: string) => `/projects/${encode(projectId)}/production-settings`,
  projectModels: (projectId: string) => `/models?project=${encode(projectId)}`,
  projectJobs: (projectId: string) => `/jobs?project=${encode(projectId)}`,
  projectDiagnostics: (projectId: string) => `/diagnostics?project=${encode(projectId)}`,
  canvas: (projectId: string, episodeId?: string | null) =>
    `/projects/${encode(projectId)}/canvas${episodeId ? `?episode=${encode(episodeId)}` : ""}`,
  operations: (projectId: string) => `/projects/${encode(projectId)}/operations`,

  episodePlan: (projectId: string, episodeId: string) =>
    `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/plan`,
  directorDesk: (projectId: string, episodeId: string, shotId?: string | null) =>
    shotId
      ? `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/direct/${encode(shotId)}`
      : `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/direct`,
  generation: (projectId: string, episodeId: string, shotId?: string | null) =>
    shotId
      ? `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/generation/${encode(shotId)}`
      : `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/generation`,
  episodeReview: (projectId: string, episodeId: string) =>
    `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/review`,
  audio: (projectId: string, episodeId: string) =>
    `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/audio`,
  timeline: (projectId: string, episodeId: string) =>
    `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/timeline`,
  delivery: (projectId: string, episodeId: string) =>
    `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/delivery`,
  episodeRun: (projectId: string, episodeId: string) =>
    `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/run`,
} as const;

/** Canonical route metadata registry. */
export const ROUTE_REGISTRY: Record<string, RouteMetadata> = {
  projects: { id: "projects", scope: "GLOBAL", title: "项目列表", pathPattern: "/projects" },
  models: { id: "models", scope: "GLOBAL", title: "模型配置", pathPattern: "/models" },
  jobs: { id: "jobs", scope: "GLOBAL", title: "任务队列", pathPattern: "/jobs" },
  diagnostics: { id: "diagnostics", scope: "GLOBAL", title: "诊断与审计", pathPattern: "/diagnostics" },
  mediaLab: { id: "mediaLab", scope: "GLOBAL", title: "素材实验室", pathPattern: "/lab" },

  projectHome: { id: "projectHome", scope: "PROJECT", title: "项目总览", pathPattern: "/projects/:projectId", parentRouteId: "projects" },
  story: { id: "story", scope: "PROJECT", title: "故事工作区", pathPattern: "/projects/:projectId/story", parentRouteId: "projectHome" },
  assets: { id: "assets", scope: "PROJECT", title: "资产圣经", pathPattern: "/projects/:projectId/assets", featureFlag: "ASSET_BIBLE_V2", parentRouteId: "projectHome" },
  qcPolicies: { id: "qcPolicies", scope: "PROJECT", title: "质检策略", pathPattern: "/projects/:projectId/qc-policies", parentRouteId: "projectHome" },
  directorRecipes: { id: "directorRecipes", scope: "PROJECT", title: "导演配方", pathPattern: "/projects/:projectId/director-recipes", parentRouteId: "projectHome" },
  productionSettings: { id: "productionSettings", scope: "PROJECT", title: "生产设置", pathPattern: "/projects/:projectId/production-settings", parentRouteId: "projectHome" },
  settings: { id: "settings", scope: "PROJECT", title: "生产设置", pathPattern: "/projects/:projectId/settings", parentRouteId: "projectHome" },
  projectModels: { id: "projectModels", scope: "PROJECT", title: "模型配置", pathPattern: "/projects/:projectId/models", parentRouteId: "projectHome" },
  projectJobs: { id: "projectJobs", scope: "PROJECT", title: "任务队列", pathPattern: "/projects/:projectId/jobs", parentRouteId: "projectHome" },
  projectDiagnostics: { id: "projectDiagnostics", scope: "PROJECT", title: "诊断与审计", pathPattern: "/projects/:projectId/diagnostics", parentRouteId: "projectHome" },
  canvas: { id: "canvas", scope: "PROJECT", title: "高级画布", pathPattern: "/projects/:projectId/canvas", parentRouteId: "projectHome" },
  operations: { id: "operations", scope: "PROJECT", title: "项目运营与工具", pathPattern: "/projects/:projectId/operations", parentRouteId: "projectHome" },

  episodePlan: { id: "episodePlan", scope: "EPISODE", title: "分集策划", pathPattern: "/projects/:projectId/episodes/:episodeId/plan", parentRouteId: "projectHome" },
  directorDesk: { id: "directorDesk", scope: "EPISODE", title: "导演工作台", pathPattern: "/projects/:projectId/episodes/:episodeId/direct", featureFlag: "DIRECTOR_DESK_V2", parentRouteId: "episodePlan" },
  directorDeskShot: { id: "directorDeskShot", scope: "EPISODE", title: "导演工作台", pathPattern: "/projects/:projectId/episodes/:episodeId/direct/:shotId", featureFlag: "DIRECTOR_DESK_V2", parentRouteId: "directorDesk" },
  generation: { id: "generation", scope: "EPISODE", title: "镜头生成", pathPattern: "/projects/:projectId/episodes/:episodeId/generation", parentRouteId: "episodePlan" },
  generationShot: { id: "generationShot", scope: "EPISODE", title: "镜头生成", pathPattern: "/projects/:projectId/episodes/:episodeId/generation/:shotId", parentRouteId: "generation" },
  episodeReview: { id: "episodeReview", scope: "EPISODE", title: "本集审核", pathPattern: "/projects/:projectId/episodes/:episodeId/review", parentRouteId: "episodePlan" },
  audio: { id: "audio", scope: "EPISODE", title: "声音工作区", pathPattern: "/projects/:projectId/episodes/:episodeId/audio", parentRouteId: "episodePlan" },
  timeline: { id: "timeline", scope: "EPISODE", title: "时间线", pathPattern: "/projects/:projectId/episodes/:episodeId/timeline", parentRouteId: "episodePlan" },
  delivery: { id: "delivery", scope: "EPISODE", title: "成片交付", pathPattern: "/projects/:projectId/episodes/:episodeId/delivery", parentRouteId: "episodePlan" },
  episodeRun: { id: "episodeRun", scope: "EPISODE", title: "分集生产运行", pathPattern: "/projects/:projectId/episodes/:episodeId/run", featureFlag: "EPISODE_AGENT_RUN_V2", parentRouteId: "episodePlan" },
};

/** Parse projectId, episodeId, shotId, and routeId from any pathname. */
export function parseRouteContext(pathname: string): RouteContext {
  const clean = pathname.split("?")[0].replace(/\/+$/, "") || "/";

  if (clean === "/projects") return { routeId: "projects", scope: "GLOBAL", projectId: null, episodeId: null, shotId: null };
  if (clean === "/models") return { routeId: "models", scope: "GLOBAL", projectId: null, episodeId: null, shotId: null };
  if (clean === "/jobs") return { routeId: "jobs", scope: "GLOBAL", projectId: null, episodeId: null, shotId: null };
  if (clean === "/diagnostics") return { routeId: "diagnostics", scope: "GLOBAL", projectId: null, episodeId: null, shotId: null };
  if (clean === "/lab") return { routeId: "mediaLab", scope: "GLOBAL", projectId: null, episodeId: null, shotId: null };

  const episodeMatch = clean.match(/^\/projects\/([^/]+)\/episodes\/([^/]+)\/(plan|direct|generation|review|audio|timeline|delivery|run)(?:\/([^/]+))?$/);
  if (episodeMatch) {
    const [, projectId, episodeId, action, shotId] = episodeMatch;
    const decodedProjectId = safeDecodeRouteSegment(projectId);
    const decodedEpisodeId = safeDecodeRouteSegment(episodeId);
    const decodedShotId = shotId ? safeDecodeRouteSegment(shotId) : null;
    if (decodedProjectId === null || decodedEpisodeId === null || (shotId && decodedShotId === null)) return emptyRouteContext();
    let routeId: string;
    if (action === "direct") routeId = shotId ? "directorDeskShot" : "directorDesk";
    else if (action === "generation") routeId = shotId ? "generationShot" : "generation";
    else if (action === "plan") routeId = "episodePlan";
    else if (action === "review") routeId = "episodeReview";
    else if (action === "audio") routeId = "audio";
    else if (action === "timeline") routeId = "timeline";
    else if (action === "delivery") routeId = "delivery";
    else routeId = "episodeRun";

    return {
      routeId,
      scope: "EPISODE",
      projectId: decodedProjectId,
      episodeId: decodedEpisodeId,
      shotId: decodedShotId,
    };
  }

  const projectMatch = clean.match(/^\/projects\/([^/]+)(?:\/(story|assets|qc-policies|director-recipes|production-settings|settings|models|jobs|diagnostics|canvas|operations))?$/);
  if (projectMatch) {
    const [, projectId, sub] = projectMatch;
    const decodedProjectId = safeDecodeRouteSegment(projectId);
    if (decodedProjectId === null) return emptyRouteContext();
    let routeId = "projectHome";
    if (sub === "story") routeId = "story";
    else if (sub === "assets") routeId = "assets";
    else if (sub === "qc-policies") routeId = "qcPolicies";
    else if (sub === "director-recipes") routeId = "directorRecipes";
    else if (sub === "production-settings" || sub === "settings") routeId = "productionSettings";
    else if (sub === "models") routeId = "projectModels";
    else if (sub === "jobs") routeId = "projectJobs";
    else if (sub === "diagnostics") routeId = "projectDiagnostics";
    else if (sub === "canvas") routeId = "canvas";
    else if (sub === "operations") routeId = "operations";

    return {
      routeId,
      scope: "PROJECT",
      projectId: decodedProjectId,
      episodeId: null,
      shotId: null,
    };
  }

  return emptyRouteContext();
}

function emptyRouteContext(): RouteContext {
  return { routeId: null, scope: null, projectId: null, episodeId: null, shotId: null };
}

function safeDecodeRouteSegment(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

/** Validate whether current route parameters match expected entity ownership without silent cross-project leak. */
export function validateRouteOwnership(
  context: RouteContext,
  expectedProjectId: string,
  expectedEpisodeId?: string | null
): { valid: boolean; reason?: string } {
  if (context.projectId && context.projectId !== expectedProjectId) {
    return {
      valid: false,
      reason: `项目不匹配：当前路由属于项目 ${context.projectId}，而非 ${expectedProjectId}`,
    };
  }
  if (expectedEpisodeId && context.episodeId && context.episodeId !== expectedEpisodeId) {
    return {
      valid: false,
      reason: `分集不匹配：当前路由属于分集 ${context.episodeId}，而非 ${expectedEpisodeId}`,
    };
  }
  return { valid: true };
}

/** Generate standard hierarchical breadcrumbs for navigation. */
export function buildBreadcrumbs(options: {
  pathname: string;
  projectTitle?: string | null;
  seasonTitle?: string | null;
  episodeTitle?: string | null;
  shotCode?: string | null;
}): BreadcrumbItem[] {
  const { pathname, projectTitle, seasonTitle, episodeTitle, shotCode } = options;
  const context = parseRouteContext(pathname);
  const items: BreadcrumbItem[] = [];

  // Root
  items.push({ label: "项目列表", to: routes.projects() });

  if (context.projectId) {
    const projLabel = projectTitle || "项目总览";
    if (context.routeId === "projectHome") {
      items.push({ label: projLabel, isCurrent: true });
      return items;
    }
    items.push({ label: projLabel, to: routes.projectHome(context.projectId) });

    if (context.scope === "PROJECT" && context.routeId) {
      const meta = ROUTE_REGISTRY[context.routeId];
      items.push({ label: meta?.title || "工作区", isCurrent: true });
      return items;
    }
  }

  if (context.projectId && context.episodeId) {
    const epLabel = [seasonTitle, episodeTitle].filter(Boolean).join(" / ") || "分集策划";
    if (context.routeId === "episodePlan") {
      items.push({ label: epLabel, isCurrent: true });
      return items;
    }
    items.push({ label: epLabel, to: routes.episodePlan(context.projectId, context.episodeId) });

    if (context.routeId && context.routeId in ROUTE_REGISTRY) {
      const meta = ROUTE_REGISTRY[context.routeId];
      if (context.shotId) {
        items.push({
          label: meta.title,
          to: context.routeId.startsWith("director")
            ? routes.directorDesk(context.projectId, context.episodeId)
            : routes.generation(context.projectId, context.episodeId),
        });
        items.push({ label: shotCode || `镜头 ${context.shotId}`, isCurrent: true });
      } else {
        items.push({ label: meta.title, isCurrent: true });
      }
    }
  }

  if (context.scope === "GLOBAL" && context.routeId && context.routeId !== "projects") {
    const meta = ROUTE_REGISTRY[context.routeId];
    items.push({ label: meta?.title || "系统页面", isCurrent: true });
  }

  return items;
}
