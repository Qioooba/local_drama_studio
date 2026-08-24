export type DirectorDeskShotNavItem = {
  id: string;
  code: string;
  order_key: string;
  scene_id: string | null;
  scene_code: string | null;
  scene_title: string | null;
  group_id: string | null;
  group_code: string | null;
  group_title: string | null;
  thumbnail_media_version_id: string | null;
  current_video_media_version_id: string | null;
  status: string;
  continuity_status: string;
  job_status: string | null;
};

export type DirectorDeskCandidate = {
  id: string;
  intent_id: string;
  variant_no: number;
  variant_type: string;
  parent_variant_id: string | null;
  branch_reason: string;
  seed_policy?: string;
  explicit_seed?: number | null;
  capability_profile_version_id?: string | null;
  status: string;
  is_stale: boolean;
  stale_reason: string | null;
  media_asset_id: string | null;
  media_kind: string | null;
  media_version_id: string;
  version_no: number | null;
  take_no: number | null;
  stage: string | null;
  rel_path: string | null;
  mime_type: string | null;
  duration_ms: number | null;
  integrity_status: string | null;
  selected: boolean;
  approved: boolean;
  created_at: string | null;
};

export type DirectorDeskFrameAnchor = {
  anchor_id: string;
  inherited_from_anchor_id: string | null;
  source_media_version_id: string;
  media_version_id: string;
  rel_path: string | null;
  role_hint: string | null;
  source: "INHERITED" | "EXPLICIT" | "GENERATED" | "EXTRACTED";
  status: "AUTO_INHERITED" | "EXPLICIT" | "GENERATED" | "LOCKED" | "STALE" | "CONFLICT" | "MISSING";
  stale: boolean;
  stale_reason: string | null;
};

export type DirectorDeskBoundary = {
  transition_id: string;
  boundary_revision: number;
  from_shot_id: string;
  from_shot_code: string;
  to_shot_id: string;
  to_shot_code: string;
  enforcement: string;
  compatibility: string;
  stale: boolean;
  stale_reason: string | null;
  previous_end: DirectorDeskFrameAnchor | null;
  current_start: DirectorDeskFrameAnchor | null;
  inheritance_recommended?: boolean;
  inheritance_reason?: string;
};

export type DirectorDeskResponse = {
  project: { id: string; code: string; name: string; aspect_ratio: string | null };
  episode: { id: string; code: string; title: string | null; status: string; shot_count: number; approved_count: number; blocked_count: number };
  shot_nav: {
    items: DirectorDeskShotNavItem[];
    total: number;
    selected_index: number;
    window_start: number;
    window_end: number;
    has_previous: boolean;
    has_next: boolean;
  };
  current_shot: {
    shot: { id: string; code: string; order_key: string; target_duration_ms: number; shot_type: string | null; status: string; revision: number; scene_id: string | null; scene_code: string | null; scene_title: string | null; group_id: string | null; group_code: string | null; group_title: string | null };
    current_revision: { id: string; revision_no: number; is_frozen: boolean; fields: Record<string, unknown> } | null;
    source_context: { scene_id?: string | null; source_range?: Record<string, unknown> | null; source_text?: string | null };
    intent_suggestions?: {
      environment: { value: string; source_label: string; source_kind: "SCENE"; source_revision: string; stale: boolean; stale_reason: string | null } | null;
      continuity: { value: string | null; source_label: string | null; source_kind: "PREVIOUS_SHOT"; eligible: boolean; reason: string | null; source_revision: string; stale: boolean; stale_reason: string | null } | null;
      script: { subject_action: string; creative_intent: string; dialogue: unknown; source_label: string; source_kind: "APPLIED_BREAKDOWN_DRAFT"; source_revision_id: string | null; source_fingerprint: string; stale: boolean; stale_reason: string | null } | null;
    };
    assets: Array<Record<string, unknown>>;
    asset_states: Array<Record<string, unknown>>;
    selected_variant: { id: string; intent_id: string; variant_no: number; status: string; is_stale: boolean; media_version_id: string | null } | null;
    current_media: (Partial<DirectorDeskCandidate> & { media_version_id: string }) | null;
    candidates: DirectorDeskCandidate[];
    frame_bridge: {
      previous: DirectorDeskBoundary | null;
      current_start: DirectorDeskFrameAnchor | null;
      current_end: DirectorDeskFrameAnchor | null;
      next: DirectorDeskBoundary | null;
      compatibility: string;
      stale: boolean;
    };
    qc_summary: Record<string, unknown>;
    review_summary: { subject_id?: string | null; count?: number; latest?: Record<string, unknown> | null };
    generation_preferences: Record<string, unknown>;
    active_jobs: Array<Record<string, unknown>>;
    blockers: Array<{ code: string; message: string; scope: string; blocking: boolean }>;
  };
  permissions: { can_edit: boolean; can_generate: boolean; can_approve: boolean };
  read_only: boolean;
  request_shape: "bounded_director_desk_read_model";
};

export type RerollReasonCode =
  | "USER_REROLL"
  | "FACE_FIX"
  | "IDENTITY_FIX"
  | "COMPOSITION_FIX"
  | "MOTION_FIX"
  | "CONTINUITY_FIX"
  | "FRAME_BRIDGE_FIX"
  | "MODEL_COMPARE"
  | "PROMPT_TUNE"
  | "QC_AUTO_RETRY"
  | "OTHER";

export type RerollResult = {
  variant: { id: string; status: string; variant_no?: number };
  job: { id: string; state: string };
  reroll: { retry: false; parent_variant_id: string };
};
