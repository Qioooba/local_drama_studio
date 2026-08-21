import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { GenerationWorkbench, type GenerationStep } from "../features/generation/GenerationWorkbench";
import { getEpisodeProduction, getG6Readiness, h3CandidateRuntime, listProfiles, listProjects, planG6I2VProbe, reviewInbox } from "../generated/api";
import { queryKeys } from "../query/queryKeys";

const validSteps: readonly GenerationStep[] = ["setup", "inputs", "preflight", "variants", "review"] as const;

/** V2 owner for explicit, two-step generation preflight and submit. */
export function GenerationPage() {
  const { projectId = "", episodeId = "", shotId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const rawStep = searchParams.get("step");
  const activeStep: GenerationStep = rawStep && validSteps.includes(rawStep as GenerationStep) ? (rawStep as GenerationStep) : "setup";

  const handleStepChange = (step: GenerationStep) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("step", step);
      return next;
    });
  };

  const profiles = useQuery({ queryKey: queryKeys.profiles.list(), queryFn: () => listProfiles() });
  const projects = useQuery({ queryKey: queryKeys.projects.list({ limit: 100 }), queryFn: () => listProjects({ limit: 100 }) });
  const production = useQuery({ queryKey: ["generation", "episode", episodeId, "production"], queryFn: () => getEpisodeProduction(episodeId), enabled: Boolean(episodeId) });
  const candidates = useQuery({ queryKey: ["generation", "episode", episodeId, "candidates"], queryFn: () => reviewInbox(projectId, "", { episode_id: episodeId }), enabled: Boolean(projectId && episodeId) });
  const runtime = useQuery({ queryKey: ["generation", "h3-runtime"], queryFn: () => h3CandidateRuntime() });
  const readiness = useQuery({ queryKey: ["generation", "g6", projectId], queryFn: () => getG6Readiness(projectId), enabled: Boolean(projectId) });
  const probe = useQuery({ queryKey: ["generation", "i2v-probe", projectId], queryFn: () => planG6I2VProbe(projectId), enabled: Boolean(projectId) });
  const shots = production.data?.items ?? [];
  const selectedShotId = shotId && shots.some((item) => String(item.id) === shotId) ? shotId : null;
  const project = useMemo(() => projects.data?.items.find((item) => item.id === projectId), [projectId, projects.data?.items]);
  const pending = profiles.isPending || production.isPending || candidates.isPending;
  const criticalError = profiles.error ?? production.error ?? candidates.error;
  const base = `/projects/${encodeURIComponent(projectId)}/episodes/${encodeURIComponent(episodeId)}`;

  return <div className="v2-page generation-page">
    <header className="v2-page-header"><div><p className="eyebrow">Advanced · Manual Generation</p><h2>手动生成工作台</h2><p className="muted">先建立 Intent 并运行只读预检，再由你二次确认创建 Variant 与 Job；此页不会自动选择或批准候选。</p></div><span className="status-pill neutral">显式提交 · 不可变输入</span></header>
    {pending ? <p className="empty-state" role="status">正在读取镜头、候选与能力版本…</p>
      : criticalError ? <p className="inline-error" role="alert">手动生成上下文读取失败：{criticalError instanceof Error ? criticalError.message : String(criticalError)}</p>
        : shots.length === 0 ? <section className="panel"><h3>本集还没有可生成镜头</h3><p className="muted">先在分集策划中创建并导演镜头，再返回手动生成。</p><button className="secondary" type="button" onClick={() => navigate(`${base}/plan`)}>打开分集策划</button></section>
          : <GenerationWorkbench
            projectId={projectId}
            profiles={profiles.data?.items ?? []}
            candidates={candidates.data?.items ?? []}
            h3={runtime.data?.runtime}
            g6Readiness={readiness.data?.readiness}
            i2vProbePlan={probe.data?.plan}
            shots={shots}
            selectedShotId={selectedShotId}
            onSelectShot={(id) => navigate(`${base}/generation/${encodeURIComponent(id)}${searchParams.toString() ? `?${searchParams.toString()}` : ""}`)}
            onOpenProfiles={() => navigate(`/models?project=${encodeURIComponent(projectId)}`)}
            onOpenReviews={() => navigate(`${base}/review`)}
            onSubmitted={() => {
              void production.refetch();
              void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.scope(projectId) });
            }}
            aspectRatio={project?.aspect_ratio === "9:16" ? "9:16" : project?.aspect_ratio === "16:9" ? "16:9" : undefined}
            activeStep={activeStep}
            onStepChange={handleStepChange}
          />}
    {(runtime.error || readiness.error || probe.error) && <p className="inline-warning" role="status">部分本机门禁暂不可用；服务端预检仍为最终裁决，提交按钮不会绕过真实 blocker。</p>}
  </div>;
}
