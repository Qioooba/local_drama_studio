import { useQuery } from "@tanstack/react-query";
import { ErrorState, Skeleton } from "../components/ui";
import { RuntimeEnvironmentsPanel } from "../features/model-config/RuntimeEnvironmentsPanel";
import { ProfileConfigurationPanel } from "../features/profiles/ProfileConfigurationPanel";
import { ComfyLabPanel } from "../features/shared/ComfyLabPanel";
import { listWorkflowVersions } from "../generated/api";
import { queryKeys } from "../query/queryKeys";

export function SystemWorkflowsPage() {
  const workflows = useQuery({ queryKey: queryKeys.workflows.versions(), queryFn: () => listWorkflowVersions() });
  return <div className="v2-page system-workflows-page">
    <div className="panel-heading"><div><p className="eyebrow">定义、验证与发布</p><h3>工作流与运行环境</h3></div><span className="status-pill neutral">系统级</span></div>
    <p className="muted">在一个 owner 中维护工作流版本、能力匹配与本机运行环境。项目只能消费已发布且兼容的版本。</p>
    {workflows.isPending ? <Skeleton label="正在读取工作流版本" lines={5} /> : workflows.error ? <ErrorState description={`工作流读取失败：${String(workflows.error)}`} onRetry={() => void workflows.refetch()} /> : <>
      <ProfileConfigurationPanel mode="workflows" workflows={workflows.data?.items ?? []} workflowsLoading={workflows.isFetching} onChanged={() => void workflows.refetch()} />
      <RuntimeEnvironmentsPanel workflows={(workflows.data?.items ?? []).map((item) => ({ id: item.id, code: item.code, title: item.title, status: item.status }))} />
    </>}
    <details className="models-expert-tools"><summary><span>开发者：Comfy 工作流捕获与本机测试</span><small>仅在接入或排查自定义 ComfyUI 工作流时使用</small></summary><ComfyLabPanel /></details>
  </div>;
}
