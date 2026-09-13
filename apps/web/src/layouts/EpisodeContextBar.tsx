import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { parseRouteContext, routes } from "../app/routeRegistry";
import { getEpisodePostOverviewV2, getEpisodeProductionOverviewV2 } from "../generated/api";
import { useCapabilityOptions } from "../features/model-config/CapabilityPicker";
import { EpisodeTaskDrawer } from "./EpisodeTaskDrawer";
import { selectEpisodeStageFacts, type EpisodeStageId } from "./episodeContextSelectors";
import "./episode-context-bar.css";

type EpisodeOption = { id: string; title: string };
type SeasonOption = { id: string; title: string; episodes: EpisodeOption[] };

type EpisodeContextBarProps = {
  episodeId: string;
  onEpisodeChange: (episodeId: string) => void;
  pathname: string;
  projectId: string;
  seasons: SeasonOption[];
};

function currentStage(pathname: string): EpisodeStageId {
  const routeId = parseRouteContext(pathname).routeId;
  if (routeId === "shotStudio" || routeId === "shotStudioShot") return "SHOTS";
  if (routeId === "postReview" || routeId === "postAudio" || routeId === "postEdit") return "POST";
  if (routeId === "delivery") return "DELIVER";
  return "MAKE";
}

export function EpisodeContextBar({ episodeId, onEpisodeChange, pathname, projectId, seasons }: EpisodeContextBarProps) {
  const [taskDrawerOpen, setTaskDrawerOpen] = useState(false);
  const overview = useQuery({ queryKey: ["episode-production-v2", episodeId, "overview"], queryFn: () => getEpisodeProductionOverviewV2(episodeId), staleTime: 5_000 });
  const post = useQuery({ queryKey: ["episode-post-v2", episodeId, "overview"], queryFn: () => getEpisodePostOverviewV2(episodeId), staleTime: 5_000 });
  const textCapability = useCapabilityOptions("LLM_EPISODE_PLAN", { projectId, episodeId });
  const imageCapability = useCapabilityOptions("IMAGE_CHARACTER", { projectId, episodeId });
  const videoCapability = useCapabilityOptions("VIDEO_I2V", { projectId, episodeId });
  const episodes = seasons.flatMap((season) => season.episodes.map((episode) => ({ ...episode, seasonTitle: season.title })));
  const index = episodes.findIndex((episode) => episode.id === episodeId);
  const previous = index > 0 ? episodes[index - 1] : null;
  const next = index >= 0 && index < episodes.length - 1 ? episodes[index + 1] : null;
  const stage = currentStage(pathname);
  const facts = selectEpisodeStageFacts(overview.data?.overview, post.data?.overview);
  const stages: Array<{ id: EpisodeStageId; label: string; to: string }> = [
    { id: "MAKE", label: "本集生成", to: routes.episodePlan(projectId, episodeId) },
    { id: "SHOTS", label: "镜头修正", to: routes.shotStudio(projectId, episodeId) },
    { id: "POST", label: "后期成片", to: routes.postEdit(projectId, episodeId) },
    { id: "DELIVER", label: "交付", to: routes.delivery(projectId, episodeId) },
  ];
  const capabilitySummaries = [
    { label: "文", capability: "LLM_EPISODE_PLAN", query: textCapability },
    { label: "图", capability: "IMAGE_CHARACTER", query: imageCapability },
    { label: "视", capability: "VIDEO_I2V", query: videoCapability },
  ];
  const returnTo = `${pathname}${typeof window === "undefined" ? "" : `${window.location.search}${window.location.hash}`}`;

  return <section className="episode-context-bar" aria-label="当前分集与制作阶段">
    <div className="episode-context-bar__switcher">
      <button type="button" aria-label={previous ? `上一集：${previous.title}` : "已经是第一集"} disabled={!previous} onClick={() => previous && onEpisodeChange(previous.id)}>←</button>
      <label>
        <span className="sr-only">切换当前分集</span>
        <select aria-label="切换当前分集" title="切换当前分集" value={episodeId} onChange={(event) => onEpisodeChange(event.target.value)}>
          {seasons.map((season) => <optgroup key={season.id} label={season.title}>
            {season.episodes.map((episode) => <option key={episode.id} value={episode.id}>{episode.title}</option>)}
          </optgroup>)}
        </select>
      </label>
      <button type="button" aria-label={next ? `下一集：${next.title}` : "已经是最后一集"} disabled={!next} onClick={() => next && onEpisodeChange(next.id)}>→</button>
      <span className="episode-context-bar__count">{index >= 0 ? `${index + 1} / ${episodes.length} 集` : "当前集"}</span>
    </div>
    <nav className="episode-context-bar__stages" aria-label="本集制作阶段">
      {stages.map((item, stageIndex) => <Link key={item.id} to={item.to} className={`${stage === item.id ? "active " : ""}${facts[item.id].state}`} aria-current={stage === item.id ? "step" : undefined} title={facts[item.id].label}>
        <span>{facts[item.id].state === "complete" ? "✓" : stageIndex + 1}</span><span className="episode-context-bar__stage-copy">{item.label}<small>{facts[item.id].label}</small></span>
      </Link>)}
    </nav>
    <div className="episode-context-bar__facts">
      <div className="episode-context-bar__capabilities" aria-label="当前生效能力">
        {capabilitySummaries.map((item) => {
          const option = item.query.data?.selection.option;
          return <Link key={item.capability} to={`/system/capabilities?view=catalog&project=${encodeURIComponent(projectId)}&episode=${encodeURIComponent(episodeId)}&capability=${encodeURIComponent(item.capability)}&returnTo=${encodeURIComponent(returnTo)}`} title={`${item.capability}：${option?.profile.title ?? "尚未就绪"}`}><strong>{item.label}</strong>{option?.model.name ?? (item.query.isPending ? "读取中" : "未就绪")}</Link>;
        })}
      </div>
      <button type="button" className="episode-context-bar__tasks" onClick={() => setTaskDrawerOpen(true)} aria-haspopup="dialog">
        任务 <strong>{overview.data?.overview.active_job_count ?? 0}</strong>{overview.data?.overview.attention_count ? <span>{overview.data.overview.attention_count} 项异常</span> : null}
      </button>
    </div>
    <EpisodeTaskDrawer open={taskDrawerOpen} onClose={() => setTaskDrawerOpen(false)} projectId={projectId} episodeId={episodeId} />
  </section>;
}
