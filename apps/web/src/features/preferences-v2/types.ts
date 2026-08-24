export type PreferenceOwnerType = "PROJECT" | "EPISODE" | "SHOT";
export type PreferenceMode = "AUTO" | "EXPLICIT";

export type GenerationPreference = {
  preference_set_id: string;
  project_id: string;
  owner_type: PreferenceOwnerType;
  owner_id: string;
  capability: string;
  status: string;
  revision: number;
  current_version_id: string;
  version_no: number;
  execution_profile_version_id: string | null;
  resolution_mode: PreferenceMode;
  settings: Record<string, unknown>;
  reason: string;
  is_frozen: boolean;
  created_at: string;
  created_by: string;
};

export type GenerationResolution = {
  capability: string;
  profile_version_id: string | null;
  source: PreferenceOwnerType | "AUTO";
  native_support: boolean;
  fallback_support: boolean;
  warnings: string[];
  estimated_resources: Record<string, unknown>;
  blocked_reason: string | null;
  preference: GenerationPreference | null;
  profile: {
    code: string;
    title: string;
    version_no: number;
    capability: string;
    status: string;
    resources: Record<string, unknown>;
    override_schema?: Record<string, unknown>;
    model_bundle?: Record<string, unknown>;
  } | null;
  effective_settings?: Record<string, unknown>;
  setting_sources?: Record<string, string>;
  recommendation: {
    selection_reason: "AUTO_NEWEST_PUBLISHED_EXACT_CAPABILITY" | "EXPLICIT_PUBLISHED_VERSION";
    facts: {
      capability_exact_match: boolean;
      published: boolean;
      native_support: boolean;
      resources: Record<string, unknown>;
    };
    local_success_rate: {
      status: "AVAILABLE" | "UNKNOWN";
      reason: "SCHEMA_UNAVAILABLE" | "NO_COMPARABLE_DIMENSION_HISTORY" | "INSUFFICIENT_SAME_DIMENSION_SAMPLES" | null;
      value: number | null;
      successful_sample_count: number;
      terminal_sample_count: number;
      minimum_sample_count: number;
      dimensions: Record<string, number | string | null> | null;
      evidence: { source: string; candidate_count: number; candidate_limit: number; gpu_hardware_model_known: boolean };
    };
  } | null;
};

export type ProjectOption = { id: string; code: string; title: string };
export type SeasonOption = { id: string; code: string; title: string };
export type EpisodeOption = { id: string; code: string; title: string; production_status?: string };
export type ShotOption = { id: string; code: string; shot_type?: string; status?: string };
export type ProfileOption = {
  id: string;
  code: string;
  title: string;
  version_id: string;
  version_no?: number;
  capability: string;
  status: string;
  override_schema?: Record<string, unknown>;
  model_bundle?: Record<string, unknown>;
};

export type PreferencePutPayload = {
  owner_type: PreferenceOwnerType;
  owner_id: string;
  capability: string;
  resolution_mode: PreferenceMode;
  execution_profile_version_id: string | null;
  settings: Record<string, unknown>;
  reason: string;
  expected_revision: number | null;
};

export type ApiFailure = {
  status: number;
  code: string;
  message: string;
  requestId: string | null;
  details: Record<string, unknown>;
  suggestedAction: string | null;
};
