import type {
  ApiFailure,
  EpisodeOption,
  GenerationPreference,
  GenerationResolution,
  PreferencePutPayload,
  ProjectOption,
  SeasonOption,
  ShotOption,
} from "./types";
import { ApiRequestError, requestJson as generatedRequestJson } from "../../generated/api";

const API_ROOT = "/api/v1";

export class PreferenceApiError extends Error implements ApiFailure {
  status: number;
  code: string;
  requestId: string | null;
  details: Record<string, unknown>;
  suggestedAction: string | null;

  constructor(failure: ApiFailure) {
    super(failure.message);
    this.name = "PreferenceApiError";
    this.status = failure.status;
    this.code = failure.code;
    this.requestId = failure.requestId;
    this.details = failure.details;
    this.suggestedAction = failure.suggestedAction;
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    return await generatedRequestJson<T>(`${API_ROOT}${path}`, init);
  } catch (reason) {
    if (!(reason instanceof ApiRequestError)) throw reason;
    throw new PreferenceApiError({
      status: reason.status,
      code: reason.code,
      message: reason.message,
      requestId: reason.requestId,
      details: reason.details ?? {},
      suggestedAction: reason.suggestedAction,
    });
  }
}

export async function listPreferenceProjects(): Promise<ProjectOption[]> {
  const data = await requestJson<{ items: ProjectOption[] }>("/projects?limit=200");
  return data.items;
}

export async function listPreferenceSeasons(projectId: string): Promise<SeasonOption[]> {
  const data = await requestJson<{ items: SeasonOption[] }>(`/projects/${encodeURIComponent(projectId)}/seasons`);
  return data.items;
}

export async function listPreferenceEpisodes(seasonId: string): Promise<EpisodeOption[]> {
  const data = await requestJson<{ items: EpisodeOption[] }>(`/projects/seasons/${encodeURIComponent(seasonId)}/episodes`);
  return data.items;
}

export async function listPreferenceShots(episodeId: string): Promise<ShotOption[]> {
  const data = await requestJson<{ items: ShotOption[] }>(`/projects/episodes/${encodeURIComponent(episodeId)}/shots`);
  return data.items;
}

export async function listGenerationPreferences(projectId: string): Promise<GenerationPreference[]> {
  const data = await requestJson<{ items: GenerationPreference[] }>(`/projects/${encodeURIComponent(projectId)}/generation-preferences`);
  return data.items;
}

export async function resolveGenerationPreference(
  projectId: string,
  capability: string,
  episodeId?: string,
  shotId?: string,
): Promise<GenerationResolution> {
  const query = new URLSearchParams({ capability });
  if (episodeId) query.set("episode_id", episodeId);
  if (shotId) query.set("shot_id", shotId);
  const data = await requestJson<{ resolution: GenerationResolution }>(
    `/projects/${encodeURIComponent(projectId)}/generation-preferences?${query.toString()}`,
  );
  return data.resolution;
}

export async function putGenerationPreference(
  projectId: string,
  payload: PreferencePutPayload,
): Promise<GenerationPreference> {
  const data = await requestJson<{ preference: GenerationPreference }>(
    `/projects/${encodeURIComponent(projectId)}/generation-preferences`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return data.preference;
}
