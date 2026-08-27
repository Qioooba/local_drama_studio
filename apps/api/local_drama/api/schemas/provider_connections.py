from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ProviderConnectionCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    provider_kind: str = Field(min_length=1, max_length=48)
    base_url: str = Field(min_length=1, max_length=500)
    model: str | None = Field(default=None, max_length=240)
    credential_source: Literal["NONE", "OS_SECRET_STORE", "WINDOWS_CREDENTIAL_MANAGER", "ENVIRONMENT"] = "NONE"
    environment_variable_name: str | None = Field(default=None, max_length=160)


class ProviderConnectionUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=500)
    model: str | None = Field(default=None, max_length=240)


class ProviderSecretRequest(BaseModel):
    secret: str = Field(min_length=1, max_length=2560)


class ProviderProbeRequest(BaseModel):
    model: str | None = Field(default=None, max_length=240)
    load_test: bool = False
