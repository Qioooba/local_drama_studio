from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from local_drama.api.schemas.director_desk import (
    DirectorDeskEpisode,
    DirectorDeskProject,
    FrameBridgeProjection,
    ShotNavigatorWindow,
)
from local_drama.api.schemas.projects import DirectorIntentV3
from local_drama.api.schemas.variants import RerollReasonCode
from local_drama.domain.generation import VariantPlan
from local_drama.domain.policies import VariantInput

JsonObject = dict[str, JsonValue]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShotDraftRequest(StrictModel):
    fields: DirectorIntentV3
    freeze: bool = False
    expected_revision_no: int | None = Field(default=None, ge=1)


class ShotMarkReadyRequest(StrictModel):
    draft: DirectorIntentV3 | None = None
    freeze: bool = False
    expected_revision_no: int | None = Field(default=None, ge=1)


class ShotRevisionWrite(StrictModel):
    id: str
    shot_id: str
    revision_no: int
    fields: JsonObject
    is_frozen: bool


class ShotWriteFact(StrictModel):
    id: str
    status: str
    current_revision_id: str | None = None
    revision: int
    updated_at: str


class ShotDraftResponse(StrictModel):
    shot_revision: ShotRevisionWrite
    shot: ShotWriteFact


class ShotWorkingAdoption(StrictModel):
    id: str
    shot_id: str
    media_asset_id: str
    media_version_id: str
    slot_type: Literal["KEYFRAME", "VIDEO"]
    selection_type: Literal["KEYFRAME", "PROXY_WINNER"]
    status: Literal["ADOPTED"]
    replayed: bool


class ShotWorkingAdoptionResponse(StrictModel):
    adoption: ShotWorkingAdoption


class ShotGenerationIntentRequest(StrictModel):
    purpose: Literal["T2I", "I2V_PROXY", "T2V", "R2V"]
    creative_goal: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ShotGenerationIntentFact(StrictModel):
    id: str
    project_id: str
    owner_type: Literal["SHOT"]
    owner_id: str
    purpose: str
    creative_goal: str
    status: str
    created_at: str
    idempotent_replay: bool = False


class ShotGenerationIntentResponse(StrictModel):
    intent: ShotGenerationIntentFact


class ShotGenerationBindingRequest(StrictModel):
    role: str = Field(min_length=1, max_length=40)
    media_version_id: str = Field(min_length=1, max_length=64)
    ordinal: int = Field(default=0, ge=0, le=1000)
    weight: float | None = Field(default=None, ge=0.0, le=1.0)


class ShotBaseGenerationPreflightRequest(StrictModel):
    operation: Literal["BASE"]
    stage_code: Literal["SHOT_IMAGE", "VIDEO"]
    intent_id: str = Field(min_length=1, max_length=64)
    variant_type: Literal["BASE"] = "BASE"
    parent_variant_id: None = None
    branch_reason: str = Field(min_length=1, max_length=1000)
    prompt_revision_id: str | None = Field(default=None, min_length=1, max_length=64)
    profile_version_id: str = Field(min_length=1, max_length=64)
    parameter_set: JsonObject = Field(default_factory=dict)
    seed_policy: Literal["EXPLICIT", "PROVIDER_RANDOM"]
    explicit_seed: int | None = None
    provider_random_nonce: str | None = Field(default=None, min_length=36, max_length=36)
    expected_effective_configuration_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    bindings: list[ShotGenerationBindingRequest] = Field(default_factory=list, max_length=100)
    expected_shot_revision: int = Field(ge=1)

    def to_domain(self) -> VariantPlan:
        return VariantPlan(
            variant_type="BASE",
            parent_variant_id=None,
            branch_reason=self.branch_reason,
            prompt_revision_id=self.prompt_revision_id,
            profile_version_id=self.profile_version_id,
            parameter_set=self.parameter_set,
            seed_policy=self.seed_policy,
            explicit_seed=self.explicit_seed,
            bindings=tuple(VariantInput(item.role, item.media_version_id, item.ordinal, item.weight) for item in self.bindings),
            provider_random_nonce=self.provider_random_nonce,
            expected_effective_configuration_fingerprint=self.expected_effective_configuration_fingerprint,
        )


