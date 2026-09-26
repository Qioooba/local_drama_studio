"""V2-only materialization of immutable Job artifacts into Comfy input.

An execution snapshot stores a business reference, never a Windows path.  The
worker resolves that reference only after the source artifact's integrity and
V2 lineage have been verified, then copies it into the isolated Comfy input
root under a content-addressed filename.  This is the reusable hand-off
primitive for future V2 multi-stage runs such as image-to-video.

Two reference shapes are accepted, and nothing else:

``{"artifact_id": "uuid"}``
    The original V2 branch.  It stays exactly as strict as before: the artifact
    must be a ``VERIFIED`` ``COMFY_OUTPUT`` produced by a real
    ``MODEL_PLATFORM_EXECUTION`` job whose frozen contract declares an ``IMAGE``
    output.

``{"media_version_id": "uuid", "sha256": "…64 hex…"}``
    The controlled business-reference branch (design §D4.3).  Explainer beats and
    character/scene references live in ``media_versions`` — an uploaded photo or a
    shot's adopted still is a perfectly legitimate image, but it was **not**
    produced by a V2 Comfy job, so it cannot use the artifact branch and must not
    be forged into one.  This branch therefore verifies its own facts: the version
    must be registered, be an ``IMAGE``, be integrity ``VERIFIED``, and its frozen
    SHA-256 must equal the snapshot's.  It is limited to the declared image
    semantic roles, and any other field, path or URL is rejected.
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

#: The exact key set of the business-reference branch.  Keeping it a closed set is
#: what stops this from becoming a "copy any file" escape hatch.
_MEDIA_REFERENCE_KEYS = frozenset({"media_version_id", "sha256"})

#: Where a materialized business reference is recorded on the semantic input, so a
#: downstream auditor can tell a real file was placed in the Comfy input root.
_MEDIA_INPUT_RECEIPT_KEY = "__media_version_inputs__"

_HEX = frozenset("0123456789abcdef")


def materialize_v2_comfy_artifact_inputs(
    database: Database,
    settings: Settings,
    semantic_inputs: Mapping[str, Any],
    *,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Return Comfy-ready scalar inputs while preserving snapshot references.

    ``project_id`` is the frozen business scope of the job being executed.  When
    it is supplied, a ``media_version_id`` reference must belong to that project;
    a cross-project reference needs an explicit reuse grant and is refused here
    rather than silently widening the input bridge.
    """

    result = dict(semantic_inputs)
    receipts: list[dict[str, Any]] = []
    for raw_role, value in semantic_inputs.items():
        role = str(raw_role).strip().upper()
        reference = _artifact_id(value)
        if reference is not None:
            if role not in _IMAGE_ARTIFACT_ROLES:
                raise DomainRuleError(
                    "MP_COMFY_ARTIFACT_ROLE_UNSUPPORTED",
                    "V2 Comfy 制品引用不支持该语义槽位。",
                    {"role": role},
                )
            artifact = _verified_image_artifact(database, reference)
            result[str(raw_role)] = _copy_to_comfy_input(
                settings,
                _artifact_source(settings, artifact),
                content_id=artifact["id"],
                sha256=artifact["sha256"],
            )
            continue

        media = _media_version_reference(value)
        if media is None:
            continue
        if role not in _IMAGE_ARTIFACT_ROLES:
            raise DomainRuleError(
                "MP_COMFY_MEDIA_ROLE_UNSUPPORTED",
                "V2 Comfy 媒体引用不支持该语义槽位。",
                {"role": role},
            )
        resolved = _verified_image_media_version(database, media, project_id=project_id)
        # A business media version lives in the *project* tree, not under work_root:
        # resolving its rel_path against work_root is what made every explainer
        # image-to-video input fail with ``MP_COMFY_ARTIFACT_FILE_MISSING`` even
        # though the file was registered, hashed and readable.  The project root is
        # therefore resolved here and the file is verified against it.
        source = controlled_path(
            _project_root(settings, database, str(resolved["project_id"])),
            str(resolved["sandbox_rel_path"]),
            must_exist=True,
            require_file=True,
            code="MP_COMFY_MEDIA_FILE_MISSING",
        )
        result[str(raw_role)] = _copy_to_comfy_input(
            settings,
            source,
            content_id=resolved["id"],
            sha256=resolved["sha256"],
            prefix="mv",
        )
        receipts.append(
            {
                "role": role,
                "media_version_id": resolved["id"],
                "sha256": resolved["sha256"],
                "materialized_name": result[str(raw_role)],
            }
        )

    if receipts:
        existing = result.get(_MEDIA_INPUT_RECEIPT_KEY)
        merged = list(existing) if isinstance(existing, list) else []
        merged.extend(receipts)
        result[_MEDIA_INPUT_RECEIPT_KEY] = merged
    return result


