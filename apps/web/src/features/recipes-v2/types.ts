export type DirectorRecipeDocument = {
  aspect_ratio: string;
  shot_planning: { avg_duration_ms: number; dialogue_coverage: string };
  asset_policy: { character_required_refs: string[] };
  generation: { image: { capability: string }; video: { capability: string } };
  qc_policy_ref: { policy_version_id: string };
};

export type DirectorRecipeVersion = {
  id: string;
  recipe_id: string;
  version_no: number;
  recipe: DirectorRecipeDocument;
  recipe_hash: string;
  reason: string;
  is_frozen: boolean;
  created_at: string;
  created_by: string;
};

export type DirectorRecipe = {
  id: string;
  project_id: string;
  code: string;
  title: string;
  status: string;
  revision: number;
  versions: DirectorRecipeVersion[];
};

export type DirectorRecipeBinding = DirectorRecipeVersion & {
  project_id: string;
  recipe_version_id: string;
  code: string;
  title: string;
  revision: number;
};

export type QcPolicyVersionOption = { policy_version_id: string; owner_type: string; stage: string; version_no: number };

