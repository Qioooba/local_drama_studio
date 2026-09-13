import type { EpisodePostOverview, EpisodeProductionOverview } from "../generated/api";

export type EpisodeStageId = "MAKE" | "SHOTS" | "POST" | "DELIVER";
export type EpisodeStageFact = { state: "complete" | "running" | "blocked" | "pending" | "unknown"; label: string };

function productionStages(overview: EpisodeProductionOverview | undefined, codes: Array<keyof NonNullable<EpisodeProductionOverview["stage_summary"]>>): EpisodeStageFact {
  if (!overview?.stage_summary) return { state: "unknown", label: "尚未检查" };
  const facts = codes.map((code) => overview.stage_summary?.[code]).filter(Boolean);
  if (!facts.length) return { state: "pending", label: "尚未开始" };
  const total = facts.reduce((sum, fact) => sum + (fact?.total ?? 0), 0);
  const completed = facts.reduce((sum, fact) => sum + (fact?.completed ?? 0), 0);
  const running = facts.reduce((sum, fact) => sum + (fact?.running ?? 0), 0);
  const blocked = facts.reduce((sum, fact) => sum + (fact?.attention ?? 0) + (fact?.stale ?? 0) + (fact?.requires_confirmation ?? 0), 0);
  if (blocked) return { state: "blocked", label: `${blocked} 项待处理` };
  if (running) return { state: "running", label: `${completed}/${total || completed} 处理中` };
  if (total > 0 && completed >= total) return { state: "complete", label: `${completed}/${total} 已完成` };
  return { state: "pending", label: total ? `${completed}/${total}` : "尚未开始" };
}

export function selectEpisodeStageFacts(overview?: EpisodeProductionOverview, post?: EpisodePostOverview): Record<EpisodeStageId, EpisodeStageFact> {
  return {
    MAKE: productionStages(overview, ["SHOT_PLANNING"]),
    SHOTS: productionStages(overview, ["SHOT_IMAGE", "VIDEO"]),
    POST: post
      ? post.edit.frozen_timeline_id && post.delivery.verified_render_count > 0
        ? { state: "complete", label: "成片已验证" }
        : post.edit.frozen_timeline_id ? { state: "running", label: "等待有效成片" } : post.blockers.length ? { state: "blocked", label: `${post.blockers.length} 项待处理` } : { state: "pending", label: "尚未冻结" }
      : { state: "unknown", label: "尚未检查" },
    DELIVER: post
      ? post.delivery.latest_package_status === "APPROVED" ? { state: "complete", label: "交付已批准" }
        : post.delivery.latest_package_status === "VERIFIED" ? { state: "blocked", label: "交付待人工批准" }
        : post.delivery.package_count > 0 ? { state: "blocked", label: "交付包待处理" } : { state: "pending", label: "尚未交付" }
      : { state: "unknown", label: "尚未检查" },
  };
}
