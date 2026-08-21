from typing import Literal

from pydantic import BaseModel, Field


class AssetProposalDecisionRequest(BaseModel):
    action: Literal["CREATE_NEW", "MERGE_EXISTING", "REJECT"]
    expected_revision: int = Field(ge=1)
    target_asset_id: str | None = Field(default=None, max_length=36)
    new_asset_code: str | None = Field(default=None, max_length=120)
    decision_note: str = Field(default="", max_length=1000)