class ShotBaseGenerationSubmitRequest(ShotBaseGenerationPreflightRequest):
    plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)


class ShotRerollGenerationRequest(StrictModel):
    operation: Literal["REROLL"]
    stage_code: Literal["SHOT_IMAGE", "VIDEO"]
    parent_variant_id: str = Field(min_length=1, max_length=64)
    reason_code: RerollReasonCode
    reason_note: str | None = Field(default=None, max_length=900)
    explicit_seed: int | None = None
    profile_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=200)


ShotGenerationRequest = Annotated[
    ShotBaseGenerationSubmitRequest | ShotRerollGenerationRequest,
    Field(discriminator="operation"),
]


class ShotGenerationPreflightFact(StrictModel):
    shot_id: str
    shot_revision: int
    intent_id: str
    status: Literal["READY", "BLOCKED"]
    plan_hash: str
    variant_plan_hash: str
    recipe_hash: str
    dependencies: JsonObject
    resource_estimate: JsonObject
    disk_gate: JsonObject
    blockers: list[JsonObject]
    effective_configuration: JsonObject
    would_persist_variant: Literal[False]
    would_create_job: Literal[False]
    reproducibility: JsonObject
    style_context: JsonObject | None = None


class ShotGenerationPreflightResponse(StrictModel):
    preflight: ShotGenerationPreflightFact


class ShotGenerationVariantWrite(StrictModel):
    id: str
    intent_id: str
    variant_no: int
    status: str


class ShotGenerationJobWrite(StrictModel):
    id: str
    state: str


class ShotGenerationRerollFact(StrictModel):
    retry: Literal[False]
    parent_variant_id: str


class ShotBaseGenerationResponse(StrictModel):
    operation: Literal["BASE"]
    variant: ShotGenerationVariantWrite
    job: ShotGenerationJobWrite
    idempotent_replay: bool


class ShotRerollGenerationResponse(StrictModel):
    operation: Literal["REROLL"]
    variant: ShotGenerationVariantWrite
    job: ShotGenerationJobWrite
    reroll: ShotGenerationRerollFact


ShotGenerationResponse = Annotated[
    ShotBaseGenerationResponse | ShotRerollGenerationResponse,
    Field(discriminator="operation"),
]


class ShotFact(StrictModel):
    id: str
    code: str
    order_key: str
    target_duration_ms: int
    shot_type: str | None = None
    status: str
    revision: int
    scene_id: str | None = None
    scene_code: str | None = None
    scene_title: str | None = None
    group_id: str | None = None
    group_code: str | None = None
    group_title: str | None = None


class ShotRevisionFact(StrictModel):
    id: str
    revision_no: int
    is_frozen: bool
    fields: JsonObject


class SourceRangeFact(StrictModel):
    scene_id: str
    scene_code: str
    scene_title: str
    location: str | None = None
    time_of_day: str | None = None
    scene_revision: int
    ordinal: int | None = None
    source_start: int | None = None
    source_end: int | None = None
    source_label: str | None = None


class SourceContextFact(StrictModel):
    scene_id: str | None = None
    source_range: SourceRangeFact | None = None
    source_text: str | None = None


class EnvironmentSuggestion(StrictModel):
    value: str
    source_label: str
    source_kind: Literal["SCENE"]
    source_revision: str
    stale: bool
    stale_reason: str | None = None


class ContinuitySuggestion(StrictModel):
    value: str | None = None
    source_label: str | None = None
    source_kind: Literal["PREVIOUS_SHOT"]
    eligible: bool
    reason: str | None = None
    source_revision: str
    stale: bool
    stale_reason: str | None = None


class ScriptSuggestion(StrictModel):
    subject_action: str
    creative_intent: str
    dialogue: JsonValue = None
    source_label: str
    source_kind: Literal["APPLIED_BREAKDOWN_DRAFT"]
    source_revision_id: str | None = None
    source_fingerprint: str
    stale: bool
    stale_reason: str | None = None


class IntentSuggestions(StrictModel):
    environment: EnvironmentSuggestion | None = None
    continuity: ContinuitySuggestion | None = None
    script: ScriptSuggestion | None = None


