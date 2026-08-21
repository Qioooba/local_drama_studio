export type QcOwnerType = "PROJECT" | "EPISODE" | "SHOT";
export type QcStage = "IMAGE" | "VIDEO" | "AUDIO" | "CONTINUITY" | "DELIVERY";
export type QcCategory = "VISUAL" | "FACE" | "IDENTITY" | "COMPOSITION" | "MOTION" | "CONTINUITY" | "AUDIO" | "TECHNICAL";

export type QcPolicyDocument = {
  checks?: QcCategory[];
  thresholds?: Partial<Record<QcCategory, number>>;
  attention_selection?: "REQUIRE_CONFIRMATION";
  [key: string]: unknown;
};

export type QcPolicy = {
  policy_set_id: string;
  project_id: string;
  owner_type: QcOwnerType;
  owner_id: string;
  stage: QcStage;
  status: string;
  revision: number;
  policy_version_id: string;
  version_no: number;
  policy: QcPolicyDocument;
  max_auto_rerolls: number;
  auto_reroll_categories: QcCategory[];
  is_frozen: boolean;
  reason: string;
  created_at: string;
  created_by: string;
};

export type QcPolicyResolution = QcPolicy & { source: QcOwnerType };
export type QcPolicyPut = {
  owner_type: QcOwnerType;
  owner_id: string;
  stage: QcStage;
  policy: QcPolicyDocument;
  max_auto_rerolls: number;
  auto_reroll_categories: QcCategory[];
  reason: string;
  expected_revision: number | null;
};
export type QcScopeOption = { id: string; code: string; title?: string };

