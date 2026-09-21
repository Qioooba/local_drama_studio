from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator


class VideoUpscaleResponse(RootModel[dict[str, Any]]):
    """Named JSON response contract for versioned video-upscale projections."""


class UpscaleTargetOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["FOLLOW_ORIENTATION_1080", "CUSTOM"] = "FOLLOW_ORIENTATION_1080"
    width: int | None = Field(default=None, ge=64, le=4096)
    height: int | None = Field(default=None, ge=64, le=4096)
    fit: Literal["CONTAIN", "COVER"] = "CONTAIN"
    allow_cross_orientation: bool = False

    @model_validator(mode="after")
    def validate_custom_size(self) -> "UpscaleTargetOptions":
        if self.mode == "CUSTOM" and (self.width is None or self.height is None):
            raise ValueError("自定义目标必须同时填写宽和高")
        if self.mode != "CUSTOM" and (self.width is not None or self.height is not None):
            raise ValueError("跟随方向模式不接受自定义宽高")
        if self.width is not None and self.width % 2:
            raise ValueError("目标宽度必须是偶数")
        if self.height is not None and self.height % 2:
            raise ValueError("目标高度必须是偶数")
        return self


class UpscalePipelineOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: UpscaleTargetOptions = Field(default_factory=UpscaleTargetOptions)
    scale_policy: Literal["AUTO_NATIVE", "EXPLICIT_NATIVE"] = "AUTO_NATIVE"
    native_scale: Literal[2, 3, 4] | None = None
    fps_policy: Literal["PRESERVE_CFR"] = "PRESERVE_CFR"
    audio_policy: Literal["COPY_IF_COMPATIBLE_ELSE_AAC", "COPY_STRICT"] = "COPY_IF_COMPATIBLE_ELSE_AAC"
    aac_bitrate_kbps: Literal[128, 192, 256, 320] = 192
    subtitle_policy: Literal["INHERIT"] = "INHERIT"
    watermark_policy: Literal["INHERIT_SOURCE_STATE"] = "INHERIT_SOURCE_STATE"
    encoder: Literal["libx264", "h264_nvenc"] = "libx264"
    crf: int = Field(default=18, ge=14, le=28)
    preset: Literal["ultrafast", "veryfast", "medium", "slow"] = "veryfast"
    container: Literal["mp4"] = "mp4"
    pix_fmt: Literal["yuv420p"] = "yuv420p"
    source_policy: Literal["PREFER_FINAL_DELIVERY", "APPROVED_COMPOSE"] = "PREFER_FINAL_DELIVERY"
    existing_result_policy: Literal["REUSE_EQUIVALENT", "NEW_VARIANT"] = "REUSE_EQUIVALENT"
    chunk_frames: int = Field(default=240, ge=48, le=480)
    max_attempts: int = Field(default=2, ge=1, le=3)

    @model_validator(mode="after")
    def validate_native_scale(self) -> "UpscalePipelineOptions":
        if self.scale_policy == "EXPLICIT_NATIVE" and self.native_scale is None:
            raise ValueError("显式原生倍率策略必须选择倍率")
        if self.scale_policy == "AUTO_NATIVE" and self.native_scale is not None:
            raise ValueError("自动原生倍率策略不接受固定倍率")
        return self


class UpscaleModelOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter_code: Literal["ncnn.realesrgan.video.v1"] = "ncnn.realesrgan.video.v1"
    model_name: Literal["realesr-animevideov3", "realesrgan-x4plus-anime", "realesrgan-x4plus"]
    native_scales: list[Literal[2, 3, 4]] = Field(min_length=1, max_length=3)
    tile_size: int = Field(default=0, ge=0, le=1024)
    tile_fallback_sizes: list[int] = Field(default_factory=list, max_length=3)
    tta: bool = False
    load_threads: int = Field(default=1, ge=1, le=4)
    proc_threads: int = Field(default=1, ge=1, le=4)
    save_threads: int = Field(default=2, ge=1, le=4)

    @model_validator(mode="after")
    def validate_tile_and_scales(self) -> "UpscaleModelOptions":
        if self.tile_size not in {0, 64, 128, 256, 512, 1024}:
            raise ValueError("tile_size 必须为自动或已支持的2次幂")
        allowed_tiles = {64, 128, 256, 512}
        if not self.tile_fallback_sizes:
            ladder = [256, 128, 64] if self.tile_size == 0 else [512, 256, 128, 64]
            self.tile_fallback_sizes = [value for value in ladder if value < self.tile_size][:3]
            if self.tile_size == 0:
                self.tile_fallback_sizes = ladder
        if (
            any(value not in allowed_tiles for value in self.tile_fallback_sizes)
            or len(set(self.tile_fallback_sizes)) != len(self.tile_fallback_sizes)
            or self.tile_fallback_sizes != sorted(self.tile_fallback_sizes, reverse=True)
            or (self.tile_size > 0 and any(value >= self.tile_size for value in self.tile_fallback_sizes))
        ):
            raise ValueError("tile_fallback_sizes 必须是严格递减且小于初始 tile 的受支持值")
        if len(set(self.native_scales)) != len(self.native_scales):
            raise ValueError("native_scales 不可重复")
        return self


class ProjectUpscaleSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_version_id: str = Field(min_length=1, max_length=80)
    pipeline_overrides: dict[str, object] = Field(default_factory=dict)
    model_overrides: dict[str, object] = Field(default_factory=dict)
    expected_revision: int = Field(ge=0)


class VideoUpscaleSelectionResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["EXPLICIT", "ALL_ELIGIBLE"] = "EXPLICIT"
    episode_ids: list[str] = Field(default_factory=list, max_length=200)
    search: str | None = Field(default=None, max_length=200)
    season_id: str | None = Field(default=None, max_length=64)
    source_policy: Literal["PREFER_FINAL_DELIVERY", "APPROVED_COMPOSE"] = "PREFER_FINAL_DELIVERY"

    @model_validator(mode="after")
    def validate_selection(self) -> "VideoUpscaleSelectionResolveRequest":
        if self.mode == "EXPLICIT" and not self.episode_ids:
            raise ValueError("显式选择至少需要一集")
        if len(set(self.episode_ids)) != len(self.episode_ids):
            raise ValueError("episode_ids 不可重复")
        return self


class VideoUpscalePlanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["localdrama.video-upscale-request.v1"] = "localdrama.video-upscale-request.v1"
    selection_hash: str = Field(pattern="^[0-9a-f]{64}$")
    episode_ids: list[str] = Field(min_length=1, max_length=200)
    preset_version_id: str = Field(min_length=1, max_length=80)
    execution_profile_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    batch_pipeline_overrides: dict[str, object] = Field(default_factory=dict)
    batch_model_overrides: dict[str, object] = Field(default_factory=dict)
    item_overrides: list[dict[str, object]] = Field(default_factory=list, max_length=200)
    existing_result_policy: Literal["REUSE_EQUIVALENT", "NEW_VARIANT"] = "REUSE_EQUIVALENT"

    @model_validator(mode="after")
    def validate_episode_ids(self) -> "VideoUpscalePlanCreateRequest":
        if len(set(self.episode_ids)) != len(self.episode_ids):
            raise ValueError("episode_ids 不可重复")
        override_ids = [str(item.get("episode_id") or "") for item in self.item_overrides]
        if any(not item for item in override_ids) or len(set(override_ids)) != len(override_ids):
            raise ValueError("item_overrides 必须包含唯一的 episode_id")
        if any(item not in self.episode_ids for item in override_ids):
            raise ValueError("item_overrides 只能覆盖本次选择的分集")
        return self


class VideoUpscaleBatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=64)
    plan_hash: str = Field(pattern="^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=200)
    acknowledged_warning_ids: list[str] = Field(default_factory=list, max_length=400)


class VideoUpscalePreviewCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=64)
    plan_hash: str = Field(pattern="^[0-9a-f]{64}$")
    episode_id: str = Field(min_length=1, max_length=64)
    start_ms: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=5000, ge=1000, le=10000)
    acknowledged_warning_ids: list[str] = Field(default_factory=list, max_length=40)


class VideoUpscaleBatchControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "PAUSE_PENDING",
        "PAUSE_ALL",
        "RESUME",
        "RETRY_FAILED",
        "CANCEL_UNFINISHED",
    ]
    expected_revision: int = Field(ge=1)


class VideoUpscaleCleanupPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retention_days: int = Field(default=7, ge=1, le=365)


class VideoUpscaleCleanupCommitRequest(VideoUpscaleCleanupPlanRequest):
    eligible_before: str = Field(min_length=20, max_length=80)
    plan_hash: str = Field(pattern="^[0-9a-f]{64}$")


class VideoUpscalePresetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=64)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,79}$")
    title: str = Field(min_length=1, max_length=200)
    profile_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    pipeline_options: UpscalePipelineOptions
    model_options: UpscaleModelOptions


class VideoUpscalePresetVersionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    profile_version_id: str | None = Field(default=None, min_length=1, max_length=64)
    pipeline_options: UpscalePipelineOptions
    model_options: UpscaleModelOptions
    expected_current_version_id: str = Field(min_length=1, max_length=80)


class EpisodeDeliverySelectionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: str = Field(min_length=1, max_length=64)
    target_slot: str = Field(pattern=r"^[A-Za-z0-9_:-]{2,120}$")
    selected_render_id: str = Field(min_length=1, max_length=64)
    expected_selection_revision: int = Field(ge=0)


class EpisodeDeliverySelectionPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EpisodeDeliverySelectionItem] = Field(min_length=1, max_length=200)


class EpisodeDeliverySelectionCommitRequest(EpisodeDeliverySelectionPlanRequest):
    plan_hash: str = Field(pattern="^[0-9a-f]{64}$")


class DeliveryBuildBatchItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: str = Field(min_length=1, max_length=64)
    target_version_id: str = Field(min_length=1, max_length=64)


class DeliveryBuildBatchPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DeliveryBuildBatchItemRequest] = Field(min_length=1, max_length=200)
    brand_kit_id: str | None = Field(default=None, min_length=1, max_length=64)
    watermark_profile_id: str | None = Field(default=None, min_length=1, max_length=64)
    compliance_policy_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_unique_items(self) -> "DeliveryBuildBatchPlanRequest":
        keys = [(item.episode_id, item.target_version_id) for item in self.items]
        if len(set(keys)) != len(keys):
            raise ValueError("items 不可包含重复的分集与交付目标")
        return self


class DeliveryBuildBatchSubmitRequest(DeliveryBuildBatchPlanRequest):
    plan_hash: str = Field(pattern="^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=200)
