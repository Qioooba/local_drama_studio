from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RuntimeEnvironmentCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    title: str = Field(min_length=1, max_length=200)
    manifest: dict[str, Any]


class RuntimeEnvironmentVersionRequest(BaseModel):
    manifest: dict[str, Any]
    change_note: str = Field(default="更新运行环境", min_length=1, max_length=500)


class WorkflowAppContractRequest(BaseModel):
    capability: str = Field(min_length=1, max_length=100)
    contract: dict[str, Any]
    bindings: dict[str, Any]
    semantic_phases: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowRuntimeBindRequest(BaseModel):
    contract_version_id: str = Field(min_length=36, max_length=36)
    runtime_environment_version_id: str = Field(min_length=36, max_length=36)


class RuntimeStartRequest(BaseModel):
    instance_kind: Literal["MANAGED", "EXTERNAL"] = "MANAGED"


class RuntimeStopRequest(BaseModel):
    expected_instance_id: str = Field(min_length=36, max_length=36)