class AssetFact(StrictModel):
    binding_id: str
    role_in_shot: str
    asset_state_id: str | None = None
    id: str
    kind: str
    code: str
    name: str
    description: str
    canonical_media_version_id: str | None = None
    status: str
    effective_state_id: str | None = None


class AssetStateFact(StrictModel):
    id: str
    story_asset_id: str
    code: str
    label: str
    state_kind: str
    description: str
    status: str
    revision: int
    state: JsonObject


class MediaProjection(StrictModel):
    id: str | None = None
    intent_id: str | None = None
    variant_no: int | None = None
    variant_type: str | None = None
    parent_variant_id: str | None = None
    branch_reason: str | None = None
    seed_policy: str | None = None
    explicit_seed: int | None = None
    capability_profile_version_id: str | None = None
    status: str | None = None
    is_stale: bool = False
    stale_reason: str | None = None
    purpose: str | None = None
    media_asset_id: str | None = None
    media_kind: str | None = None
    media_version_id: str
    version_no: int | None = None
    take_no: int | None = None
    stage: str | None = None
    rel_path: str | None = None
    mime_type: str | None = None
    duration_ms: int | None = None
    integrity_status: str | None = None
    thumbnail_ready: bool = False
    selected: bool = False
    approved: bool = False
    created_at: str | None = None


class CandidateFact(MediaProjection):
    id: str
    intent_id: str
    variant_no: int
    variant_type: str
    branch_reason: str
    status: str


class SelectedVariantFact(StrictModel):
    id: str
    intent_id: str
    variant_no: int
    status: str
    is_stale: bool
    media_version_id: str | None = None


class ReviewDecisionFact(StrictModel):
    id: str
    decision: str
    subject_revision: int
    is_stale: bool
    stale_reason: str | None = None
    comment: str | None = None
    created_at: str
    created_by: str


class ReviewSummary(StrictModel):
    subject_id: str | None = None
    count: int
    latest: ReviewDecisionFact | None = None


class MachineCheckRunFact(StrictModel):
    id: str
    policy_version: str
    status: str
    created_at: str
    updated_at: str


class MachineCheckResultFact(StrictModel):
    item_id: str
    result: str
    details: JsonObject


class QcSummary(StrictModel):
    subject_id: str | None = None
    latest_run: MachineCheckRunFact | None = None
    results: list[MachineCheckResultFact]


class GenerationProfileFact(StrictModel):
    code: str
    title: str
    version_no: int
    capability: str
    status: str
    resources: JsonObject
    capability_contract: JsonObject
    override_schema: JsonObject | None = None
    model_bundle: JsonObject | None = None


class GenerationResolutionFact(StrictModel):
    capability: str
    profile_version_id: str | None = None
    source: str
    native_support: bool
    fallback_support: bool
    warnings: list[str]
    estimated_resources: JsonObject
    blocked_reason: str | None = None
    preference: JsonObject | None = None
    profile: GenerationProfileFact | None = None
    effective_settings: JsonObject | None = None
    setting_sources: dict[str, str] | None = None
    recommendation: JsonObject | None = None
    resolution_fingerprint: str


class GenerationPreferences(StrictModel):
    resolutions: list[GenerationResolutionFact]
    available: bool


class CapabilityOption(StrictModel):
    id: str
    version_id: str
    code: str
    title: str
    version_no: int | None = None
    capability: str
    capability_contract: JsonObject
    source: str
    status: Literal["PUBLISHED", "BLOCKED"]
    blocked_reason: str | None = None


class ActiveJobFact(StrictModel):
    id: str
    type: str
    subject_type: str
    subject_id: str
    state: str
    channel: str
    priority: int
    progress_updated_at: str | None = None
    started_at: str | None = None
    last_error_code: str | None = None
    last_error_detail_redacted: str | None = None
    created_at: str
    updated_at: str
    progress: JsonObject


class StudioBlocker(StrictModel):
    code: str
    message: str
    scope: str
    blocking: bool


class FrameBridgeCommandBase(StrictModel):
    expected_boundary_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class FrameBridgeInheritCommand(FrameBridgeCommandBase):
    source_anchor_id: str | None = Field(default=None, min_length=1)
    lock: bool | None = None