def _artifact_id(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    if "artifact_id" not in value:
        return None
    if set(value) != {"artifact_id"} or not isinstance(value.get("artifact_id"), str) or not value["artifact_id"].strip():
        raise DomainRuleError("MP_COMFY_ARTIFACT_REFERENCE_INVALID", "V2 Comfy 制品引用必须且只能包含 artifact_id。")
    return cast(str, value["artifact_id"]).strip()


def _media_version_reference(value: Any) -> dict[str, str] | None:
    """Return the validated ``{media_version_id, sha256}`` reference, else ``None``.

    A mapping that *looks* like this branch but carries an extra key is rejected
    loudly: silently ignoring ``{"media_version_id":…, "path":"C:\\…"}`` would turn
    the bridge into a path inlet.
    """

    if not isinstance(value, Mapping):
        return None
    if "media_version_id" not in value and "sha256" not in value:
        return None
    if set(value) != _MEDIA_REFERENCE_KEYS:
        raise DomainRuleError(
            "MP_COMFY_MEDIA_REFERENCE_INVALID",
            "V2 Comfy 媒体引用必须且只能包含 media_version_id 与 sha256。",
            {"unexpected": sorted(set(value) - _MEDIA_REFERENCE_KEYS)},
        )
    media_version_id = value.get("media_version_id")
    sha256 = value.get("sha256")
    if not isinstance(media_version_id, str) or not media_version_id.strip():
        raise DomainRuleError("MP_COMFY_MEDIA_REFERENCE_INVALID", "媒体版本 ID 必须是非空字符串。")
    if not isinstance(sha256, str) or len(sha256) != 64 or not set(sha256.lower()) <= _HEX:
        raise DomainRuleError("MP_COMFY_MEDIA_REFERENCE_INVALID", "媒体引用必须携带 64 位十六进制 sha256。")
    return {"media_version_id": media_version_id.strip(), "sha256": sha256.lower()}


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


def _verified_image_media_version(
    database: Database,
    reference: Mapping[str, str],
    *,
    project_id: str | None,
) -> Mapping[str, str]:
    """Verify one business media version is a real, readable, same-project image."""

    with database.connect() as connection:
        row = connection.execute(
            """SELECT mv.id, ma.project_id AS project_id, ma.media_kind AS media_kind,
                      mv.integrity_status, mv.sha256, mv.rel_path
                 FROM media_versions mv
                 JOIN media_assets ma ON ma.id = mv.media_asset_id
                WHERE mv.id = ?""",
            (reference["media_version_id"],),
        ).fetchone()
    if row is None:
        raise DomainRuleError(
            "MP_COMFY_MEDIA_VERSION_UNKNOWN",
            "引用的媒体版本不存在，无法作为生成输入。",
            {"media_version_id": reference["media_version_id"]},
        )
    media_kind = str(row["media_kind"] or "").upper()
    if media_kind != "IMAGE":
        raise DomainRuleError(
            "MP_COMFY_MEDIA_KIND_INVALID",
            "该媒体引用不是 IMAGE，不能作为图像生成输入。",
            {"media_version_id": reference["media_version_id"], "media_kind": media_kind},
        )
    integrity = str(row["integrity_status"] or "").upper()
    if integrity != "VERIFIED":
        raise DomainRuleError(
            "MP_COMFY_MEDIA_INTEGRITY_NOT_VERIFIED",
            "该媒体版本未通过完整性校验，拒绝作为生成输入。",
            {"media_version_id": reference["media_version_id"], "integrity_status": integrity},
        )
    stored_sha = str(row["sha256"] or "").lower()
    if stored_sha != reference["sha256"]:
        raise DomainRuleError(
            "MP_COMFY_MEDIA_HASH_MISMATCH",
            "媒体版本的 sha256 与冻结快照不一致，拒绝作为生成输入。",
            {
                "media_version_id": reference["media_version_id"],
                "expected": reference["sha256"],
                "registered": stored_sha,
            },
        )
    if not project_id:
        raise DomainRuleError(
            "MP_COMFY_MEDIA_SCOPE_REQUIRED",
            "媒体引用必须携带冻结的项目范围，不能跨项目使用参考图。",
            {"media_version_id": reference["media_version_id"]},
        )
    if str(row["project_id"] or "") != str(project_id):
        raise DomainRuleError(
            "MP_COMFY_MEDIA_CROSS_PROJECT",
            "媒体版本不属于本次冻结的项目范围，跨项目引用需要显式授权。",
            {
                "media_version_id": reference["media_version_id"],
                "media_project_id": str(row["project_id"] or ""),
                "project_id": str(project_id),
            },
        )
    relative = str(row["rel_path"] or "").strip()
    if not relative:
        raise DomainRuleError(
            "MP_COMFY_MEDIA_PATH_MISSING",
            "媒体版本没有受控存储路径，无法作为生成输入。",
            {"media_version_id": reference["media_version_id"]},
        )
    return {
        "id": str(row["id"]),
        "project_id": str(row["project_id"]),
        "sandbox_rel_path": relative,
        "sha256": stored_sha,
    }


def _project_root(settings: Settings, database: Database, project_id: str) -> Path:
    """The controlled root of one project, resolved the way MediaService resolves it."""

    if not project_id:
        raise DomainRuleError(
            "MP_COMFY_MEDIA_SCOPE_REQUIRED", "媒体引用缺少项目范围，无法定位受控文件。"
        )
    with database.connect() as connection:
        row = connection.execute(
            "SELECT root_rel FROM projects WHERE id=?", (project_id,)
        ).fetchone()
    if row is None:
        raise DomainRuleError(
            "PROJECT_NOT_FOUND", "媒体引用所属的项目不存在。", {"project_id": project_id}
        )
    return settings.resolve_project_root(str(row["root_rel"]))


def _artifact_source(settings: Settings, artifact: Mapping[str, str]) -> Path:
    """A V2 job artifact path, which really is relative to the controlled work tree."""

    work_root = settings.work_root.resolve()
    return controlled_path(
        work_root,
        artifact["sandbox_rel_path"],
        must_exist=True,
        require_file=True,
        code="MP_COMFY_ARTIFACT_FILE_MISSING",
    )


def _copy_to_comfy_input(
    settings: Settings,
    source: Path,
    *,
    content_id: str,
    sha256: str,
    prefix: str = "v2",
) -> str:
    if settings.comfy_input_root is None:
        raise DomainRuleError("MP_COMFY_INPUT_ROOT_REQUIRED", "V2 Comfy 制品输入需要显式隔离的 Comfy input root。")
    input_root = settings.comfy_input_root.resolve()
    input_root.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}-{content_id}-{sha256[:12]}{source.suffix.lower()}"
    target = (input_root / filename).resolve()
    if not target.is_relative_to(input_root):
        raise DomainRuleError("MP_COMFY_INPUT_PATH_INVALID", "V2 Comfy 输入物化路径越界。")
    if not target.exists():
        partial = target.with_name(f".partial-{target.name}")
        shutil.copyfile(source, partial)
        replace_path(partial, target)
    if _sha256(target) != sha256:
        target.unlink(missing_ok=True)
        raise DomainRuleError("MP_COMFY_INPUT_INTEGRITY_MISMATCH", "V2 Comfy 输入物化后的 SHA-256 不一致。")
    return filename


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
