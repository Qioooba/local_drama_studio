import { Link } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import type { EpisodeProductionShot, StoryboardShot } from "../../generated/api";
import { MediaThumbnail } from "../shared/MediaThumbnail";

const SHOT_STATUS_LABELS: Record<string, string> = { DRAFT: "待准备", DIRECTED: "待确认", READY: "可生成", PRODUCTION_READY: "可生成", GENERATING: "生成中", RUNNING: "生成中", FAILED: "失败", SELECTED: "已选中", APPROVED: "已批准" };
const OVERALL_STATUS_LABELS: Record<string, string> = { EMPTY: "未开始", READY: "整段已完成", RUNNING: "整段生成中", NEEDS_REVIEW: "整段待确认", BLOCKED: "整段受阻", FAILED: "整段失败", STALE: "整段需更新", CANCELLED: "整段已取消" };

function shotReadiness(shot: EpisodeProductionShot) {
  if (shot.shot_readiness) return shot.shot_readiness;
  const planning = shot.stages?.find((stage) => stage.stage_code === "SHOT_PLANNING");
  return {
    status: planning?.state === "READY" ? "READY" : "DRAFT",
    ready: planning?.state === "READY",
    allowed_actions: planning?.allowed_actions ?? [],
  };
}

function FramePlaceholder() {
  return <svg aria-hidden="true" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m6 16 4-4 3 3 2-2 3 3" /></svg>;
}

function firstText(fields: Record<string, unknown>, keys: string[], fallback: string) {
  for (const key of keys) if (typeof fields[key] === "string" && fields[key].trim()) return fields[key].trim();
  return fallback;
}

function shortShotCode(shotCode: string, shotId: string) {
  const raw = shotCode || shotId.slice(0, 8);
  // EPISODE_001-01-01 -> 01-01；保留短码避免挤爆两列卡片
  const parts = raw.split("-");
  if (parts.length >= 3 && parts[0].startsWith("EPISODE")) return parts.slice(-2).join("-");
  return raw;
}

function dialogueSummary(value: unknown): string {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (Array.isArray(value)) {
    const first = value.find((item) => typeof item === "string" || (item && typeof item === "object"));
    if (typeof first === "string") return first;
    if (first && typeof first === "object") return firstText(first as Record<string, unknown>, ["text", "content", "dialogue"], "无对白");
  }
  return "无对白";
}

export function EpisodeShotBoard({ projectId, episodeId, shots, storyboard = [] }: { projectId: string; episodeId: string; shots: EpisodeProductionShot[]; storyboard?: StoryboardShot[] }) {
  const readyCount = shots.filter((shot) => shotReadiness(shot).ready).length;
  const detailsById = new Map(storyboard.map((shot) => [shot.id, shot]));
  const thumbnailUrl = (id: string) => `/api/v1/media-versions/${encodeURIComponent(id)}/thumbnail?size=medium&frame=poster`;
  return <section className="episode-shot-board" aria-labelledby="episode-shot-board-title">
    <header><div><p className="eyebrow">本集镜头总览</p><h2 id="episode-shot-board-title">一集 · {shots.length} 个镜头</h2><p>整集由 AI 自动生产并推荐结果。只有关键镜头或异常镜头需要进入这里局部修正。</p></div><dl><div><dt>可生成</dt><dd>{readyCount}</dd></div><div><dt>待处理</dt><dd>{shots.length - readyCount}</dd></div></dl></header>
    {shots.length ? <ol aria-label="本集镜头">{shots.map((shot, index) => {
      const slots = shot.material_slots ?? [];
      const keyframe = slots.find((slot) => slot.kind === "KEYFRAME")?.selected_version_id ?? null;
      const video = slots.find((slot) => slot.kind === "VIDEO");
      const videoSelected = video?.selected_version_id ?? null;
      const thumbId = keyframe ?? videoSelected;
        const detail = detailsById.get(shot.shot_id);
        const fields = (detail?.fields ?? {}) as Record<string, unknown>;
        const readiness = shotReadiness(shot);
        const overallLabel = OVERALL_STATUS_LABELS[shot.overall_state] ?? shot.overall_state;
        const cameraPlan = (fields.camera_plan ?? {}) as Record<string, unknown>;
        const cameraMovement = typeof cameraPlan.movement === "string" && cameraPlan.movement ? cameraPlan.movement : null;
        const composition = typeof fields.composition === "string" && fields.composition ? fields.composition : null;
        return <li key={shot.shot_id}><Link to={`${routes.shotStudio(projectId, episodeId, shot.shot_id)}?focus=generate`}>
          <span className="episode-shot-board__thumb">{thumbId ? <MediaThumbnail src={thumbnailUrl(thumbId)} alt="" fallbackLabel="关键帧待生成" loading="lazy" decoding="async" /> : <FramePlaceholder />}</span>
          <span className="episode-shot-board__meta"><small>镜头 {String(index + 1).padStart(2, "0")}</small><strong title={shot.shot_code || shot.shot_id}>{shortShotCode(shot.shot_code, shot.shot_id)}</strong></span>
          <span className={`episode-shot-board__state state-${readiness.status.toLowerCase()}`}>{SHOT_STATUS_LABELS[readiness.status] ?? readiness.status}</span>
          {shot.overall_state !== "READY" && <small className={`episode-shot-board__aggregate state-${shot.overall_state.toLowerCase()}`}>整段：{overallLabel}</small>}
          <span className="episode-shot-board__summary">{firstText(fields, ["subject_action", "action", "visual", "summary", "creative_intent"], "AI 画面描述待生成")}</span>
          <div className="episode-shot-board__tags">
            {cameraMovement && <span className="episode-shot-board__tag">运镜: {cameraMovement}</span>}
            {composition && <span className="episode-shot-board__tag">构图: {composition}</span>}
            <span className="episode-shot-board__tag continuity-tag">首尾帧流转</span>
          </div>
          <span className="episode-shot-board__dialogue">对白：{dialogueSummary(detail?.current_dialogue ?? fields.dialogue)}</span>
          <span className="episode-shot-board__duration">{detail ? `${(detail.target_duration_ms / 1000).toFixed(1)} 秒` : "时长待同步"}</span>
          <span className="episode-shot-board__action">{video?.candidate_count ? `${video.candidate_count} 个候选 · 查看` : readiness.ready ? "生成候选" : "智能自愈 / 修正"}</span>
        </Link></li>;
      })}</ol> : <p className="empty-state">本集还没有镜头，请返回本集制作，让 AI 生成本集方案。</p>}
      <div className="episode-shot-board__footer">
        <Link className="primary v2-inline-link" to={routes.episodePlan(projectId, episodeId)}>返回本集制作并继续</Link>
      </div>
    </section>;
}
