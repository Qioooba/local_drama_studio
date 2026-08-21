import { createElement, type ReactNode } from "react";
import { Navigate, useParams } from "react-router-dom";

export type LocalFeatureFlag = "DIRECTOR_DESK_V2" | "ASSET_BIBLE_V2" | "EPISODE_AGENT_RUN_V2";

const STORAGE_KEY = "local-drama.feature-flags.v2";

function parseBoolean(value: unknown): boolean | undefined {
  if (typeof value === "boolean") return value;
  if (typeof value !== "string") return undefined;
  if (["1", "true", "on", "enabled"].includes(value.trim().toLowerCase())) return true;
  if (["0", "false", "off", "disabled"].includes(value.trim().toLowerCase())) return false;
  return undefined;
}

/** Local-only rollout switch. V2 defaults on; disabled routes fall back within V2 without reviving the retired shell. */
export function featureEnabled(flag: LocalFeatureFlag): boolean {
  try {
    const stored = JSON.parse(globalThis.localStorage?.getItem(STORAGE_KEY) ?? "{}") as Record<string, unknown>;
    const override = parseBoolean(stored[flag]);
    if (override !== undefined) return override;
  } catch { /* Storage may be unavailable or malformed; fall through safely. */ }
  const environment = (import.meta.env as Record<string, unknown>)[`VITE_${flag}`];
  return parseBoolean(environment) ?? true;
}

export function FeatureFlagRoute({ flag, children, fallbackView }: { flag: LocalFeatureFlag; children: ReactNode; fallbackView: "projects" | "generation" }) {
  const { projectId, episodeId, shotId } = useParams();
  if (featureEnabled(flag)) return children;
  void shotId;
  const projectHome = projectId ? `/projects/${encodeURIComponent(projectId)}` : "/projects";
  const episodePlan = projectId && episodeId ? `${projectHome}/episodes/${encodeURIComponent(episodeId)}/plan` : projectHome;
  return createElement(Navigate, { replace: true, to: fallbackView === "generation" ? episodePlan : episodeId ? episodePlan : projectHome });
}

export const featureFlagStorageKey = STORAGE_KEY;