class FrameBridgeCurrentFrameCommand(FrameBridgeCommandBase):
    media_version_id: str | None = Field(default=None, min_length=1)
    frame_anchor_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def exactly_one_source(self) -> "FrameBridgeCurrentFrameCommand":
        if (self.media_version_id is None) == (self.frame_anchor_id is None):
            raise ValueError("必须且只能提供 media_version_id 或 frame_anchor_id")
        return self


class FrameBridgeSourceFrameCommand(FrameBridgeCommandBase):
    frame_anchor_id: str = Field(min_length=1)


class FrameBridgeLockCommand(FrameBridgeCommandBase):
    locked: bool


class FrameBridgeWriteFact(StrictModel):
    id: str
    from_shot_id: str
    to_shot_id: str
    constraint_type: str
    from_anchor_id: str | None = None
    to_anchor_id: str | None = None
    enforcement: str
    compatibility_status: str
    note: str | None = None
    created_at: str
    updated_at: str
    created_by: str
    revision: int
    schema_version: str
    boundary_revision: int
    is_stale: bool
    stale_reason: str | None = None
    locked: bool
    inherited_from_anchor_id: str | None = None
    idempotent_replay: bool


class FrameBridgeWriteResponse(StrictModel):
    frame_bridge: FrameBridgeWriteFact


class DialogueTextRevisionFact(StrictModel):
    id: str
    revision_no: int
    text: str
    pronunciation: JsonObject
    text_hash: str
    created_at: str


class DialogueVoiceBindingFact(StrictModel):
    character_asset_id: str
    character_code: str
    character_name: str
    voice_profile_version_id: str
    voice_code: str
    voice_title: str
    voice_ref: str
    provider_profile_version_id: str | None = None


class DialogueTtsCandidateFact(StrictModel):
    id: str
    dialogue_text_revision_id: str
    voice_profile_version_id: str
    media_version_id: str
    emotion: str
    speech_rate: float
    seed: int | None = None
    model_ref: str
    candidate_kind: str
    status: str
    created_at: str
    is_stale: bool
    selected: bool


class DialogueWorkingSelectionFact(StrictModel):
    id: str
    tts_candidate_id: str
    media_version_id: str
    source_text_revision_id: str
    is_stale: bool
    created_at: str


class ShotDialogueLineFact(StrictModel):
    id: str
    code: str
    speaker: str
    revision: int
    current_text: DialogueTextRevisionFact
    voice_binding: DialogueVoiceBindingFact | None = None
    candidates: list[DialogueTtsCandidateFact]
    working_selection: DialogueWorkingSelectionFact | None = None


class ShotDialogueProjection(StrictModel):
    lines: list[ShotDialogueLineFact]
    total: int


