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

    def report(self, artifact_id: str, actor: str = "local-user") -> dict[str, Any]:
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
        license_status = "UNVERIFIED_NO_LOCAL_LICENSE_EVIDENCE"
        blockers = ["LICENSE_EVIDENCE_MISSING", "FORMAL_IMPORT_REQUIRES_OPERATOR_LICENSE_RECORD"]
        report_status = "BLOCKED"
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
                (actor, artifact_id, "离线模型 hash/量化报告生成但因 license 证据阻塞", _json({"report_id": report_id, "sha256": sha256, "byte_size": byte_size})),
            )
        return {"id": report_id, "model_artifact_id": artifact_id, "path_ref": str(path), "sha256": sha256, "byte_size": byte_size,
                "header": header, "quantization": quantization, "license_status": license_status, "report_status": report_status, "blockers": blockers,
                "runtime_contacted": False, "network_contacted": False}

    def latest_pass(self, project_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT mcr.* FROM model_compatibility_reports mcr
                JOIN model_artifacts ma ON ma.id=mcr.model_artifact_id
                JOIN local_runtimes lr ON lr.id=ma.runtime_id
                JOIN project_profile_bindings ppb ON ppb.project_id=?
                WHERE mcr.report_status='PASS' AND mcr.license_status IN ('LOCAL_LICENSE_VERIFIED','USER_OWNED')
                ORDER BY mcr.created_at DESC LIMIT 1""", (project_id,),
            ).fetchone()
        return dict(row) if row else None
