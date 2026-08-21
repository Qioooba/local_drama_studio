from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

STORY_ASSET_STATE_KINDS = (
    "BASE", "OUTFIT", "AGE", "INJURY", "EMOTION", "TIME_OF_DAY", "WEATHER", "LIGHTING", "DAMAGE", "CUSTOM",
)

STORY_ASSET_REFERENCE_KINDS = (
    "HERO", "FRONT", "LEFT", "RIGHT", "BACK", "THREE_QUARTER_LEFT", "THREE_QUARTER_RIGHT",
    "THREE_VIEW_SHEET", "FULL_BODY", "MEDIUM", "CLOSEUP", "EXPRESSION_GRID", "ACTION",
    "OUTFIT", "DETAIL", "SCENE_WIDE", "SCENE_REVERSE", "PANORAMA", "LIGHTING_REFERENCE",
    "STYLE_REFERENCE", "OTHER",
)


class StoryAssetStateCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200)
    state_kind: str = Field(default="CUSTOM", max_length=32)
    description: str = Field(default="", max_length=4000)
    state_json: dict[str, Any] | None = None


class StoryAssetStateUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    label: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    state_json: dict[str, Any] | None = None


class StoryAssetStateArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class StoryAssetReferenceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_version_id: str = Field(min_length=1, max_length=36)
    reference_kind: str = Field(min_length=1, max_length=40)
    asset_state_id: str | None = Field(default=None, min_length=1, max_length=36)
    label: str = Field(default="", max_length=200)
    priority: int = Field(default=100, ge=0, le=1000)
    is_locked: bool = False
    yaw_deg: float | None = Field(default=None, ge=-180, le=180)
    pitch_deg: float | None = Field(default=None, ge=-90, le=90)


class StoryAssetReferenceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    label: str | None = Field(default=None, max_length=200)
    priority: int | None = Field(default=None, ge=0, le=1000)
    is_locked: bool | None = None
    yaw_deg: float | None = Field(default=None, ge=-180, le=180)
    pitch_deg: float | None = Field(default=None, ge=-90, le=90)


class StoryAssetReferenceArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class EpisodeAssetStateBindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    story_asset_id: str = Field(min_length=1, max_length=36)
    asset_state_id: str = Field(min_length=1, max_length=36)


class ShotAssetStateBindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1, max_length=36)
    asset_state_id: str = Field(min_length=1, max_length=36)


class AssetMultiViewPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_state_id: str | None = Field(default=None, min_length=1, max_length=36)
    profile_version_id: str | None = Field(default=None, min_length=1, max_length=36)
    consistency_strength: str = Field(default="HIGH", pattern=r"^(LOW|MEDIUM|HIGH)$")
    background: str = Field(default="CLEAN", pattern=r"^(CLEAN|TRANSPARENT|ORIGINAL)$")


class AssetMultiViewSubmitRequest(AssetMultiViewPreflightRequest):
    plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)


class AssetExpressionPreflightRequest(AssetMultiViewPreflightRequest):
    """Expression generation shares consistency/background controls but resolves IMAGE_EXPRESSION."""


class AssetExpressionSubmitRequest(AssetExpressionPreflightRequest):
    plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)


class AssetDetailPreflightRequest(AssetMultiViewPreflightRequest):
    """Close-up/detail generation resolves IMAGE_EDIT with a frozen HERO image."""


class AssetDetailSubmitRequest(AssetDetailPreflightRequest):
    plan_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)
