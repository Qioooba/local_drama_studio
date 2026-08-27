import { NavLink, Outlet, useParams, useSearchParams } from "react-router-dom";
import { routes } from "../app/routeRegistry";

function SectionNav({ items }: { items: Array<{ to: string; label: string; end?: boolean }> }) {
  return <nav className="workspace-section-nav" aria-label="工作区分区">{items.map((item) => <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => isActive ? "active" : ""}>{item.label}</NavLink>)}</nav>;
}

export function PostShell() {
  const { projectId = "", episodeId = "" } = useParams();
  return <section className="workspace-section-shell">
    <header className="workspace-section-head"><div><p className="eyebrow">本集后期</p><h2>审阅、声音与编辑</h2></div></header>
    <SectionNav items={[
      { to: routes.postReview(projectId, episodeId), label: "审核" },
      { to: routes.postAudio(projectId, episodeId), label: "声音" },
      { to: routes.postEdit(projectId, episodeId), label: "编辑" },
    ]} />
    <Outlet />
  </section>;
}

export function ProjectSettingsShell() {
  const { projectId = "" } = useParams();
  const items = [
    ["production", "生产"], ["capabilities", "能力"], ["directing", "导演"], ["quality", "质量"],
    ["delivery", "交付"], ["automation", "自动化"], ["rights", "权利"], ["data", "数据"],
  ] as const;
  return <section className="workspace-section-shell">
    <header className="workspace-section-head"><div><p className="eyebrow">项目级规则</p><h2>项目设置</h2></div><p>这里只保存项目策略和绑定；运行控制与系统实现细节不在设置中。</p></header>
    <SectionNav items={items.map(([section, label]) => ({ to: routes.settings(projectId, section), label }))} />
    <Outlet />
  </section>;
}

export function SystemShell() {
  const [params] = useSearchParams();
  const projectId = params.get("project");
  return <section className="workspace-section-shell system-center-shell">
    <header className="workspace-section-head"><div><p className="eyebrow">高级与运维</p><h2>系统中心</h2></div><p>创作流程只消费能力与状态；连接、任务细节、诊断和工作流在这里管理。</p></header>
    <SectionNav items={[
      { to: routes.systemCapabilities(projectId), label: "能力" },
      { to: routes.systemJobs(projectId), label: "任务" },
      { to: routes.systemDiagnostics(projectId), label: "诊断" },
      { to: routes.systemWorkflows(projectId), label: "工作流" },
    ]} />
    <Outlet />
  </section>;
}
