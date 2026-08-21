from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ShotReorderCommand(BaseModel):
    shot_id: str = Field(min_length=1, max_length=36)
    before_shot_id: str | None = Field(default=None, max_length=36)
    after_shot_id: str | None = Field(default=None, max_length=36)
    expected_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def exactly_one_anchor(self) -> "ShotReorderCommand":
        if (self.before_shot_id is None) == (self.after_shot_id is None):
            raise ValueError("必须且只能提供 before_shot_id 或 after_shot_id")
        return self


class ShotSplitCommand(BaseModel):
    shot_id: str = Field(min_length=1, max_length=36)
    expected_revision: int = Field(ge=1)
    first_code: str = Field(min_length=1, max_length=64)
    second_code: str = Field(min_length=1, max_length=64)
    first_duration_ms: int = Field(gt=0)


class ShotEditPlanRequest(BaseModel):
    ordering_token: str = Field(min_length=64, max_length=64)
    reorder: ShotReorderCommand | None = None
    splits: list[ShotSplitCommand] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def non_empty(self) -> "ShotEditPlanRequest":
        if self.reorder is None and not self.splits:
            raise ValueError("镜头编辑计划不能为空")
        return self


class ShotEditCommitRequest(ShotEditPlanRequest):
    expected_plan_hash: str = Field(min_length=64, max_length=64)