class ShotDialogueDraftCommand(StrictModel):
    line_id: str | None = Field(default=None, min_length=1)
    code: str = Field(min_length=1, max_length=120)
    speaker: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)
    pronunciation: JsonObject = Field(default_factory=dict)
    expected_shot_revision: int = Field(ge=1)
    expected_text_revision_no: int | None = Field(default=None, ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def revision_required_for_existing_line(self) -> "ShotDialogueDraftCommand":
        if (self.line_id is None) != (self.expected_text_revision_no is None):
            raise ValueError("更新已有对白时必须提供 expected_text_revision_no；新建对白时不得提供")
        return self


class ShotDialogueDraftWriteFact(StrictModel):
    shot_id: str
    line_id: str
    code: str
    speaker: str
    line_revision: int
    text_revision: DialogueTextRevisionFact
    idempotent_replay: bool


class ShotDialogueDraftResponse(StrictModel):
    dialogue: ShotDialogueDraftWriteFact


class DialogueTtsGenerationCommand(StrictModel):
    expected_text_revision_no: int = Field(ge=1)
    voice_profile_version_id: str = Field(min_length=1)
    emotion: str = Field(min_length=1, max_length=120)
    speech_rate: float = Field(ge=0.5, le=2.0)
    idempotency_key: str = Field(min_length=1, max_length=200)


class DialogueTtsGenerationJobFact(StrictModel):
    id: str
    state: str
    subject_kind: str
    scope_project_id: str
    scope_episode_id: str
    scope_shot_id: str
    stage_code: Literal["AUDIO_SUBTITLE"]
    idempotent_replay: bool


class DialogueTtsGenerationResponse(StrictModel):
    line_id: str
    text_revision_id: str
    text_revision_no: int
    job: DialogueTtsGenerationJobFact


class AudioWorkingAdoptionCommand(StrictModel):
    expected_text_revision_no: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class AudioWorkingAdoptionFact(StrictModel):
    id: str
    dialogue_line_id: str
    tts_candidate_id: str
    media_version_id: str
    source_text_revision_id: str
    status: Literal["ADOPTED"]
    idempotent_replay: bool


class AudioWorkingAdoptionResponse(StrictModel):
    adoption: AudioWorkingAdoptionFact


class ContinuityRevisionFact(StrictModel):
    id: str | None = None
    revision_no: int | None = None
    is_frozen: bool


class ContinuityReferenceFact(StrictModel):
    media_asset_id: str
    media_version_id: str
    purpose: str
    media_kind: str
    selection_state: Literal["APPROVED", "SELECTED"]
    version_no: int
    stage: str
    integrity_status: str


class ContinuityShotFact(StrictModel):
    position: Literal["previous", "current", "next"]
    id: str
    code: str
    order_key: str
    status: str
    target_duration_ms: int
    revision: ContinuityRevisionFact
    facets: dict[str, JsonValue | None]
    missing_facets: list[str]
    references: list[ContinuityReferenceFact]


class ContinuityShotWindow(StrictModel):
    previous: ContinuityShotFact | None = None
    current: ContinuityShotFact
    next: ContinuityShotFact | None = None


class ContinuityTransitionFact(StrictModel):
    id: str
    from_shot_id: str
    to_shot_id: str
    constraint_type: str
    enforcement: str
    compatibility_status: str
    is_stale: bool
    stale_reason: str | None = None
    from_anchor_id: str | None = None
    to_anchor_id: str | None = None
    boundary_revision: int


class ShotContinuityContext(StrictModel):
    episode_id: str
    selected_shot_id: str
    shots: ContinuityShotWindow
    transitions: list[ContinuityTransitionFact]
    read_only: Literal[True]
    runtime_contacted: Literal[False]
    network_contacted: Literal[False]
    mutated: Literal[False]
    request_shape: Literal["shot_continuity_context_v2"]


class ShotContinuityContextResponse(StrictModel):
    continuity: ShotContinuityContext


class CurrentShotAggregate(StrictModel):
    shot: ShotFact
    current_revision: ShotRevisionFact | None = None
    source_context: SourceContextFact
    intent_suggestions: IntentSuggestions
    assets: list[AssetFact]
    asset_states: list[AssetStateFact]
    selected_variant: SelectedVariantFact | None = None
    current_media: MediaProjection | None = None
    candidates: list[CandidateFact]
    frame_bridge: FrameBridgeProjection
    dialogue: ShotDialogueProjection
    qc_summary: QcSummary
    review_summary: ReviewSummary
    generation_preferences: GenerationPreferences
    generation_intents: list[ShotGenerationIntentFact]
    capability_options: list[CapabilityOption]
    active_jobs: list[ActiveJobFact]
    blockers: list[StudioBlocker]


class AllowedActions(StrictModel):
    edit_draft: bool
    mark_ready: bool
    generate: bool
    adopt_working_version: bool
    write_review_decision: Literal[False]


class ReviewHandoff(StrictModel):
    subject_type: Literal["MEDIA_VERSION"] | None = None
    subject_id: str | None = None
    route_kind: Literal["REVIEW"]
    write_owner: Literal["REVIEW_WORKSPACE"]


class ShotStudioResponse(StrictModel):
    project: DirectorDeskProject
    episode: DirectorDeskEpisode
    shot_nav: ShotNavigatorWindow
    current_shot: CurrentShotAggregate
    allowed_actions: AllowedActions
    review_handoff: ReviewHandoff
    read_only: Literal[True]
    runtime_contacted: Literal[False]
    network_contacted: Literal[False]
    mutated: Literal[False]
    request_shape: Literal["bounded_shot_studio_v2"]
