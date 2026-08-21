import { Link, Navigate, useLocation } from "react-router-dom";

const segment = (value: string) => encodeURIComponent(value);
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

  if (!view) return "/projects";
  if (view === "overview") return projectId ? `/projects/${segment(projectId)}` : "/projects";
  if (view === "jobs") return projectId ? `/jobs?project=${segment(projectId)}` : "/jobs";
  if (view === "profiles") return projectId ? `/projects/${segment(projectId)}/production-settings` : "/models";
  if (view === "diagnostics") return projectId ? `/diagnostics?project=${segment(projectId)}` : "/diagnostics";
  if (view === "canvas" && projectId) {
    if (shotId) return null;
    return `/projects/${segment(projectId)}/canvas${episodeId ? `?episode=${segment(episodeId)}` : ""}`;
  }
  if (view === "projects") {
    if (shotId) return null;
    if (projectId && episodeId) return `/projects/${segment(projectId)}/episodes/${segment(episodeId)}/plan`;
    if (projectId) return `/projects/${segment(projectId)}/operations`;
    return "/projects";
  }
  if (view === "generation" && projectId && episodeId) {
    const base = `/projects/${segment(projectId)}/episodes/${segment(episodeId)}/generation`;
    return shotId ? `${base}/${segment(shotId)}` : base;
  }
  if (view === "reviews" && projectId && episodeId && !reviewId) {
    if (shotId) return null;
    return `/projects/${segment(projectId)}/episodes/${segment(episodeId)}/review`;
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
    const episode = `/projects/${segment(projectId)}/episodes/${segment(episodeId)}`;
    links.push({ to: `${episode}/plan`, label: "打开分集策划" });
    links.push({ to: shotId ? `${episode}/direct/${segment(shotId)}` : `${episode}/direct`, label: "打开导演工作台" });
    links.push({ to: shotId ? `${episode}/generation/${segment(shotId)}` : `${episode}/generation`, label: "打开手动生成" });
    links.push({ to: `${episode}/review`, label: "打开本集审核" });
    links.push({ to: `${episode}/audio`, label: "打开声音工作区" });
    links.push({ to: `${episode}/timeline`, label: "打开时间线" });
  } else if (projectId) {
    const project = `/projects/${segment(projectId)}`;
    links.push({ to: project, label: "打开项目首页" });
    links.push({ to: `${project}/story`, label: "打开故事工作区" });
    links.push({ to: `${project}/assets`, label: "打开资产圣经" });
    links.push({ to: `${project}/production-settings`, label: "打开生产设置" });
    links.push({ to: `${project}/canvas`, label: "打开高级画布" });
    links.push({ to: `${project}/operations`, label: "打开项目运营与兼容工具" });
  }
  links.push({ to: "/projects", label: "返回项目列表" });
  links.push({ to: "/jobs", label: "打开任务与机器" });
  links.push({ to: "/diagnostics", label: "打开诊断与审计" });
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
      <p className="eyebrow">V2 链接兼容</p>
      <h1 id="legacy-compatibility-title">旧链接需要选择新的工作区</h1>
      <p>这个旧地址没有可无损自动跳转的 V2 页面。为避免丢失精确审核、画布或镜头上下文，系统没有猜测目标，也没有加载旧版应用。</p>
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
