import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { GenerationWorkbench, type GenerationStep } from "../features/generation/GenerationWorkbench";
import { getDirectorDesk, getEpisodeProduction, getG6Readiness, h3CandidateRuntime, listProfiles, listProjects, planG6I2VProbe, reviewInbox } from "../generated/api";
import { queryKeys } from "../query/queryKeys";

const validSteps: readonly GenerationStep[] = ["setup", "inputs", "preflight", "variants", "review"] as const;

/** Creator-facing owner for shot generation, background preflight, and one resource confirmation. */
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
  const candidates = useQuery({ queryKey: ["generation", "episode", episodeId, "candidates"], queryFn: () => reviewInbox(projectId, "", { episode_id: episodeId, include_resolved: true }), enabled: Boolean(projectId && episodeId) });
  const runtime = useQuery({ queryKey: ["generation", "h3-runtime"], queryFn: () => h3CandidateRuntime() });
  const readiness = useQuery({ queryKey: ["generation", "g6", projectId], queryFn: () => getG6Readiness(projectId), enabled: Boolean(projectId) });
  const probe = useQuery({ queryKey: ["generation", "i2v-probe", projectId], queryFn: () => planG6I2VProbe(projectId), enabled: Boolean(projectId) });
  const shots = (production.data?.items ?? []).filter((item) => !item.archived_at);
  const selectedShotId = shotId && shots.some((item) => String(item.id) === shotId) ? shotId : null;
  const director = useQuery({
    queryKey: ["generation", "director-frame-bridge", projectId, episodeId, selectedShotId],
    queryFn: () => getDirectorDesk(projectId, episodeId, { shotId: selectedShotId ?? undefined }),
    enabled: Boolean(projectId && episodeId && selectedShotId),
  });
  const directorCurrentShot = director.data?.current_shot as { frame_bridge?: { current_start?: { media_version_id?: string; status?: string; stale?: boolean } | null } } | undefined;
  const bridgeStart = directorCurrentShot?.frame_bridge?.current_start;
  const initialSourceImageId = bridgeStart && bridgeStart.status === "LOCKED" && !bridgeStart.stale
    ? bridgeStart.media_version_id
    : undefined;
  const project = useMemo(() => projects.data?.items.find((item) => item.id === projectId), [projectId, projects.data?.items]);
  const pending = profiles.isPending || production.isPending || candidates.isPending;
  const criticalError = profiles.error ?? production.error ?? candidates.error;
  const base = `/projects/${encodeURIComponent(projectId)}/episodes/${encodeURIComponent(episodeId)}`;

  return <div className="v2-page generation-page">
    <header className="v2-page-header"><div><p className="eyebrow">镜头生成</p><h2>为镜头创建和比较候选</h2><p className="muted">系统会自动匹配生成能力、保存创作输入并检查本机资源；真正启动生成前只确认一次，候选仍需由你采用和批准。</p></div><span className="status-pill neutral">后台检查后启动</span></header>
    {pending ? <p className="empty-state" role="status">正在读取镜头、候选与能力版本…</p>
      : criticalError ? <p className="inline-error" role="alert">生成工作区读取失败：{criticalError instanceof Error ? criticalError.message : String(criticalError)}</p>
        : shots.length === 0 ? <section className="panel"><h3>本集还没有可生成镜头</h3><p className="muted">先在分集策划中创建并完善镜头，再返回这里生成候选。</p><button className="secondary" type="button" onClick={() => navigate(`${base}/plan`)}>打开分集策划</button></section>
          : <GenerationWorkbench
            projectId={projectId}
            profiles={profiles.data?.items ?? []}
            candidates={candidates.data?.items ?? []}
            h3={runtime.data?.runtime}
            g6Readiness={readiness.data?.readiness}
            i2vProbePlan={probe.data?.plan}
            shots={shots}
            selectedShotId={selectedShotId}
            initialSourceImageId={initialSourceImageId}
            onSelectShot={(id) => navigate(`${base}/generation/${encodeURIComponent(id)}${searchParams.toString() ? `?${searchParams.toString()}` : ""}`)}
            onOpenProfiles={() => navigate(`/models?project=${encodeURIComponent(projectId)}`)}
            onOpenJobs={(jobId) => navigate(`/jobs?project=${encodeURIComponent(projectId)}&job=${encodeURIComponent(jobId)}`)}
            onOpenReviews={(mediaVersionId) => {
              // The newly created KEYFRAME is not part of the generation-page
              // review query snapshot. Mark the Review owner's inbox stale
              // before navigating so its exact ?media deep link can resolve
              // the candidate instead of falling back to the first cached row.
              void queryClient.invalidateQueries({ queryKey: ["reviews", "inbox", projectId, episodeId] });
              navigate(`${base}/review${mediaVersionId ? `?media=${encodeURIComponent(mediaVersionId)}` : ""}`);
            }}
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
