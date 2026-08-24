export type ShotGroupKind = "BEAT" | "DIALOGUE" | "ACTION" | "MONTAGE" | "CUSTOM";

export type ShotGroupScene = { id: string; code: string; title: string; revision: number };
export type ShotGroupShot = {
  id: string; code: string; shot_type: string; target_duration_ms: number; status: string;
  order_key: string; scene_id: string | null; group_id: string | null; archived_at?: string | null; revision: number;
};
export type ShotGroup = {
  id: string; episode_id: string; scene_id: string | null; kind: ShotGroupKind; code: string;
  title: string; order_key: string; metadata: Record<string, unknown>; status: "ACTIVE" | "ARCHIVED";
  revision: number; members: Array<{ shot_id: string; order_key: string }>;
};
export type ShotGroupWorkspace = {
  episode: { id: string; code: string; title: string; project_id: string };
  scenes: ShotGroupScene[]; shots: ShotGroupShot[]; groups: ShotGroup[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return generatedRequestJson<T>(`/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
}

export async function getShotGroupWorkspace(episodeId: string) {
  return (await request<{ workspace: ShotGroupWorkspace }>(`/episodes/${episodeId}/shot-groups`)).workspace;
}

export async function createShotGroup(episodeId: string, input: {
  kind: ShotGroupKind; code: string; title: string; scene_id: string | null;
}) {
  return request(`/episodes/${episodeId}/shot-groups`, { method: "POST", body: JSON.stringify({ ...input, metadata: {} }) });
}

export async function assignShotScene(shot: ShotGroupShot, sceneId: string | null) {
  return request(`/shots/${shot.id}:assign-scene`, {
    method: "POST", body: JSON.stringify({ scene_id: sceneId, expected_revision: shot.revision }),
  });
}

export async function replaceShotGroupMembers(group: ShotGroup, shotIds: string[]) {
  return request(`/shot-groups/${group.id}/members`, {
    method: "PUT", body: JSON.stringify({ shot_ids: shotIds, expected_revision: group.revision }),
  });
}

export async function archiveShotGroup(group: ShotGroup) {
  return request(`/shot-groups/${group.id}:archive`, {
    method: "POST", body: JSON.stringify({ expected_revision: group.revision }),
  });
}

export async function reorderShotGroups(episodeId: string, groups: ShotGroup[]) {
  return request(`/episodes/${episodeId}/shot-groups:reorder`, {
    method: "POST",
    body: JSON.stringify({ items: groups.map((group, index) => ({
      group_id: group.id, order_key: String(index + 1).padStart(4, "0"), expected_revision: group.revision,
    })) }),
  });
}
import { requestJson as generatedRequestJson } from "../../generated/api";
