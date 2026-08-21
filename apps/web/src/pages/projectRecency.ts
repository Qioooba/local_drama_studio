import type { Project } from "../generated/api";

export type ProjectWithUpdatedAt = Project & { updated_at?: string | null };

function timestamp(value: string | null | undefined): number {
  if (!value) return Number.NEGATIVE_INFINITY;
  const normalized = value.includes("T") ? value : `${value.replace(" ", "T")}Z`;
  const parsed = Date.parse(normalized);
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

/** Newest activity first; deterministic fallbacks keep incomplete legacy rows usable. */
export function sortProjectsByUpdatedAt(projects: readonly ProjectWithUpdatedAt[]): ProjectWithUpdatedAt[] {
  return [...projects].sort((left, right) => {
    const leftActivity = timestamp(left.updated_at);
    const rightActivity = timestamp(right.updated_at);
    if (leftActivity !== rightActivity) return rightActivity > leftActivity ? 1 : -1;
    return left.code.localeCompare(right.code) || left.id.localeCompare(right.id);
  });
}

export function partitionRecentProjects(projects: readonly ProjectWithUpdatedAt[], recentLimit = 4) {
  const ordered = sortProjectsByUpdatedAt(projects);
  const boundary = Math.max(0, recentLimit);
  return { recentProjects: ordered.slice(0, boundary), otherProjects: ordered.slice(boundary) };
}
