export type RouteScope = "GLOBAL" | "PROJECT" | "EPISODE";

export interface RouteMetadata {
  id: string;
  scope: RouteScope;
  title: string;
  pathPattern: string;
  parentRouteId?: string;
}

export interface BreadcrumbItem { label: string; to?: string; isCurrent?: boolean }
export interface RouteContext {
  routeId: string | null;
  scope: RouteScope | null;
  projectId: string | null;
  episodeId: string | null;
  shotId: string | null;
}

const encode = (value: string) => encodeURIComponent(value.trim());
const projectQuery = (projectId?: string | null) => projectId ? `?project=${encode(projectId)}` : "";

export const routes = {
  home: () => "/",
  projects: () => "/projects",
  quickCreate: () => "/quick-create",
  adaptationPlans: (projectId: string) => "/projects/" + encode(projectId) + "/story/plans",
  adaptationPlan: (projectId: string, planId: string) => "/projects/" + encode(projectId) + "/story/plans/" + encode(planId),
  projectHome: (projectId: string) => `/projects/${encode(projectId)}`,
  story: (projectId: string) => `/projects/${encode(projectId)}/story`,
  storyWorkspace: (projectId: string) => `/projects/${encode(projectId)}/story`,
  assets: (projectId: string) => `/projects/${encode(projectId)}/assets`,
  settings: (projectId: string, section = "production") => `/projects/${encode(projectId)}/settings/${encode(section)}`,
  visualLabs: (projectId: string) => `/projects/${encode(projectId)}/labs`,
  visualLab: (projectId: string, labId: string) => `/projects/${encode(projectId)}/labs/${encode(labId)}`,
  episodePlan: (projectId: string, episodeId: string) => `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/plan`,
  shotStudio: (projectId: string, episodeId: string, shotId?: string | null) => `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/studio${shotId ? `/${encode(shotId)}` : ""}`,
  episodeProduction: (projectId: string, episodeId: string) => `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/production`,
  postReview: (projectId: string, episodeId: string) => `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/post/review`,
  postAudio: (projectId: string, episodeId: string) => `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/post/audio`,
  postEdit: (projectId: string, episodeId: string) => `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/post/edit`,
  delivery: (projectId: string, episodeId: string) => `/projects/${encode(projectId)}/episodes/${encode(episodeId)}/delivery`,
  systemCapabilities: (projectId?: string | null) => projectId ? `/projects/${projectId}/models` : "/system/capabilities",
  systemJobs: (projectId?: string | null) => `/system/jobs${projectQuery(projectId)}`,
  systemDiagnostics: (projectId?: string | null) => `/system/diagnostics${projectQuery(projectId)}`,
  systemWorkflows: (projectId?: string | null) => `/system/workflows${projectQuery(projectId)}`,
} as const;

