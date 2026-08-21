from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class GenerationEstimateDimensions(BaseModel):
    profile_version_id: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    frame_count: int | None = None
    steps: int | None = None
    gpu_class: str | None = None
    gpu_hardware_model: None = None
    gpu_hardware_model_known: Literal[False]


class GenerationEstimateEvidence(BaseModel):
    source: Literal["LOCAL_SUCCEEDED_JOB_ATTEMPTS"]
    most_recent_first: Literal[True]
    candidate_limit: int
    candidate_count: int
    gpu_dimension_source: Literal["JOB_RESOURCE_LEASE_CLASS_OR_CHANNEL"]
    gpu_hardware_model_recorded: Literal[False]


class GenerationEstimateAudit(BaseModel):
    read_only: Literal[True]
    writes_performed: Literal[0]
    query_count: int
    query_limit: int


class GenerationEstimateResponse(BaseModel):
    status: Literal["AVAILABLE", "NO_LOCAL_ESTIMATE"]
    reason: Literal["SCHEMA_UNAVAILABLE", "NO_MATCHING_HISTORY", "INSUFFICIENT_SAMPLES"] | None
    dimensions: GenerationEstimateDimensions
    sample_count: int
    minimum_sample_count: int
    p50_seconds: float | None
    p90_seconds: float | None
    evidence: GenerationEstimateEvidence
    audit: GenerationEstimateAudit
    local_only: Literal[True]
    network_contacted: Literal[False]
