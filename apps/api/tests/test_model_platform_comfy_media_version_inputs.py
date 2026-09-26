"""The controlled ``media_version_id`` branch of the V2 Comfy input bridge (§D4.3).

The bridge originally accepted exactly one reference shape, ``{"artifact_id": …}``,
and required the artifact to be a ``VERIFIED`` ``COMFY_OUTPUT`` produced by a real
``MODEL_PLATFORM_EXECUTION`` job.  That rule is correct and stays untouched — but it
also meant a beat's adopted still, a character HERO reference or an uploaded photo
could never be used as generation input, because those are legitimate
``media_versions`` that no V2 job produced.  The design therefore asks for a small,
*controlled* second branch instead of a generic path inlet.

These tests pin exactly how narrow that branch is:

* only the frozen project's own media, only ``IMAGE``, only ``VERIFIED``, only a
  matching sha256;
* ``{"media_version_id": …}`` with an extra key (a path, a URL, a role) is refused
  rather than partially honoured;
* a missing project scope is refused, so the bridge can never be used to reach
  another project's files;
* the accepted branch really materialises a byte-identical copy into the isolated
  Comfy input root and records a receipt naming the media version and hash.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.comfy_artifact_inputs import materialize_v2_comfy_artifact_inputs


class _MediaDatabase:
    """Fake database that answers the media-version read and the project-root read.

    The bridge resolves a business media version through its *project* root, so the
    fake has to answer both queries rather than returning one row for everything.
    """

    def __init__(self, row: dict[str, object] | None, *, project_root_rel: str | None = "project-1") -> None:
        self.row = row
        self.project_root_rel = project_root_rel
        self.queries: list[str] = []

    @contextmanager
    def connect(self):
        def execute(sql: str, *_args: object) -> object:
            self.queries.append(sql)
            if "FROM projects" in sql:
                if self.project_root_rel is None:
                    return SimpleNamespace(fetchone=lambda: None)
                return SimpleNamespace(fetchone=lambda: {"root_rel": self.project_root_rel})
            return SimpleNamespace(fetchone=lambda: self.row)

        yield SimpleNamespace(execute=execute)


def _media_row(workspace, *, project_id: str = "project-1", media_kind: str = "IMAGE", integrity: str = "VERIFIED",
               sha256: str | None = None, rel_path: str = "media/ref.png") -> dict[str, object]:
    # A real ``media_versions.rel_path`` is relative to the *project* root, which is
    # why resolving it against work_root was the bug this test now pins.
    source = workspace.projects_root / "project-1" / rel_path
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        source.write_bytes(b"adopted-still")
    digest = sha256 or hashlib.sha256(source.read_bytes()).hexdigest()
    return {
        "id": "media-version-1",
        "project_id": project_id,
        "media_kind": media_kind,
        "integrity_status": integrity,
        "sha256": digest,
        "rel_path": rel_path,
    }


def test_a_verified_same_project_image_is_materialized_with_a_receipt(workspace) -> None:
    row = _media_row(workspace)
    database = _MediaDatabase(row)

    inputs = materialize_v2_comfy_artifact_inputs(
        database,  # type: ignore[arg-type]
        workspace,
        {"REFERENCE_IMAGE": {"media_version_id": "media-version-1", "sha256": row["sha256"]}},
        project_id="project-1",
    )

    name = inputs["REFERENCE_IMAGE"]
    assert name.startswith("mv-media-version-1-")
    assert (workspace.comfy_input_root / name).read_bytes() == (
        workspace.projects_root / "project-1" / "media/ref.png"
    ).read_bytes()
    receipt = inputs["__media_version_inputs__"]
    assert receipt == [
        {
            "role": "REFERENCE_IMAGE",
            "media_version_id": "media-version-1",
            "sha256": row["sha256"],
            "materialized_name": name,
        }
    ]


def test_a_media_version_whose_file_is_missing_from_the_project_root_is_refused(workspace) -> None:
    """The project root really is the authority: work_root is not searched."""

    row = _media_row(workspace)
    # The identical relative path exists under work_root, which is exactly the
    # location that used to be (wrongly) consulted.
    decoy = workspace.work_root / "media/ref.png"
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_bytes(b"adopted-still")
    (workspace.projects_root / "project-1" / "media/ref.png").unlink()

    with pytest.raises(DomainRuleError) as raised:
        materialize_v2_comfy_artifact_inputs(
            _MediaDatabase(row),  # type: ignore[arg-type]
            workspace,
            {"REFERENCE_IMAGE": {"media_version_id": "media-version-1", "sha256": row["sha256"]}},
            project_id="project-1",
        )
    assert raised.value.code == "MP_COMFY_MEDIA_FILE_MISSING"


def test_a_cross_project_media_version_is_refused(workspace) -> None:
    row = _media_row(workspace, project_id="other-project")
    with pytest.raises(DomainRuleError) as raised:
        materialize_v2_comfy_artifact_inputs(
            _MediaDatabase(row),  # type: ignore[arg-type]
            workspace,
            {"REFERENCE_IMAGE": {"media_version_id": "media-version-1", "sha256": row["sha256"]}},
            project_id="project-1",
        )
    assert raised.value.code == "MP_COMFY_MEDIA_CROSS_PROJECT"


def test_a_missing_project_scope_is_refused(workspace) -> None:
    row = _media_row(workspace)
    with pytest.raises(DomainRuleError) as raised:
        materialize_v2_comfy_artifact_inputs(
            _MediaDatabase(row),  # type: ignore[arg-type]
            workspace,
            {"REFERENCE_IMAGE": {"media_version_id": "media-version-1", "sha256": row["sha256"]}},
        )
    assert raised.value.code == "MP_COMFY_MEDIA_SCOPE_REQUIRED"


def test_a_hash_mismatch_is_refused(workspace) -> None:
    row = _media_row(workspace, sha256="c" * 64)
    with pytest.raises(DomainRuleError) as raised:
        materialize_v2_comfy_artifact_inputs(
            _MediaDatabase(row),  # type: ignore[arg-type]
            workspace,
            {"REFERENCE_IMAGE": {"media_version_id": "media-version-1", "sha256": "d" * 64}},
            project_id="project-1",
        )
    assert raised.value.code == "MP_COMFY_MEDIA_HASH_MISMATCH"


def test_an_unverified_media_version_is_refused(workspace) -> None:
    row = _media_row(workspace, integrity="PARTIAL")
    with pytest.raises(DomainRuleError) as raised:
        materialize_v2_comfy_artifact_inputs(
            _MediaDatabase(row),  # type: ignore[arg-type]
            workspace,
            {"REFERENCE_IMAGE": {"media_version_id": "media-version-1", "sha256": row["sha256"]}},
            project_id="project-1",
        )
    assert raised.value.code == "MP_COMFY_MEDIA_INTEGRITY_NOT_VERIFIED"


def test_a_non_image_media_version_is_refused(workspace) -> None:
    row = _media_row(workspace, media_kind="VIDEO")
    with pytest.raises(DomainRuleError) as raised:
        materialize_v2_comfy_artifact_inputs(
            _MediaDatabase(row),  # type: ignore[arg-type]
            workspace,
            {"REFERENCE_IMAGE": {"media_version_id": "media-version-1", "sha256": row["sha256"]}},
            project_id="project-1",
        )
    assert raised.value.code == "MP_COMFY_MEDIA_KIND_INVALID"


def test_an_extra_key_turns_the_reference_into_an_error(workspace) -> None:
    """``{"media_version_id": …, "path": "C:\\…"}`` must never be half-honoured."""

    with pytest.raises(DomainRuleError) as raised:
        materialize_v2_comfy_artifact_inputs(
            _MediaDatabase(None),  # type: ignore[arg-type]
            workspace,
            {"REFERENCE_IMAGE": {"media_version_id": "media-version-1", "sha256": "a" * 64, "path": "C:/secret.png"}},
            project_id="project-1",
        )
    assert raised.value.code == "MP_COMFY_MEDIA_REFERENCE_INVALID"


def test_a_malformed_sha256_is_refused(workspace) -> None:
    with pytest.raises(DomainRuleError) as raised:
        materialize_v2_comfy_artifact_inputs(
            _MediaDatabase(None),  # type: ignore[arg-type]
            workspace,
            {"REFERENCE_IMAGE": {"media_version_id": "media-version-1", "sha256": "short"}},
            project_id="project-1",
        )
    assert raised.value.code == "MP_COMFY_MEDIA_REFERENCE_INVALID"


def test_a_media_reference_is_refused_in_a_non_image_role(workspace) -> None:
    row = _media_row(workspace)
    with pytest.raises(DomainRuleError) as raised:
        materialize_v2_comfy_artifact_inputs(
            _MediaDatabase(row),  # type: ignore[arg-type]
            workspace,
            {"PROMPT": {"media_version_id": "media-version-1", "sha256": row["sha256"]}},
            project_id="project-1",
        )
    assert raised.value.code == "MP_COMFY_MEDIA_ROLE_UNSUPPORTED"


def test_scalar_inputs_are_untouched(workspace) -> None:
    inputs = materialize_v2_comfy_artifact_inputs(
        _MediaDatabase(None),  # type: ignore[arg-type]
        workspace,
        {"PROMPT": "雨夜的橘猫", "WIDTH": 1024},
        project_id="project-1",
    )
    assert inputs == {"PROMPT": "雨夜的橘猫", "WIDTH": 1024}
