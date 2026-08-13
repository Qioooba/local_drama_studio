"""Offline model import and compatibility report generation.

No downloads, model loading, runtime probing or public requests occur here.
"""

from __future__ import annotations

import hashlib
import json
import struct
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_and_header(path: Path) -> tuple[str, int, dict[str, Any]]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        prefix = source.read(8)
        if len(prefix) == 8:
            header_size = struct.unpack("<Q", prefix)[0]
            header_raw = source.read(header_size)
            try:
                header = json.loads(header_raw.decode("utf-8")) if header_raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                header = {"parse_status": "INVALID_JSON"}
        else:
            header = {"parse_status": "TRUNCATED"}
        digest.update(prefix)
        size += len(prefix)
        if len(prefix) == 8:
            digest.update(header_raw)
            size += len(header_raw)
        while chunk := source.read(4 * 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    tensors = {key: value for key, value in header.items() if key != "__metadata__" and isinstance(value, dict)}
    dtypes = sorted({str(value.get("dtype")) for value in tensors.values() if value.get("dtype")})
    return digest.hexdigest(), size, {"tensor_count": len(tensors), "dtypes": dtypes, "metadata": header.get("__metadata__", {})}


class ModelCompatibilityService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def _license_evidence(self, project_id: str | None, artifact_id: str, sha256: str) -> dict[str, Any] | None:
        if not project_id:
            return None
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM model_license_evidence WHERE project_id=? AND model_artifact_id=? ORDER BY created_at DESC",
                (project_id, artifact_id),
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row["evidence_json"]))
            except json.JSONDecodeError:
                continue
            if (
                str(row["license_status"]) in {"LOCAL_LICENSE_VERIFIED", "USER_OWNED"}
                and str(row["artifact_sha256"]) == sha256
                and str(payload.get("artifact_sha256", "")) == sha256
                and str(payload.get("license_name", "")).strip() == str(row["license_name"])
            ):
                return dict(row)
        return None

    def report(self, artifact_id: str, project_id: str | None = None, actor: str = "local-user") -> dict[str, Any]:
        with self.database.connect() as connection:
            artifact = connection.execute("SELECT * FROM model_artifacts WHERE id=?", (artifact_id,)).fetchone()
        if artifact is None:
            raise DomainRuleError("MODEL_ARTIFACT_NOT_FOUND", "模型 Artifact 不存在")
        path = Path(str(artifact["machine_path_ref"])).resolve()
        if not path.is_file() or path.is_symlink():
            raise DomainRuleError("MODEL_ARTIFACT_PATH_INVALID", "模型路径缺失、不是文件或为 symlink")
        sha256, byte_size, header = _hash_and_header(path)
        component = str(artifact["kind"])
        declared_quantization = "FP32" if "audio" in component.casefold() else "FP16" if "vae" in component.casefold() else "INT8/FP8_DECLARED"
        header_dtypes = set(header.get("dtypes", []))
        quantization_status = "HEADER_MATCHED" if header_dtypes else "HEADER_UNVERIFIED"
        evidence = self._license_evidence(project_id, artifact_id, sha256)
        license_status = str(evidence["license_status"]) if evidence else "UNVERIFIED_NO_LOCAL_LICENSE_EVIDENCE"
        blockers = []
        if not evidence:
            blockers.extend(["LICENSE_EVIDENCE_MISSING", "FORMAL_IMPORT_REQUIRES_OPERATOR_LICENSE_RECORD"])
        if quantization_status != "HEADER_MATCHED":
            blockers.append("QUANTIZATION_HEADER_UNVERIFIED")
        report_status = "PASS" if evidence and quantization_status == "HEADER_MATCHED" else "BLOCKED"
        now = _utc_now()
        report_id = str(uuid.uuid4())
        quantization = {"declared": declared_quantization, "header_dtypes": sorted(header_dtypes), "status": quantization_status}
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO model_compatibility_reports
                (id, model_artifact_id, path_ref, sha256, byte_size, header_json, quantization_json,
                license_status, report_status, blockers_json, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (report_id, artifact_id, str(path), sha256, byte_size, _json(header), _json(quantization), license_status, report_status, _json(blockers), now, actor),
            )
            connection.execute(
                "UPDATE model_artifacts SET sha256=?, size_bytes=?, compatibility_json=?, license_note=?, updated_at=?, revision=revision+1 WHERE id=?",
                (sha256, byte_size, _json({"quantization": quantization, "report_id": report_id}), license_status, now, artifact_id),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'operator', 'MODEL_COMPATIBILITY_REPORTED', 'model_artifact', ?, ?, ?)""",
                (actor, artifact_id, "离线模型 hash/量化报告生成", _json({"report_id": report_id, "sha256": sha256, "byte_size": byte_size, "report_status": report_status})),
            )
        return {"id": report_id, "model_artifact_id": artifact_id, "path_ref": str(path), "sha256": sha256, "byte_size": byte_size,
                "header": header, "quantization": quantization, "license_status": license_status, "report_status": report_status, "blockers": blockers,
                "license_evidence_id": str(evidence["id"]) if evidence else None,
                "runtime_contacted": False, "network_contacted": False}

    def import_license_evidence(
        self,
        project_id: str,
        artifact_id: str,
        evidence_path: str,
        license_name: str,
        license_status: str,
        settings: Any,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if license_status not in {"LOCAL_LICENSE_VERIFIED", "USER_OWNED"}:
            raise DomainRuleError("MODEL_LICENSE_STATUS_INVALID", "模型许可证状态必须是本地核验或用户自有")
        with self.database.connect() as connection:
            project = connection.execute("SELECT root_rel FROM projects WHERE id=?", (project_id,)).fetchone()
            artifact = connection.execute("SELECT id, machine_path_ref FROM model_artifacts WHERE id=?", (artifact_id,)).fetchone()
        if project is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        if artifact is None:
            raise DomainRuleError("MODEL_ARTIFACT_NOT_FOUND", "模型 Artifact 不存在")
        project_root = (settings.projects_root / str(project["root_rel"])).resolve()
        candidate = (project_root / evidence_path).resolve()
        evidence_root = (project_root / "00_admin" / "licenses").resolve()
        if Path(evidence_path).is_absolute() or not candidate.is_relative_to(evidence_root) or candidate.is_symlink() or not candidate.is_file():
            raise DomainRuleError("MODEL_LICENSE_EVIDENCE_PATH_INVALID", "许可证证据必须位于项目 00_admin/licenses 内，且不能越界或为 symlink")
        current = project_root
        for part in Path(evidence_path).parts:
            current = current / part
            if current.is_symlink():
                raise DomainRuleError("MODEL_LICENSE_EVIDENCE_PATH_INVALID", "许可证证据路径不能经过 symlink")
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DomainRuleError("MODEL_LICENSE_EVIDENCE_INVALID", "许可证证据必须是可读取的 UTF-8 JSON object") from error
        if not isinstance(payload, dict):
            raise DomainRuleError("MODEL_LICENSE_EVIDENCE_INVALID", "许可证证据 JSON 顶层必须是 object")
        artifact_path = Path(str(artifact["machine_path_ref"])).resolve()
        if not artifact_path.is_file() or artifact_path.is_symlink():
            raise DomainRuleError("MODEL_ARTIFACT_PATH_INVALID", "模型路径缺失、不是文件或为 symlink")
        artifact_sha256, _, _ = _hash_and_header(artifact_path)
        declared_sha = str(payload.get("artifact_sha256", ""))
        declared_name = str(payload.get("license_name", license_name)).strip()
        if declared_sha != artifact_sha256 or declared_name != license_name.strip() or not license_name.strip():
            raise DomainRuleError("MODEL_LICENSE_EVIDENCE_MISMATCH", "许可证记录必须声明当前模型 SHA-256 和匹配的 license_name")
        evidence_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest()
        now = _utc_now()
        evidence_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            prior = connection.execute(
                "SELECT * FROM model_license_evidence WHERE project_id=? AND model_artifact_id=? AND evidence_sha256=?",
                (project_id, artifact_id, evidence_sha256),
            ).fetchone()
            if prior is not None:
                return {**dict(prior), "duplicate": True}
            connection.execute(
                """INSERT INTO model_license_evidence
                (id, project_id, model_artifact_id, path_rel, evidence_sha256, artifact_sha256, license_name,
                 license_status, evidence_json, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (evidence_id, project_id, artifact_id, str(Path(evidence_path).as_posix()), evidence_sha256,
                 artifact_sha256, license_name.strip(), license_status, _json(payload), now, actor),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'operator', 'MODEL_LICENSE_EVIDENCE_IMPORTED', 'model_artifact', ?, ?, ?)""",
                (actor, artifact_id, "导入项目本地模型许可证证据", _json({"evidence_id": evidence_id, "project_id": project_id, "evidence_sha256": evidence_sha256, "artifact_sha256": artifact_sha256})),
            )
        return {"id": evidence_id, "project_id": project_id, "model_artifact_id": artifact_id,
                "path_rel": str(Path(evidence_path).as_posix()), "evidence_sha256": evidence_sha256,
                "artifact_sha256": artifact_sha256, "license_name": license_name.strip(),
                "license_status": license_status, "duplicate": False}

    def latest_pass(self, project_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT mcr.* FROM model_compatibility_reports mcr
                JOIN model_license_evidence mle ON mle.model_artifact_id=mcr.model_artifact_id
                WHERE mle.project_id=? AND mcr.report_status='PASS'
                AND mcr.license_status IN ('LOCAL_LICENSE_VERIFIED','USER_OWNED')
                AND mle.artifact_sha256=mcr.sha256
                ORDER BY mcr.created_at DESC LIMIT 1""", (project_id,),
            ).fetchone()
        return dict(row) if row else None