export const ROUTE_REGISTRY: Record<string, RouteMetadata> = {
  home: { id: "home", scope: "GLOBAL", title: "工作台", pathPattern: "/" },
  projects: { id: "projects", scope: "GLOBAL", title: "项目", pathPattern: "/projects" },
  quickCreate: { id: "quickCreate", scope: "GLOBAL", title: "快速生成", pathPattern: "/quick-create" },
  adaptationPlans: { id: "adaptationPlans", scope: "PROJECT", title: "改编规划", pathPattern: "/projects/:projectId/story/plans", parentRouteId: "story" },
  adaptationPlan: { id: "adaptationPlan", scope: "PROJECT", title: "改编规划", pathPattern: "/projects/:projectId/story/plans/:planId", parentRouteId: "adaptationPlans" },
  projectHome: { id: "projectHome", scope: "PROJECT", title: "首页", pathPattern: "/projects/:projectId", parentRouteId: "projects" },
  story: { id: "story", scope: "PROJECT", title: "故事", pathPattern: "/projects/:projectId/story", parentRouteId: "projectHome" },
  assets: { id: "assets", scope: "PROJECT", title: "资产", pathPattern: "/projects/:projectId/assets", parentRouteId: "projectHome" },
  settings: { id: "settings", scope: "PROJECT", title: "项目设置", pathPattern: "/projects/:projectId/settings/:section", parentRouteId: "projectHome" },
  visualLabs: { id: "visualLabs", scope: "PROJECT", title: "Visual Lab", pathPattern: "/projects/:projectId/labs", parentRouteId: "projectHome" },
  visualLab: { id: "visualLab", scope: "PROJECT", title: "Visual Lab", pathPattern: "/projects/:projectId/labs/:labId", parentRouteId: "visualLabs" },
  episodePlan: { id: "episodePlan", scope: "EPISODE", title: "本集制作", pathPattern: "/projects/:projectId/episodes/:episodeId/plan", parentRouteId: "projectHome" },
  shotStudio: { id: "shotStudio", scope: "EPISODE", title: "镜头", pathPattern: "/projects/:projectId/episodes/:episodeId/studio", parentRouteId: "episodePlan" },
  shotStudioShot: { id: "shotStudioShot", scope: "EPISODE", title: "镜头", pathPattern: "/projects/:projectId/episodes/:episodeId/studio/:shotId", parentRouteId: "shotStudio" },
  episodeProduction: { id: "episodeProduction", scope: "EPISODE", title: "本集制作", pathPattern: "/projects/:projectId/episodes/:episodeId/production", parentRouteId: "episodePlan" },
  postReview: { id: "postReview", scope: "EPISODE", title: "后期 / 审核", pathPattern: "/projects/:projectId/episodes/:episodeId/post/review", parentRouteId: "episodePlan" },
  postAudio: { id: "postAudio", scope: "EPISODE", title: "后期 / 声音", pathPattern: "/projects/:projectId/episodes/:episodeId/post/audio", parentRouteId: "episodePlan" },
  postEdit: { id: "postEdit", scope: "EPISODE", title: "后期 / 编辑", pathPattern: "/projects/:projectId/episodes/:episodeId/post/edit", parentRouteId: "episodePlan" },
  delivery: { id: "delivery", scope: "EPISODE", title: "交付", pathPattern: "/projects/:projectId/episodes/:episodeId/delivery", parentRouteId: "episodePlan" },
  systemCapabilities: { id: "systemCapabilities", scope: "GLOBAL", title: "系统 / 能力", pathPattern: "/system/capabilities" },
  systemJobs: { id: "systemJobs", scope: "GLOBAL", title: "系统 / 任务", pathPattern: "/system/jobs" },
  systemDiagnostics: { id: "systemDiagnostics", scope: "GLOBAL", title: "系统 / 诊断", pathPattern: "/system/diagnostics" },
  systemWorkflows: { id: "systemWorkflows", scope: "GLOBAL", title: "系统 / 工作流", pathPattern: "/system/workflows" },
};

const empty = (): RouteContext => ({ routeId: null, scope: null, projectId: null, episodeId: null, shotId: null });
function decode(value?: string): string | null { try { return value ? decodeURIComponent(value) : null; } catch { return null; } }

