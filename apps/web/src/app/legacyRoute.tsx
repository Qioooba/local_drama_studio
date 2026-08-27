import { Link, Navigate, useLocation } from "react-router-dom";
import { routes } from "./routeRegistry";

const value = (params: URLSearchParams, key: string) => params.get(key)?.trim() || null;

/** Resolve only old URLs with a lossless V2 destination. */
export function resolveLegacyRoute(search: string): string | null {
  const params = new URLSearchParams(search);
  // Rollback flags now stop at the V2 compatibility page; the legacy bundle is gone.
  if (params.get("legacy") === "1") return null;
  const view = value(params, "view");
  const projectId = value(params, "project");
  const episodeId = value(params, "episode");
  const shotId = value(params, "shot");
  const reviewId = value(params, "review");

  if (!view) return routes.projects();
  if (view === "overview") return projectId ? routes.projectHome(projectId) : routes.projects();
  if (view === "jobs") return routes.systemJobs(projectId);
  if (view === "profiles") return projectId ? routes.settings(projectId, "capabilities") : routes.systemCapabilities();
  if (view === "diagnostics") return routes.systemDiagnostics(projectId);
  if (view === "canvas" && projectId) {
    if (shotId) return null;
    return episodeId
      ? routes.episodeProduction(projectId, episodeId)
      : routes.visualLabs(projectId);
  }
  if (view === "projects") {
    if (shotId) return null;
    if (projectId && episodeId) return routes.episodePlan(projectId, episodeId);
    if (projectId) return routes.projectHome(projectId);
    return routes.projects();
  }
  if (view === "generation" && projectId && episodeId) {
    return `${routes.shotStudio(projectId, episodeId, shotId)}?focus=generate`;
  }
  if (view === "reviews" && projectId && episodeId && !reviewId) {
    if (shotId) return null;
    return routes.postReview(projectId, episodeId);
  }
  return null;
}

type CompatibilityLink = { to: string; label: string };

export function legacyCompatibilityLinks(search: string): CompatibilityLink[] {
  const params = new URLSearchParams(search);
  const projectId = value(params, "project");
  const episodeId = value(params, "episode");
  const shotId = value(params, "shot");
  const links: CompatibilityLink[] = [];
  if (projectId && episodeId) {
    links.push({ to: routes.episodePlan(projectId, episodeId), label: "打开分集策划" });
    links.push({ to: routes.shotStudio(projectId, episodeId, shotId), label: "打开镜头工作台" });
    links.push({ to: `${routes.shotStudio(projectId, episodeId, shotId)}?focus=generate`, label: "打开镜头生成" });
    links.push({ to: routes.episodeProduction(projectId, episodeId), label: "打开分集生产" });
    links.push({ to: routes.postReview(projectId, episodeId), label: "打开本集审核" });
    links.push({ to: routes.postAudio(projectId, episodeId), label: "打开声音工作区" });
    links.push({ to: routes.postEdit(projectId, episodeId), label: "打开编辑工作区" });
  } else if (projectId) {
    links.push({ to: routes.projectHome(projectId), label: "打开项目首页" });
    links.push({ to: routes.story(projectId), label: "打开故事工作区" });
    links.push({ to: routes.assets(projectId), label: "打开角色与场景库" });
    links.push({ to: routes.settings(projectId), label: "打开项目设置" });
    links.push({ to: routes.visualLabs(projectId), label: "打开 Visual Lab" });
  }
  links.push({ to: routes.projects(), label: "返回项目列表" });
  links.push({ to: routes.systemJobs(projectId), label: "打开任务与机器" });
  links.push({ to: routes.systemDiagnostics(projectId), label: "打开诊断与审计" });
  return links;
}

export function LegacyCompatibilityPage() {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const context = ["view", "project", "episode", "shot", "review"]
    .map((key) => [key, value(params, key)] as const)
    .filter((entry): entry is readonly [string, string] => Boolean(entry[1]));
  return <main className="v2-page legacy-compatibility" aria-labelledby="legacy-compatibility-title">
    <section className="panel">
      <p className="eyebrow">旧链接兼容</p>
      <h1 id="legacy-compatibility-title">旧链接需要选择新的工作区</h1>
      <p>这个旧地址没有可以准确自动跳转的新页面。为避免丢失审核、画布或镜头上下文，系统没有猜测目标，也没有加载旧版应用。</p>
      {context.length > 0 && <dl aria-label="保留的旧链接上下文">{context.map(([key, item]) => <div key={key}><dt>{key}</dt><dd><code>{item}</code></dd></div>)}</dl>}
      <nav aria-label="可选择的新工作区">{legacyCompatibilityLinks(location.search).map((item) => <Link className="secondary" key={item.to} to={item.to}>{item.label}</Link>)}</nav>
    </section>
  </main>;
}

export function LegacyRouteBoundary() {
  const location = useLocation();
  const destination = resolveLegacyRoute(location.search);
  return destination ? <Navigate to={destination} replace /> : <LegacyCompatibilityPage />;
}
