from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.video_upscale.windows_uat import (
    _ISOLATION_MARKER,
    _SCHEMA,
    _SESSION_SCHEMA,
    _existing_isolation_root,
    _load_json,
    _new_isolation_root,
    _owned_file,
    finalize,
    prepare,
)


def test_isolation_root_must_be_new_absolute_and_marker_owned(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="must be absolute"):
        _new_isolation_root("relative-uat")

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(RuntimeError, match="must not already exist"):
        _new_isolation_root(str(existing))

    root = _new_isolation_root(str(tmp_path / "new-uat"))
    assert (root / _ISOLATION_MARKER).read_text(encoding="utf-8").strip() == _SCHEMA
    assert _existing_isolation_root(str(root)) == root

    (root / _ISOLATION_MARKER).write_text("foreign\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="marker is invalid"):
        _existing_isolation_root(str(root))


def test_prepare_requires_explicit_contract_before_creating_root(tmp_path: Path) -> None:
    root = tmp_path / "must-not-be-created"
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"schema_version": _SCHEMA, "confirm_isolated_test": False, "isolation_root": str(root)}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="confirm_isolated_test=true"):
        prepare(config)
    assert not root.exists()


def test_finalize_requires_human_attestation_before_opening_instance(tmp_path: Path) -> None:
    session_path = tmp_path / "session.json"
    session_path.write_text(
        json.dumps(
            {
                "schema_version": _SESSION_SCHEMA,
                "state": "AWAITING_HUMAN_VISUAL_REVIEW",
                "isolation_root": str(tmp_path / "does-not-exist"),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="I_REVIEWED_EVERY_OUTPUT"):
        finalize(session_path, reviewer="reviewer", confirmation="NOT_REVIEWED")


def test_json_contract_rejects_non_object(tmp_path: Path) -> None:
    contract = tmp_path / "contract.json"
    contract.write_text("[]", encoding="utf-8")
    with pytest.raises(TypeError, match="must be an object"):
        _load_json(contract)


def test_owned_file_rejects_project_escape(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"not-a-project-file")

    with pytest.raises(RuntimeError, match="escapes or is missing"):
        _owned_file(project_root, "../outside.mp4", label="reviewed output")
