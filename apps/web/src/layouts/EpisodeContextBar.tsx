import { Link } from "react-router-dom";
import { parseRouteContext, routes } from "../app/routeRegistry";
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

type EpisodeStage = "MAKE" | "SHOTS" | "POST" | "DELIVER";

function currentStage(pathname: string): EpisodeStage {
  const routeId = parseRouteContext(pathname).routeId;
  if (routeId === "shotStudio" || routeId === "shotStudioShot") return "SHOTS";
  if (routeId === "postReview" || routeId === "postAudio" || routeId === "postEdit") return "POST";
  if (routeId === "delivery") return "DELIVER";
  return "MAKE";
}

export function EpisodeContextBar({ episodeId, onEpisodeChange, pathname, projectId, seasons }: EpisodeContextBarProps) {
  const episodes = seasons.flatMap((season) => season.episodes.map((episode) => ({ ...episode, seasonTitle: season.title })));
  const index = episodes.findIndex((episode) => episode.id === episodeId);
  const previous = index > 0 ? episodes[index - 1] : null;
  const next = index >= 0 && index < episodes.length - 1 ? episodes[index + 1] : null;
  const stage = currentStage(pathname);
  const stages: Array<{ id: EpisodeStage; label: string; to: string }> = [
    { id: "MAKE", label: "本集生成", to: routes.episodePlan(projectId, episodeId) },
    { id: "SHOTS", label: "镜头修正", to: routes.shotStudio(projectId, episodeId) },
    { id: "POST", label: "后期成片", to: routes.postEdit(projectId, episodeId) },
    { id: "DELIVER", label: "交付", to: routes.delivery(projectId, episodeId) },
  ];

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
      {stages.map((item, stageIndex) => <Link key={item.id} to={item.to} className={stage === item.id ? "active" : ""} aria-current={stage === item.id ? "step" : undefined}>
        <span>{stageIndex + 1}</span>{item.label}
      </Link>)}
    </nav>
  </section>;
}
