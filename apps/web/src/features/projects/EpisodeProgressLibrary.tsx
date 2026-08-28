import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import { ChevronRightIcon } from "../../components/icons";
import { MediaThumb } from "../../components/ui";
import type { ProjectOverviewV2 } from "../../generated/api";

type SeasonItem = ProjectOverviewV2["seasons"][number];
export type EpisodeProgressGroup = {
  season: Omit<SeasonItem, "episodes">;
  episodes: SeasonItem["episodes"];
};

type CardSize = "compact" | "standard" | "large";
const INITIAL_EPISODE_COUNT = 24;

const CARD_SIZES: Array<{ id: CardSize; label: string }> = [
  { id: "compact", label: "紧凑" },
  { id: "standard", label: "标准" },
  { id: "large", label: "大图" },
];

const statusLabel: Record<string, string> = {
  NOT_STARTED: "未开始",
  PLANNED: "待制作",
  DRAFT: "草稿",
  DELIVERED: "已交付",
  APPROVED: "已批准",
  IN_PROGRESS: "制作中",
};

const statusClass: Record<string, string> = {
  NOT_STARTED: "state-not_started",
  PLANNED: "state-not_started",
  DRAFT: "state-draft",
  DELIVERED: "state-delivered",
  APPROVED: "state-approved",
  IN_PROGRESS: "state-running",
};

function isComplete(status: string) {
  return /DELIVERED|APPROVED/.test(status.toUpperCase());
}

function thumbnailUrl(episode: EpisodeProgressGroup["episodes"][number], size: CardSize) {
  const derivativeSize = size === "compact" ? "small" : "medium";
  if (episode.preview_render_id) {
    return `/api/v1/episode-renders/${encodeURIComponent(episode.preview_render_id)}/thumbnail?size=${derivativeSize}&frame=poster`;
  }
  if (episode.preview_media_version_id) {
    return `/api/v1/media-versions/${encodeURIComponent(episode.preview_media_version_id)}/thumbnail?size=${derivativeSize}&frame=poster`;
  }
  return null;
}

export function EpisodeProgressLibrary({ projectId, groups, loading }: { projectId: string; groups: EpisodeProgressGroup[]; loading: boolean }) {
  const [cardSize, setCardSize] = useState<CardSize>("standard");
  const firstSeasonId = groups[0]?.season.id;
  const [openSeasonIds, setOpenSeasonIds] = useState<Set<string>>(new Set());
  const [fullyExpandedSeasonIds, setFullyExpandedSeasonIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    setOpenSeasonIds(firstSeasonId ? new Set([firstSeasonId]) : new Set());
    setFullyExpandedSeasonIds(new Set());
  }, [firstSeasonId, projectId]);

  const allExpanded = groups.length > 0 && groups.every((group) => openSeasonIds.has(group.season.id));
  const toggleSeason = (seasonId: string) => {
    setOpenSeasonIds((current) => {
      const next = new Set(current);
      if (next.has(seasonId)) next.delete(seasonId);
      else next.add(seasonId);
      return next;
    });
  };

  return <section className="panel episode-progress-library" aria-labelledby="episode-list-title">
    <div className="panel-heading episode-library-heading">
      <div><p className="eyebrow">分集</p><h3 id="episode-list-title">制作进度</h3></div>
      <span className="status-pill neutral">全项目 {groups.reduce((total, group) => total + group.episodes.length, 0)} 集</span>
    </div>

    <div className="episode-library-toolbar" aria-label="分集网格显示选项">
      <button type="button" className="secondary" disabled={groups.length === 0} onClick={() => setOpenSeasonIds(allExpanded ? new Set() : new Set(groups.map((group) => group.season.id)))}>{allExpanded ? "全部收起" : "全部展开"}</button>
      <div className="episode-card-size" role="group" aria-label="缩略图大小">
        <span>缩放</span>
        {CARD_SIZES.map((option) => <button key={option.id} type="button" aria-pressed={cardSize === option.id} onClick={() => setCardSize(option.id)}>{option.label}</button>)}
      </div>
    </div>

    {loading && <p className="empty-state" role="status">正在读取分集目录…</p>}
    {!loading && groups.map((group) => {
      const expanded = openSeasonIds.has(group.season.id);
      const fullyExpanded = fullyExpandedSeasonIds.has(group.season.id);
      const visibleEpisodes = fullyExpanded ? group.episodes : group.episodes.slice(0, INITIAL_EPISODE_COUNT);
      const regionId = `season-episodes-${group.season.id}`;
      return <section className="episode-season" key={group.season.id} aria-labelledby={`season-${group.season.id}`}>
        <button type="button" className="episode-season-toggle" aria-expanded={expanded} aria-controls={regionId} onClick={() => toggleSeason(group.season.id)}>
          <ChevronRightIcon />
          <span className="episode-season-title"><h4 id={`season-${group.season.id}`}>{group.season.code} · {group.season.title}</h4><small>{expanded ? "收起本季分集" : "展开本季分集"}</small></span>
          <span className="status-pill neutral">本季 {group.episodes.length} 集</span>
        </button>
        {expanded && <div id={regionId} className="episode-season-content">
          <div className={`episode-card-grid episode-card-grid--${cardSize}`}>
          {visibleEpisodes.map((episode, index) => {
            const state = episode.production_status.toUpperCase();
            const complete = isComplete(state);
            const actionLabel = complete ? "查看交付" : "继续制作";
            const destination = complete
              ? routes.delivery(projectId, episode.id)
              : state === "NOT_STARTED" || state === "DRAFT" || state === "PLANNED"
                ? routes.episodePlan(projectId, episode.id)
                : routes.episodeProduction(projectId, episode.id);
            const ordinal = episode.number ?? episode.display_order ?? index + 1;
            return <article className="episode-card" key={episode.id}>
              <Link className="episode-card-link" to={destination} aria-label={`第 ${ordinal} 集 ${episode.title}，${actionLabel}`}>
                <span className="episode-card-media">
                  <MediaThumb src={thumbnailUrl(episode, cardSize)} alt={`第 ${ordinal} 集 ${episode.title} 缩略图`} emptyLabel="暂无画面" aspectRatio="16 / 9" objectFit="cover" />
                  <span className="episode-card-index">第 {ordinal} 集</span>
                </span>
                <span className="episode-card-body">
                  <strong title={episode.title}>{episode.title}</strong>
                  <span className="episode-card-meta"><span className={`status-pill ${statusClass[state] ?? "neutral"}`}>{statusLabel[state] ?? episode.production_status}</span><span>{actionLabel}</span></span>
                </span>
              </Link>
            </article>;
          })}
          {group.episodes.length === 0 && <p className="empty-state">本季度还没有分集。</p>}
          </div>
          {group.episodes.length > INITIAL_EPISODE_COUNT && <div className="episode-season-more">
            <span>{fullyExpanded ? `已显示本季全部 ${group.episodes.length} 集` : `当前显示前 ${INITIAL_EPISODE_COUNT} 集`}</span>
            <button type="button" className="secondary" onClick={() => setFullyExpandedSeasonIds((current) => {
              const next = new Set(current);
              if (next.has(group.season.id)) next.delete(group.season.id);
              else next.add(group.season.id);
              return next;
            })}>{fullyExpanded ? `收起到前 ${INITIAL_EPISODE_COUNT} 集` : `展开全部 ${group.episodes.length} 集`}</button>
          </div>}
        </div>}
      </section>;
    })}
  </section>;
}
