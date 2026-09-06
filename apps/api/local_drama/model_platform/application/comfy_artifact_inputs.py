"""V2-only materialization of immutable Job artifacts into Comfy input.

An execution snapshot stores a business reference, never a Windows path.  The
worker resolves that reference only after the source artifact's integrity and
V2 lineage have been verified, then copies it into the isolated Comfy input
root under a content-addressed filename.  This is the reusable hand-off
primitive for future V2 multi-stage runs such as image-to-video.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Mapping, cast

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.image_input_roles import COMFY_IMAGE_INPUT_ROLES
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.atomic import replace_path
from local_drama.infrastructure.filesystem.path_policy import controlled_path

_IMAGE_ARTIFACT_ROLES = COMFY_IMAGE_INPUT_ROLES


def materialize_v2_comfy_artifact_inputs(
    database: Database,
    settings: Settings,
    semantic_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Return Comfy-ready scalar inputs while preserving snapshot references.

    A reference has the exact public shape ``{"artifact_id": "uuid"}``.
    It is intentionally not a generic path/URL escape hatch.  Anything else
    remains untouched for the workflow's normal semantic contract validation.
    """

    result = dict(semantic_inputs)
    for raw_role, value in semantic_inputs.items():
        reference = _artifact_id(value)
        if reference is None:
            continue
        role = str(raw_role).strip().upper()
        if role not in _IMAGE_ARTIFACT_ROLES:
            raise DomainRuleError("MP_COMFY_ARTIFACT_ROLE_UNSUPPORTED", "V2 Comfy 制品引用不支持该语义槽位。", {"role": role})
        artifact = _verified_image_artifact(database, reference)
        result[str(raw_role)] = _copy_to_comfy_input(settings, artifact)
    return result


def _artifact_id(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    if set(value) != {"artifact_id"} or not isinstance(value.get("artifact_id"), str) or not value["artifact_id"].strip():
        raise DomainRuleError("MP_COMFY_ARTIFACT_REFERENCE_INVALID", "V2 Comfy 制品引用必须且只能包含 artifact_id。")
    return cast(str, value["artifact_id"]).strip()


def _verified_image_artifact(database: Database, artifact_id: str) -> Mapping[str, str]:
    with database.connect() as connection:
        row = connection.execute(
            """SELECT artifact.id,artifact.kind,artifact.status,artifact.sandbox_rel_path,artifact.sha256,
                      source_job.type AS source_job_type,source_snapshot.execution_binding_json
               FROM artifacts artifact
               JOIN job_attempts attempt ON attempt.id=artifact.job_attempt_id
               JOIN jobs source_job ON source_job.id=attempt.job_id
               JOIN mp_execution_job_links source_link ON source_link.job_id=source_job.id
               JOIN mp_execution_snapshots source_snapshot ON source_snapshot.id=source_link.execution_snapshot_id
               WHERE artifact.id=?""",
            (artifact_id,),
        ).fetchone()
    if row is None or str(row["status"]) != "VERIFIED" or str(row["kind"]) != "COMFY_OUTPUT" or str(row["source_job_type"]) != "MODEL_PLATFORM_EXECUTION":
        raise DomainRuleError("MP_COMFY_ARTIFACT_REFERENCE_INVALID", "V2 Comfy 输入必须引用已验证的 V2 Comfy 产物。")
    try:
        binding = json.loads(str(row["execution_binding_json"]))
    except (TypeError, ValueError) as error:
        raise DomainRuleError("MP_COMFY_ARTIFACT_REFERENCE_INVALID", "来源 V2 制品缺少可验证的输出合同。") from error
    expected = binding.get("expected_output") if isinstance(binding, dict) else None
    if not isinstance(expected, dict) or expected.get("media_kind") != "IMAGE":
        raise DomainRuleError("MP_COMFY_ARTIFACT_MEDIA_KIND_INVALID", "图生视频只能引用声明为 IMAGE 的 V2 制品。")
    return {
        "id": str(row["id"]),
        "sandbox_rel_path": str(row["sandbox_rel_path"]),
        "sha256": str(row["sha256"]),
    }


def _copy_to_comfy_input(settings: Settings, artifact: Mapping[str, str]) -> str:
    if settings.comfy_input_root is None:
        raise DomainRuleError("MP_COMFY_INPUT_ROOT_REQUIRED", "V2 Comfy 制品输入需要显式隔离的 Comfy input root。")
    work_root = settings.work_root.resolve()
    source = controlled_path(
        work_root,
        artifact["sandbox_rel_path"],
        must_exist=True,
        require_file=True,
        code="MP_COMFY_ARTIFACT_FILE_MISSING",
    )
    input_root = settings.comfy_input_root.resolve()
    input_root.mkdir(parents=True, exist_ok=True)
    filename = f"v2-{artifact['id']}-{artifact['sha256'][:12]}{source.suffix.lower()}"
    target = (input_root / filename).resolve()
    if not target.is_relative_to(input_root):
        raise DomainRuleError("MP_COMFY_INPUT_PATH_INVALID", "V2 Comfy 输入物化路径越界。")
    if not target.exists():
        partial = target.with_name(f".partial-{target.name}")
        shutil.copyfile(source, partial)
        replace_path(partial, target)
    if _sha256(target) != artifact["sha256"]:
        target.unlink(missing_ok=True)
        raise DomainRuleError("MP_COMFY_INPUT_INTEGRITY_MISMATCH", "V2 Comfy 输入物化后的 SHA-256 不一致。")
    return filename


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
