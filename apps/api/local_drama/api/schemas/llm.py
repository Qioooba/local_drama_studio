from __future__ import annotations

from pydantic import BaseModel, Field


class LLMProbeRequest(BaseModel):
    provider_connection_id: str | None = Field(default=None, min_length=1, max_length=100)
    provider: str | None = Field(default=None, max_length=50)
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=500)
    remember_api_key: bool = Field(default=False)
    load_test: bool = Field(default=True)
    allow_remote_outbound: bool = Field(default=False)


class LLMProfileSyncRequest(BaseModel):
    provider_connection_id: str | None = Field(default=None, min_length=1, max_length=100)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    provider: str | None = Field(default=None, max_length=50)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    capability: str | None = Field(default="LLM_STORY_PARSE", max_length=100)
    allow_remote_outbound: bool = Field(default=False)
    probe_job_id: str | None = Field(default=None, min_length=1, max_length=100)


class LLMProfilePublishRequest(BaseModel):
    profile_version_id: str = Field(min_length=1)
    api_key: str | None = Field(default=None, max_length=500)
    allow_remote_outbound: bool = Field(default=False)
    probe_job_id: str | None = Field(default=None, min_length=1, max_length=100)


class VideoPromptExpandRequest(BaseModel):
    profile_version_id: str = Field(min_length=1, max_length=100)
    story: str = Field(min_length=2, max_length=2000)
    api_key: str | None = Field(default=None, max_length=500)
    remember_api_key: bool = Field(default=False)
    allow_remote_outbound: bool = Field(default=False)
    language: str = Field(default="zh-CN", pattern=r"^(zh-CN|en-US)$")
    output_spec: dict[str, float | int | str] = Field(default_factory=dict)
