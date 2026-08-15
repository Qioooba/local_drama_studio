from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MotionControlPoint(BaseModel):
    """A normalized point from the local motion brush/vector canvas."""

    x: float = Field(ge=0, le=1_000_000)
    y: float = Field(ge=0, le=1_000_000)
    pressure: float = Field(default=1, ge=0, le=1)
    time_us: int | None = Field(default=None, ge=0)


class MotionControlKeyframe(BaseModel):
    time_us: int = Field(ge=0)
    x: float | None = Field(default=None, ge=0, le=1_000_000)
    y: float | None = Field(default=None, ge=0, le=1_000_000)
    scale: float | None = Field(default=None, gt=0, le=20)
    rotation_degrees: float | None = Field(default=None, ge=-3600, le=3600)


class MotionControlRequest(BaseModel):
    control_kind: Literal["MOTION_MASK", "VECTOR", "KEYFRAME"]
    operation: Literal["MOTION_BRUSH", "INPAINT", "OUTPAINT"]
    subject_role: str = Field(min_length=1, max_length=80)
    profile_version_id: str = Field(min_length=1)
    mask_media_version_id: str | None = Field(default=None, min_length=1)
    keyframe_media_version_id: str | None = Field(default=None, min_length=1)
    vector_path: list[MotionControlPoint] = Field(default_factory=list, max_length=5000)
    keyframes: list[MotionControlKeyframe] = Field(default_factory=list, max_length=500)
    coordinate_space: Literal["NORMALIZED", "PIXELS"] = "NORMALIZED"
    note: str = Field(default="", max_length=1000)
