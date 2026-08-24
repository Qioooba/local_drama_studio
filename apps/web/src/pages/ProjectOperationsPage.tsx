import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { routes } from "../app/routeRegistry";
import { listProjects } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import "./project-operations.css";

const OWNER_WORKSPACES = [
  {
    id: "settings",
    code: "SET",
    title: "生产设置",
    description: "项目默认、交付目标、品牌、自动化、外发授权与项目包",
    owns: ["配置快照", "品牌与健康", "自动化与发件箱", "资产授权 / 导入导出"],
  },
  {
    id: "models",
    code: "MOD",
    title: "模型与能力",
    description: "Profile、Workflow、能力偏好、适配器合同与许可证存证",
    owns: ["执行 Profile", "工作流版本", "能力解析偏好", "兼容性与合同"],
  },
  {
    id: "jobs",
    code: "JOB",
    title: "任务队列",
    description: "持久化 Job、Attempt、容量快照、重试与取消",
    owns: ["Job 状态", "Attempt 历史", "容量预检", "失败恢复"],
  },
  {
    id: "diagnostics",
    code: "DIA",
    title: "诊断与审计",
    description: "本机环境检查、只读审计历史与跨实体检索",
    owns: ["环境诊断", "审计事件", "全局检索", "故障线索"],
  },
] as const;

/** Migration index only. Each operational fact has one dedicated owner workspace. */
export function ProjectOperationsPage() {
  const { projectId = "" } = useParams();
  const project = useQuery({
    queryKey: queryKeys.projects.detail(projectId),
    queryFn: async () =>
      (await listProjects({ limit: 100 })).items.find((item) => item.id === projectId) ?? null,
    enabled: Boolean(projectId),
  });

  if (!projectId) {
    return <div className="v2-page"><p className="inline-error" role="alert">缺少项目上下文。</p></div>;
  }
  if (project.isPending) {
    return <div className="v2-page"><p className="empty-state" role="status">正在读取项目运维入口…</p></div>;
  }
  if (!project.data) {
    return <div className="v2-page"><p className="inline-error" role="alert">项目读取失败：{project.error instanceof Error ? project.error.message : "项目不存在"}</p></div>;
  }

  const hrefFor = (owner: (typeof OWNER_WORKSPACES)[number]["id"]) => {
    if (owner === "settings") return routes.productionSettings(projectId);
    if (owner === "models") return routes.projectModels(projectId);
    if (owner === "jobs") return routes.projectJobs(projectId);
    return routes.projectDiagnostics(projectId);
  };

  return (
    <div className="v2-page project-operations-page">
      <header className="v2-page-header project-operations-header">
        <div>
          <p className="eyebrow">Project Operations</p>
          <h2>项目运维入口</h2>
          <p className="muted">
            此页只负责分流，不再复制各模块的事实面板。选择任务后进入唯一 owner 工作区，浏览器返回可回到本索引。
          </p>
        </div>
        <span className="status-pill neutral">{project.data.title}</span>
      </header>

      <nav className="project-operations-grid" aria-label="项目运维工作区">
        {OWNER_WORKSPACES.map((workspace) => (
          <Link key={workspace.id} to={hrefFor(workspace.id)} className="project-operation-card">
            <span className="project-operation-code" aria-hidden="true">{workspace.code}</span>
            <div>
              <h3>{workspace.title}</h3>
              <p>{workspace.description}</p>
            </div>
            <span className="project-operation-enter">进入工作区 →</span>
          </Link>
        ))}
      </nav>

      <section className="panel project-operation-ownership" aria-labelledby="operation-ownership-title">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Single ownership</p>
            <h3 id="operation-ownership-title">事实归属与旧入口迁移</h3>
          </div>
        </div>
        <div className="project-operation-owner-list">
          {OWNER_WORKSPACES.map((workspace) => (
            <article key={workspace.id}>
              <strong>{workspace.title}</strong>
              <ul>{workspace.owns.map((item) => <li key={item}>{item}</li>)}</ul>
              <Link to={hrefFor(workspace.id)}>打开唯一 owner</Link>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
