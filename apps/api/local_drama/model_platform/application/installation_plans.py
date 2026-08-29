"""Durable, fail-closed V2 plans for Windows offline model imports.

Creating a plan is intentionally not an import command.  It records the
administrator's selected destination, expected artifacts and licence decision
without accepting a server path or performing file I/O.  A later Host-owned
import worker is the only component allowed to resolve a staged bundle.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Mapping, Sequence

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.application.trusted_download_sources import validate_trusted_https_source

_BUNDLE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True, slots=True)
class ExpectedInstallArtifact:
    relative_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class OfflineImportPlanRequest:
    target_library_id: str
    release_code: str
    bundle_reference: str
    license_id: str
    expected_artifacts: tuple[ExpectedInstallArtifact, ...]


@dataclass(frozen=True, slots=True)
class TrustedDownloadArtifact:
    """A hash-bound HTTPS source for one library-relative artifact."""

    relative_path: str
    sha256: str
    size_bytes: int
    source_url: str


@dataclass(frozen=True, slots=True)
class TrustedDownloadPlanRequest:
    """Persist a download contract; a later Host command performs I/O."""

    target_library_id: str
    release_code: str
    bundle_reference: str
    license_id: str
    artifacts: tuple[TrustedDownloadArtifact, ...]


@dataclass(frozen=True, slots=True)
class InstallationPlan:
    id: str
    target_library_id: str
    target_library_label: str
    release_code: str
    source_kind: str
    expected_artifact_count: int
    expected_total_bytes: int
    license_id: str
    status: str
    created_at: str


@dataclass(frozen=True, slots=True)
class InstallationTarget:
    id: str
    label: str


class InstallationPlanService:
    """Own the durable V2 plan boundary for imports and trusted downloads."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def create_offline_import(self, request: OfflineImportPlanRequest) -> InstallationPlan:
        artifacts = _validate_artifacts(request.expected_artifacts)
        return self._create_plan(
            target_library_id=request.target_library_id,
            release_code=request.release_code,
            bundle_reference=request.bundle_reference,
            license_id=request.license_id,
            artifacts=artifacts,
            source_kind="OFFLINE_BUNDLE",
            status="AWAITING_OFFLINE_IMPORT",
            source_details={},
        )

    def create_trusted_download(self, request: TrustedDownloadPlanRequest) -> InstallationPlan:
        """Persist exact approved sources without initiating a network request."""
        artifacts = _validate_artifacts(
            tuple(ExpectedInstallArtifact(item.relative_path, item.sha256, item.size_bytes) for item in request.artifacts)
        )
        sources_by_path: dict[str, str] = {}
        for item in request.artifacts:
            source = validate_trusted_https_source(self.settings, item.source_url)
            normalized_path = PurePosixPath(item.relative_path).as_posix()
            sources_by_path[normalized_path] = source.url
        if len(sources_by_path) != len(artifacts):
            raise DomainRuleError("MP_TRUSTED_DOWNLOAD_SOURCE_INVALID", "每个下载组件必须有且仅有一个可信来源。")
        return self._create_plan(
            target_library_id=request.target_library_id,
            release_code=request.release_code,
            bundle_reference=request.bundle_reference,
            license_id=request.license_id,
            artifacts=artifacts,
            source_kind="TRUSTED_HTTPS",
            status="AWAITING_TRUSTED_DOWNLOAD",
            source_details={
                "artifacts": [
                    {"relative_path": item.relative_path, "source_url": sources_by_path[item.relative_path]}
                    for item in artifacts
                ]
            },
        )

    def _create_plan(
        self,
        *,
        target_library_id: str,
        release_code: str,
        bundle_reference: str,
        license_id: str,
        artifacts: tuple[ExpectedInstallArtifact, ...],
        source_kind: str,
        status: str,
        source_details: Mapping[str, object],
    ) -> InstallationPlan:
        release_code = release_code.strip()
        bundle_reference = bundle_reference.strip()
        license_id = license_id.strip()
        if not release_code:
            raise DomainRuleError("MP_INSTALL_RELEASE_CODE_REQUIRED", "安装计划必须声明模型发布标识。")
        if not _BUNDLE_REFERENCE.fullmatch(bundle_reference):
            raise DomainRuleError(
                "MP_OFFLINE_BUNDLE_REFERENCE_INVALID",
                "安装包标识只能包含字母、数字、点、短横线和下划线，不能包含服务器路径。",
            )
        if not license_id:
            raise DomainRuleError("MP_INSTALL_LICENSE_REQUIRED", "创建安装计划前必须确认许可证标识。")
        library = self._target_library(target_library_id)
        now = _utc_now()
        plan = InstallationPlan(
            id=str(uuid.uuid4()),
            target_library_id=target_library_id,
            target_library_label=_library_label(str(library["code"])),
            release_code=release_code,
            source_kind=source_kind,
            expected_artifact_count=len(artifacts),
            expected_total_bytes=sum(item.size_bytes for item in artifacts),
            license_id=license_id,
            status=status,
            created_at=now,
        )
        source = {"kind": plan.source_kind, "bundle_reference": bundle_reference, **source_details}
        expected = {
            "release_code": release_code,
            "artifacts": [
                {"relative_path": item.relative_path, "sha256": item.sha256.lower(), "size_bytes": item.size_bytes}
                for item in artifacts
            ],
        }
        license_record = {"license_id": license_id, "accepted": True}
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO mp_install_plans
                (id,target_library_id,source_json,expected_json,license_json,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (plan.id, plan.target_library_id, _json(source), _json(expected), _json(license_record), plan.status, now, now),
            )
        return plan

    def list(self, *, limit: int = 100) -> list[InstallationPlan]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT plan.id,plan.target_library_id,library.code,plan.source_json,plan.expected_json,
                          plan.license_json,plan.status,plan.created_at
                   FROM mp_install_plans plan
                   JOIN mp_model_libraries library ON library.id=plan.target_library_id
                   ORDER BY plan.created_at DESC,plan.id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [_to_public_plan(row) for row in rows]

    def list_targets(self) -> Sequence[InstallationTarget]:
        """Expose only safe destination identities that remain service-managed."""
        configured = {str(root.resolve()) for root in self.settings.model_library_roots}
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT id,code,root_path_local,read_only
                   FROM mp_model_libraries ORDER BY code,id"""
            ).fetchall()
        return [
            InstallationTarget(str(row["id"]), _library_label(str(row["code"])))
            for row in rows
            if not bool(row["read_only"]) and str(row["root_path_local"]) in configured
        ]

    def _target_library(self, library_id: str) -> sqlite3.Row:
        with self.database.connect() as connection:
            row: sqlite3.Row | None = connection.execute(
                "SELECT id,code,root_path_local,read_only FROM mp_model_libraries WHERE id=?", (library_id,)
            ).fetchone()
        if row is None:
            raise DomainRuleError("MP_INSTALL_TARGET_LIBRARY_NOT_FOUND", "目标模型库尚未由 V2 控制面登记。")
        if bool(row["read_only"]):
            raise DomainRuleError("MP_INSTALL_TARGET_LIBRARY_READ_ONLY", "只读模型库不能接收离线导入。")
        configured = {str(root.resolve()) for root in self.settings.model_library_roots}
        if str(row["root_path_local"]) not in configured:
            raise DomainRuleError(
                "MP_INSTALL_TARGET_LIBRARY_STALE",
                "目标模型库已不属于当前 Windows 服务身份配置；请重新配置并登记。",
            )
        return row


def _validate_artifacts(items: Sequence[ExpectedInstallArtifact]) -> tuple[ExpectedInstallArtifact, ...]:
    if not items or len(items) > 100:
        raise DomainRuleError("MP_INSTALL_EXPECTED_ARTIFACTS_INVALID", "离线导入计划必须包含 1 到 100 个预期组件。")
    normalized: list[ExpectedInstallArtifact] = []
    seen: set[str] = set()
    for item in items:
        try:
            path = PurePosixPath(item.relative_path)
        except TypeError as error:
            raise DomainRuleError("MP_INSTALL_ARTIFACT_PATH_INVALID", "组件路径必须是库内相对路径。") from error
        rendered = path.as_posix()
        if (
            path.is_absolute()
            or not rendered
            or "\\" in item.relative_path
            or ":" in item.relative_path
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise DomainRuleError("MP_INSTALL_ARTIFACT_PATH_INVALID", "组件路径必须是安全的库内相对路径。")
        if rendered in seen:
            raise DomainRuleError("MP_INSTALL_ARTIFACT_PATH_DUPLICATE", "离线导入计划不能包含重复组件路径。")
        if not _SHA256.fullmatch(item.sha256):
            raise DomainRuleError("MP_INSTALL_ARTIFACT_HASH_INVALID", "每个组件必须提供 SHA-256 校验值。")
        if item.size_bytes <= 0:
            raise DomainRuleError("MP_INSTALL_ARTIFACT_SIZE_INVALID", "每个组件必须提供大于零的预期大小。")
        seen.add(rendered)
        normalized.append(ExpectedInstallArtifact(rendered, item.sha256.lower(), item.size_bytes))
    return tuple(normalized)


def _to_public_plan(row: sqlite3.Row) -> InstallationPlan:
    source = _json_object(row["source_json"])
    expected = _json_object(row["expected_json"])
    license_record = _json_object(row["license_json"])
    artifacts = expected.get("artifacts")
    valid_artifacts = artifacts if isinstance(artifacts, list) else []
    total = sum(
        item.get("size_bytes", 0)
        for item in valid_artifacts
        if isinstance(item, dict) and isinstance(item.get("size_bytes"), int) and item["size_bytes"] > 0
    )
    raw_kind = source.get("kind")
    source_kind = raw_kind if isinstance(raw_kind, str) else "UNKNOWN"
    raw_release_code = expected.get("release_code")
    release_code = raw_release_code if isinstance(raw_release_code, str) else "未声明发布版"
    raw_license_id = license_record.get("license_id")
    license_id = raw_license_id if isinstance(raw_license_id, str) else "未记录"
    return InstallationPlan(
        id=str(row["id"]),
        target_library_id=str(row["target_library_id"]),
        target_library_label=_library_label(str(row["code"])),
        release_code=release_code,
        source_kind=source_kind,
        expected_artifact_count=len(valid_artifacts),
        expected_total_bytes=total,
        license_id=license_id,
        status=str(row["status"]),
        created_at=str(row["created_at"]),
    )


def _library_label(code: str) -> str:
    suffix = code.removeprefix("model-library-").replace("-", " ").strip()
    return f"模型库 {suffix}" if suffix else "受管模型库"


def _json_object(value: object) -> Mapping[str, object]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
