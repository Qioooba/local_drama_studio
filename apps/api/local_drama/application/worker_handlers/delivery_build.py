"""DELIVERY_BUILD job handler: frozen-plan precheck plus deterministic package build."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Protocol

from local_drama.domain.errors import DomainRuleError


class DeliveryPlanPort(Protocol):
    """Freshness precheck for the frozen delivery plan (BackgroundOperationService)."""

    def delivery_plan(
        self,
        episode_render_version_id: str,
        target_version_id: str,
        brand_kit_id: str | None = None,
        watermark_profile_id: str | None = None,
        compliance_policy_id: str | None = None,
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...


class DeliveryBuildPort(Protocol):
    """Deterministic delivery package builder (TimelineService)."""

    def build_delivery(
        self,
        episode_render_version_id: str,
        target_version_id: str,
        brand_kit_id: str | None = None,
        watermark_profile_id: str | None = None,
        compliance_policy_id: str | None = None,
        *,
        actor: str = "local-user",
    ) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...


AtomicWriter = Callable[[Path, Callable[[Path], object]], None]


def run_delivery_build_job(
    job: dict[str, Any],
    output_root: Path,
    *,
    work_root: Path,
    delivery_planner: DeliveryPlanPort,
    delivery_builder: DeliveryBuildPort,
    atomic_writer: AtomicWriter,
) -> tuple[str, str]:
    snapshot = job["input_snapshot"]
    render_id = str(snapshot.get("episode_render_version_id") or "")
    target_version_id = str(snapshot.get("target_version_id") or "")
    expected = str(snapshot.get("delivery_fingerprint") or "")
    if (
        job["subject_type"] != "EPISODE_RENDER_VERSION"
        or str(job["subject_id"]) != render_id
        or not target_version_id
        or not expected
    ):
        raise DomainRuleError("DELIVERY_JOB_SNAPSHOT_INVALID", "交付 Job 缺少不可变渲染或目标版本输入")
    current = delivery_planner.delivery_plan(
        render_id,
        target_version_id,
        snapshot.get("brand_kit_id"),
        snapshot.get("watermark_profile_id"),
        snapshot.get("compliance_policy_id"),
    )
    if str(current["fingerprint"]) != expected:
        raise DomainRuleError("DELIVERY_INPUT_STALE", "交付入队后渲染、目标或批准状态已变化，请重新提交")
    delivery = delivery_builder.build_delivery(
        render_id,
        target_version_id,
        snapshot.get("brand_kit_id"),
        snapshot.get("watermark_profile_id"),
        snapshot.get("compliance_policy_id"),
        actor="delivery-worker",
    )
    report = {
        "schema_version": "localdrama.delivery-build-job-report.v1",
        "job_id": str(job["id"]),
        "delivery_package_id": str(delivery["id"]),
        "manifest_sha256": str(delivery.get("manifest_sha256") or ""),
        "status": str(delivery["status"]),
        "local_only": True,
        "network_contacted": False,
    }
    output = output_root / "delivery-build-report.json"
    atomic_writer(output, lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"))
    return "DELIVERY_BUILD_REPORT", output.relative_to(work_root).as_posix()