export function parseRouteContext(pathname: string): RouteContext {
  const clean = pathname.split("?")[0].replace(/\/+$/, "") || "/";
  if (clean === "/") return { routeId: "home", scope: "GLOBAL", projectId: null, episodeId: null, shotId: null };
  if (clean === "/projects") return { routeId: "projects", scope: "GLOBAL", projectId: null, episodeId: null, shotId: null };
  if (clean === "/quick-create") return { routeId: "quickCreate", scope: "GLOBAL", projectId: null, episodeId: null, shotId: null };
  const system = clean.match(/^\/system\/(capabilities|jobs|diagnostics|workflows)$/);
  if (system) return { routeId: `system${system[1][0].toUpperCase()}${system[1].slice(1)}`, scope: "GLOBAL", projectId: null, episodeId: null, shotId: null };
  const episode = clean.match(/^\/projects\/([^/]+)\/episodes\/([^/]+)\/(plan|studio|production|delivery|post\/(review|audio|edit))(?:\/([^/]+))?$/);
  if (episode) {
    const projectId = decode(episode[1]); const episodeId = decode(episode[2]); const shotId = episode[3] === "studio" ? decode(episode[5]) : null;
    if (!projectId || !episodeId || (episode[5] && !shotId)) return empty();
    const routeId = episode[3] === "plan" ? "episodePlan" : episode[3] === "studio" ? (shotId ? "shotStudioShot" : "shotStudio") : episode[3] === "production" ? "episodeProduction" : episode[3] === "delivery" ? "delivery" : episode[4] === "review" ? "postReview" : episode[4] === "audio" ? "postAudio" : "postEdit";
    return { routeId, scope: "EPISODE", projectId, episodeId, shotId };
  }
  const lab = clean.match(/^\/projects\/([^/]+)\/labs(?:\/([^/]+))?$/);
  if (lab) { const projectId = decode(lab[1]); if (!projectId) return empty(); return { routeId: lab[2] ? "visualLab" : "visualLabs", scope: "PROJECT", projectId, episodeId: null, shotId: null }; }
  const adaptationPlan = clean.match(/^\/projects\/([^/]+)\/story\/plans(?:\/([^/]+))?$/);
  if (adaptationPlan) {
    const projectId = decode(adaptationPlan[1]);
    if (!projectId) return empty();
    return { routeId: adaptationPlan[2] ? "adaptationPlan" : "adaptationPlans", scope: "PROJECT", projectId, episodeId: null, shotId: null };
  }
  const project = clean.match(/^\/projects\/([^/]+)(?:\/(story|assets|settings)(?:\/([^/]+))?)?$/);
  if (project) { const projectId = decode(project[1]); if (!projectId) return empty(); return { routeId: project[2] === "story" ? "story" : project[2] === "assets" ? "assets" : project[2] === "settings" ? "settings" : "projectHome", scope: "PROJECT", projectId, episodeId: null, shotId: null }; }
  return empty();
}

export function validateRouteOwnership(context: RouteContext, expectedProjectId: string, expectedEpisodeId?: string | null) {
  if (context.projectId && context.projectId !== expectedProjectId) return { valid: false, reason: `项目不匹配：当前路由属于项目 ${context.projectId}，而非 ${expectedProjectId}` };
  if (expectedEpisodeId && context.episodeId && context.episodeId !== expectedEpisodeId) return { valid: false, reason: `分集不匹配：当前路由属于分集 ${context.episodeId}，而非 ${expectedEpisodeId}` };
  return { valid: true };
}

export function buildBreadcrumbs({ pathname, projectTitle, seasonTitle, episodeTitle, shotCode }: { pathname: string; projectTitle?: string | null; seasonTitle?: string | null; episodeTitle?: string | null; shotCode?: string | null }): BreadcrumbItem[] {
  const context = parseRouteContext(pathname);
  if (context.routeId === "quickCreate") return [{ label: "快速生成", isCurrent: true }];
  if (context.routeId === "home") return [{ label: "工作台", isCurrent: true }];
  const items: BreadcrumbItem[] = [{ label: "工作台", to: routes.home() }];
  if (context.routeId === "projects") return [...items, { label: "项目", isCurrent: true }];
  if (context.projectId) {
    items.push({ label: "项目", to: routes.projects() });
    if (context.routeId === "projectHome") return [...items, { label: projectTitle || "首页", isCurrent: true }];
    items.push({ label: projectTitle || "首页", to: routes.projectHome(context.projectId) });
  }
  if (context.projectId && context.episodeId) {
    const label = [seasonTitle, episodeTitle].filter(Boolean).join(" / ") || "分集";
    if (context.routeId === "episodePlan") return [...items, { label, isCurrent: true }];
    items.push({ label, to: routes.episodePlan(context.projectId, context.episodeId) });
  }
  if (context.routeId) {
    const meta = ROUTE_REGISTRY[context.routeId];
    if (context.shotId && context.projectId && context.episodeId) {
      items.push({ label: meta.title, to: routes.shotStudio(context.projectId, context.episodeId) }, { label: shotCode || context.shotId, isCurrent: true });
    } else items.push({ label: meta?.title || "工作区", isCurrent: true });
  }
  return items;
}
