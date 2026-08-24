export type EpisodePlanAssetState = { id: string; code: string; label: string; state_kind: string };
export type EpisodePlanAsset = {
  asset: { id: string; code: string; name: string; kind: string };
  states: EpisodePlanAssetState[];
  usage: { shots: Array<{ shot_id: string; role_in_shot: string; asset_state_id: string | null }> };
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return generatedRequestJson<T>(`/api/v1${path}`, {
    ...init, headers: { "Content-Type": "application/json", ...init?.headers },
  });
}

export async function getEpisodePlanAssets(projectId: string) {
  return (await request<{ bible: { items: EpisodePlanAsset[] } }>(`/projects/${projectId}/asset-bible`)).bible.items;
}

export async function setEpisodePlanShotAssetState(shotId: string, assetId: string, assetStateId: string) {
  return request(`/shots/${shotId}/asset-state-bindings`, {
    method: "POST", body: JSON.stringify({ asset_id: assetId, asset_state_id: assetStateId }),
  });
}

export async function markEpisodePlanShotReady(shotId: string) {
  return request(`/projects/shots/${shotId}:mark-production-ready`, { method: "POST" });
}

export type BatchCommandResult = { shotId: string; ok: boolean; message: string };

export async function runPerShot(
  shotIds: string[], command: (shotId: string) => Promise<unknown>,
): Promise<BatchCommandResult[]> {
  return Promise.all(shotIds.map(async (shotId) => {
    try {
      await command(shotId);
      return { shotId, ok: true, message: "已提交" };
    } catch (error) {
      return { shotId, ok: false, message: error instanceof Error ? error.message : String(error) };
    }
  }));
}
import { requestJson as generatedRequestJson } from "../../generated/api";
