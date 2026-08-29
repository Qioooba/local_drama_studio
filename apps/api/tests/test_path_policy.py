from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from local_drama.bootstrap.config_loader import load_machine_config
from local_drama.bootstrap.resource_locator import ResourceLocator
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.policies import validate_project_code
from local_drama.infrastructure.filesystem.path_policy import (
    canonical_relative_path,
    controlled_path,
    path_is_within_roots,
    safe_filename,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (r"C:\\fakepath\\角色 01.png", "角色 01.png"),
        ("../scene?.mp4", "scene_.mp4"),
        ("CON.txt", "_CON.txt"),
        ("poster.png. ", "poster.png"),
        ("镜头:首帧.webp", "镜头_首帧.webp"),
    ],
)
def test_safe_filename_is_portable_to_a_windows_server(source: str, expected: str) -> None:
    assert safe_filename(source) == expected


@pytest.mark.parametrize(
    "value",
    ["../outside.mp4", r"C:\\outside.mp4", "a/CON.txt", "a/trailing. ", "a//b.txt", "a/./b.txt", "a/b.txt/"],
)
def test_canonical_relative_path_rejects_nonportable_or_escaping_values(value: str) -> None:
    with pytest.raises(DomainRuleError):
        canonical_relative_path(value)


def test_controlled_path_rejects_escape_and_accepts_posix_storage_key(tmp_path: Path) -> None:
    root = tmp_path / "projects" / "demo"
    target = root / "04_media" / "images" / "poster.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"png")
    assert controlled_path(root, "04_media/images/poster.png", must_exist=True, require_file=True) == target
    with pytest.raises(DomainRuleError):
        controlled_path(root, "../../secret.txt")


def test_model_allowlist_accepts_descendants_not_sibling_prefixes(tmp_path: Path) -> None:
    root = tmp_path / "models"
    sibling = tmp_path / "models-evil"
    root.mkdir()
    sibling.mkdir()
    allowed = root / "video.safetensors"
    denied = sibling / "video.safetensors"
    allowed.write_bytes(b"model")
    denied.write_bytes(b"model")
    assert path_is_within_roots(allowed, (root,), require_file=True)
    assert not path_is_within_roots(denied, (root,), require_file=True)


def test_model_allowlist_rejects_a_link_inside_the_configured_root(tmp_path: Path) -> None:
    root = tmp_path / "models"
    actual = root / "actual"
    actual.mkdir(parents=True)
    model = actual / "video.safetensors"
    model.write_bytes(b"model")
    alias = root / "alias"
    try:
        alias.symlink_to(actual, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this Windows test host")
    assert not path_is_within_roots(alias / model.name, (root,), require_file=True)


def test_machine_config_expands_relative_and_placeholder_model_roots_from_instance(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    release = tmp_path / "release"
    config_path = instance / "config" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        """{
          "schema_version": 1,
          "runtime": {
            "model_library_roots": ["models", "${INSTANCE_ROOT}/shared-models"]
          },
          "tools": {
            "fallback_dirs": ["tools", "${RELEASE_ROOT}/bundled-tools"]
          }
        }""",
        encoding="utf-8",
    )
    config = load_machine_config(config_path, release_root=release, instance_root=instance)
    assert config is not None
    assert config.runtime.model_library_roots == (
        (instance / "models").resolve(),
        (instance / "shared-models").resolve(),
    )
    assert config.tools.fallback_dirs == (
        str((instance / "tools").resolve()),
        str((release / "bundled-tools").resolve()),
    )


def test_resource_locator_anchors_relative_config_to_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    launch_dir = tmp_path / "service-cwd"
    launch_dir.mkdir()
    monkeypatch.chdir(launch_dir)
    monkeypatch.setenv("LOCAL_DRAMA_PACKAGED", "1")
    monkeypatch.setenv("LOCAL_DRAMA_INSTANCE_ROOT", str(instance))
    monkeypatch.setenv("LOCAL_DRAMA_CONFIG", "config/machine.json")
    locator = ResourceLocator.discover()
    assert locator.config_path == (instance / "config" / "machine.json").resolve()


@pytest.mark.parametrize("root_rel", ["../other", r"C:\\server-project", "/absolute/project"])
def test_settings_rejects_noncanonical_project_database_paths(tmp_path: Path, root_rel: str) -> None:
    settings = Settings(projects_root=tmp_path / "projects")
    with pytest.raises(DomainRuleError):
        settings.resolve_project_root(root_rel, must_exist=False)


def test_settings_rejects_storage_roots_that_alias_each_other(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="distinct directories"):
        Settings(data_root=tmp_path / "shared", work_root=tmp_path / "shared")


@pytest.mark.parametrize("code", ["con", "prn", "aux", "nul", "com1", "lpt9"])
def test_project_code_rejects_windows_reserved_device_names(code: str) -> None:
    with pytest.raises(DomainRuleError):
        validate_project_code(code)
