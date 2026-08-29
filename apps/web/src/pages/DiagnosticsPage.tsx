import { useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ErrorState, Skeleton, TabPanel, Tabs } from "../components/ui";
import { DiagnosticsOverview } from "../features/diagnostics/DiagnosticsOverview";
import { AuditHistoryPanel } from "../features/shared/AuditHistoryPanel";
import { GlobalSearchPanel } from "../features/shared/GlobalSearchPanel";
import { getDiagnostics, listProjects, runDiagnostics } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import "./system-workspaces.css";

export const DIAGNOSTIC_READ_TIMEOUT_MS = 15_000;
export const DIAGNOSTIC_RUN_TIMEOUT_MS = 90_000;

export function diagnosticRequestWithTimeout<T>(request: Promise<T>, timeoutMessage: string, timeoutMs: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error(timeoutMessage)), timeoutMs);
    request.then(
      (value) => { window.clearTimeout(timer); resolve(value); },
      (error) => { window.clearTimeout(timer); reject(error); },
    );
  });
}

export function diagnosticErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message.replace(/(?:\s|&#x20;|&#32;|&nbsp;)+$/giu, "");
}

const DIAGNOSTIC_TABS = [
  { id: "env", label: "环境状态" },
  { id: "audit", label: "操作记录" },
  { id: "search", label: "跨项目查找" },
];

/** Engineering diagnostics stay explicit and separate from creator workspaces. */
export function DiagnosticsPage() {
  const params = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const projectId = params.projectId || searchParams.get("project");
  const requestedView = searchParams.get("view");
  const activeTab = DIAGNOSTIC_TABS.some((item) => item.id === requestedView) ? requestedView! : "env";
  const selectTab = (view: string) => {
    const next = new URLSearchParams(searchParams);
    if (view === "env") next.delete("view"); else next.set("view", view);
    setSearchParams(next, { replace: true });
  };

  const projects = useQuery({ queryKey: queryKeys.projects.list({ limit: 100 }), queryFn: () => listProjects({ limit: 100 }) });
  const diagnostics = useQuery({
    queryKey: queryKeys.diagnostics.current(),
    queryFn: () => diagnosticRequestWithTimeout(getDiagnostics(), "诊断记录读取超时，请重试", DIAGNOSTIC_READ_TIMEOUT_MS),
    retry: false,
  });
  const run = useMutation({
    mutationFn: () => diagnosticRequestWithTimeout(
      runDiagnostics(),
      "诊断检查超过 90 秒，服务端可能仍在完成。请稍后重新读取诊断记录。",
      DIAGNOSTIC_RUN_TIMEOUT_MS,
    ),
    onSuccess: () => void diagnostics.refetch(),
  });

  return (
    <div className="v2-page diagnostics-page">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">系统区</p>
          <h2>系统状态与记录</h2>
        </div>
        <span className="status-pill neutral">只读 · 本机事实</span>
      </div>
      <p className="muted">
        查看当前生产依赖、追溯系统操作，或跨项目定位镜头、资产与任务。
      </p>

      <div className="system-workspace-tabs">
        <Tabs items={DIAGNOSTIC_TABS} selectedId={activeTab} onChange={selectTab} ariaLabel="诊断与审计任务" />
      </div>

      <TabPanel id="env" selectedId={activeTab}>
        <div className="system-workspace-stack">
          {diagnostics.isPending && !run.data ? (
            <section className="panel"><Skeleton label="正在读取最近一次环境检查" lines={4} /></section>
          ) : diagnostics.error && !run.data ? (
            <section className="panel"><ErrorState description={`环境状态读取失败：${diagnosticErrorMessage(diagnostics.error)}`} onRetry={() => void diagnostics.refetch()} /></section>
          ) : (
            <DiagnosticsOverview
              run={run.data?.run ?? diagnostics.data?.run ?? null}
              running={run.isPending}
              onRun={() => run.mutate()}
            />
          )}
          {run.error ? (
            <div className="inline-error diagnostic-run-error" role="alert">
              <span>本次检查未完成：{diagnosticErrorMessage(run.error)}</span>
              <button type="button" className="secondary" onClick={() => { run.reset(); void diagnostics.refetch(); }} disabled={diagnostics.isFetching}>
                {diagnostics.isFetching ? "正在读取…" : "保留并读取上次结果"}
              </button>
            </div>
          ) : null}
        </div>
      </TabPanel>

      <TabPanel id="audit" selectedId={activeTab}>
        <div className="system-workspace-stack">
          <section className="system-scope-bar" aria-label="操作记录范围">
            <label>
              查看范围
              <select
                aria-label="操作记录项目范围"
                value={projectId ?? ""}
                onChange={(event) => {
                  const next = new URLSearchParams(searchParams);
                  if (event.target.value) next.set("project", event.target.value);
                  else next.delete("project");
                  setSearchParams(next, { replace: true });
                }}
              >
                <option value="">全部项目</option>
                {projects.data?.items.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.title}
                  </option>
                ))}
              </select>
            </label>
            <span>{projectId ? "只显示所选项目的操作" : "显示所有项目及系统级操作"}</span>
          </section>
          {projects.error ? <p className="inline-error" role="alert">项目范围读取失败，当前仍可查看全部记录。</p> : null}
          <AuditHistoryPanel projectId={projectId} />
        </div>
      </TabPanel>

      <TabPanel id="search" selectedId={activeTab}>
        <div className="system-workspace-stack">
          <section className="system-scope-bar" aria-label="查找范围">
            <label>
              查看范围
              <select
                aria-label="跨项目查找范围"
                value={projectId ?? ""}
                onChange={(event) => {
                  const next = new URLSearchParams(searchParams);
                  if (event.target.value) next.set("project", event.target.value);
                  else next.delete("project");
                  setSearchParams(next, { replace: true });
                }}
              >
                <option value="">所有项目</option>
                {projects.data?.items.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
              </select>
            </label>
            <span>{projectId ? "结果限定在所选项目" : "结果可来自任意项目"}</span>
          </section>
          <GlobalSearchPanel projectId={projectId ?? undefined} />
        </div>
      </TabPanel>
    </div>
  );
}
