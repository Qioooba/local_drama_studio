import { useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ErrorState, Skeleton, TabPanel, Tabs } from "../components/ui";
import { AuditHistoryPanel } from "../features/shared/AuditHistoryPanel";
import { DiagnosticPanel } from "../features/status/ReadinessPanels";
import { GlobalSearchPanel } from "../features/shared/GlobalSearchPanel";
import { getDiagnostics, listProjects, runDiagnostics } from "../generated/api";
import { queryKeys } from "../query/queryKeys";
import "./system-workspaces.css";

const DIAGNOSTIC_RUN_TIMEOUT_MS = 10_000;

function diagnosticRequestWithTimeout<T>(request: Promise<T>, timeoutMessage: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error(timeoutMessage)), DIAGNOSTIC_RUN_TIMEOUT_MS);
    request.then(
      (value) => { window.clearTimeout(timer); resolve(value); },
      (error) => { window.clearTimeout(timer); reject(error); },
    );
  });
}

const DIAGNOSTIC_TABS = [
  { id: "env", label: "本机环境检查" },
  { id: "audit", label: "审计历史" },
  { id: "search", label: "全局检索" },
];

/** Engineering diagnostics stay explicit and separate from creator workspaces. */
export function DiagnosticsPage() {
  let projectId: string | null = null;
  let setSearchParams: ((next: URLSearchParams, opts?: { replace?: boolean }) => void) | null = null;
  let searchParams = new URLSearchParams();
  try {
    const params = useParams();
    const [sp, ssp] = useSearchParams();
    searchParams = sp;
    setSearchParams = ssp;
    projectId = params?.projectId || sp.get("project");
  } catch {
    projectId = null;
  }
  const requestedView = searchParams.get("view");
  const activeTab = DIAGNOSTIC_TABS.some((item) => item.id === requestedView) ? requestedView! : "env";
  const selectTab = (view: string) => {
    const next = new URLSearchParams(searchParams);
    if (view === "env") next.delete("view"); else next.set("view", view);
    setSearchParams?.(next, { replace: true });
  };

  const projects = useQuery({ queryKey: queryKeys.projects.list({ limit: 100 }), queryFn: () => listProjects({ limit: 100 }) });
  const diagnostics = useQuery({ queryKey: queryKeys.diagnostics.current(), queryFn: () => diagnosticRequestWithTimeout(getDiagnostics(), "诊断记录读取超时，请重试"), retry: false });
  const run = useMutation({ mutationFn: () => diagnosticRequestWithTimeout(runDiagnostics(), "诊断超时，请重试"), onSuccess: () => void diagnostics.refetch() });

  return (
    <div className="v2-page diagnostics-page">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">系统区</p>
          <h2>诊断、审计与运维检索</h2>
        </div>
        <span className="status-pill neutral">本机事实 · 创作区外</span>
      </div>
      <p className="muted">
        诊断检查本机数据库、FFmpeg、Comfy 与运行时清单；审计历史保持只读、追加和脱敏；全局检索提供跨剧目无泄漏实体定位。
      </p>

      <div className="system-workspace-tabs">
        <Tabs items={DIAGNOSTIC_TABS} selectedId={activeTab} onChange={selectTab} ariaLabel="诊断与审计任务" />
      </div>

      <TabPanel id="env" selectedId={activeTab}>
        <section className="panel" aria-labelledby="diagnostics-local-title">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">显式检查</p>
              <h3 id="diagnostics-local-title">本机环境检查</h3>
            </div>
            <button type="button" className="primary-action" onClick={() => run.mutate()} disabled={run.isPending}>
              {run.isPending ? "检查中…" : "运行诊断"}
            </button>
          </div>
          {diagnostics.isPending ? (
            <Skeleton label="正在读取诊断记录" lines={4} />
          ) : diagnostics.error ? (
            <ErrorState description={`诊断读取失败：${String(diagnostics.error)}`} onRetry={() => void diagnostics.refetch()} />
          ) : (
            <DiagnosticPanel run={run.data?.run ?? diagnostics.data?.run ?? null} />
          )}
          {run.error ? <p className="inline-error" role="alert">诊断运行失败：{String(run.error)}</p> : null}
        </section>
      </TabPanel>

      <TabPanel id="audit" selectedId={activeTab}>
        <div className="system-workspace-stack">
          <section className="panel" aria-label="审计项目范围">
            <label>
              审计项目范围
              <select
                aria-label="审计项目范围"
                value={projectId ?? ""}
                onChange={(event) => {
                  const next = new URLSearchParams(searchParams);
                  if (event.target.value) next.set("project", event.target.value);
                  else next.delete("project");
                  if (setSearchParams) setSearchParams(next, { replace: true });
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
          </section>
          <AuditHistoryPanel projectId={projectId} />
        </div>
      </TabPanel>

      <TabPanel id="search" selectedId={activeTab}>
        <div className="system-workspace-stack">
          <GlobalSearchPanel projectId={projectId ?? undefined} />
        </div>
      </TabPanel>
    </div>
  );
}
