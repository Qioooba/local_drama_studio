export type AssetProposal = {
  id: string; name: string; kind: string; status: string; revision: number;
  evidence: {
    scene_count?: number; source?: string; run_id?: string; description?: string; introduction?: string;
    biography?: string; role?: string; appearance?: string; personality?: string; motivation?: string; arc?: string;
    geography?: string; architecture?: string; layout?: string; atmosphere?: string; lighting?: string; story_function?: string;
    material?: string; function?: string; story_significance?: string; owner?: string; rules?: string; visual_prompt?: string;
  };
  suggested_asset_id: string | null; suggested_asset_code: string | null; suggested_asset_name: string | null;
  resolved_asset_id: string | null;
  name_quality?: { valid: boolean; reason?: string | null };
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return generatedRequestJson<T>(`/api/v1${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
}

export async function listAssetProposals(projectId: string) {
  return (await request<{ items: AssetProposal[] }>(`/projects/${projectId}/asset-proposals`)).items;
}

export async function decideAssetProposal(
  proposal: AssetProposal, action: "CREATE_NEW" | "MERGE_EXISTING" | "REJECT",
  options: { targetAssetId?: string; newAssetCode?: string; note?: string } = {},
) {
  return request(`/asset-proposals/${proposal.id}:decide`, {
    method: "POST", body: JSON.stringify({
      action, expected_revision: proposal.revision, target_asset_id: options.targetAssetId,
      new_asset_code: options.newAssetCode, decision_note: options.note ?? "",
    }),
  });
}
import { requestJson as generatedRequestJson } from "../../generated/api";
