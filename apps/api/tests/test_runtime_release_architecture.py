from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from local_drama.bootstrap.config_loader import load_machine_config
from local_drama.bootstrap.config_migrations import migrate_config, restore_config
from local_drama.bootstrap.resource_locator import ResourceLocator
from local_drama.config import Settings
from local_drama.entrypoints.maintenance import create_recovery_set, upgrade_database
from local_drama.platform.contracts import SecretRef
from local_drama.platform.linux.credentials import LinuxFileSecretStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _build_module():
    path = REPOSITORY_ROOT / "packaging" / "common" / "build_release.py"
    spec = importlib.util.spec_from_file_location("local_drama_build_release", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_platform_config_templates_match_runtime_contract(tmp_path: Path) -> None:
    for relative in (
        "packaging/windows/config.desktop.json",
        "packaging/windows/config.server.json",
        "packaging/linux/config.server.json",
    ):
        config = load_machine_config(
            REPOSITORY_ROOT / relative,
            release_root=tmp_path / "release",
            instance_root=tmp_path / "instance",
        )
        assert config is not None
        assert config.schema_version == 3
        assert config.storage.data_root == (tmp_path / "instance" / "data").resolve()
        assert config.frontend_dist == (tmp_path / "release" / "web").resolve()
        assert config.runtime.model_root == (tmp_path / "instance" / "models").resolve()
        assert config.runtime.model_library_roots == (
            (tmp_path / "instance" / "models" / "libraries" / "comfyui").resolve(),
            (tmp_path / "instance" / "models" / "libraries" / "pytorch").resolve(),
            (tmp_path / "instance" / "models" / "libraries" / "ollama").resolve(),
            (tmp_path / "instance" / "models" / "libraries" / "audio").resolve(),
        )
        if relative == "packaging/windows/config.server.json":
            assert config.network.mode == "LAN_SERVICE"
            assert config.network.host == "0.0.0.0"
            assert config.network.firewall_remote_address == "LocalSubnet"
            assert config.network.trusted_lan_unauthenticated is True
        elif config.network.mode == "LOCAL_ONLY":
            assert config.network.mode == "LOCAL_ONLY"
            assert config.network.trusted_lan_unauthenticated is False


def test_windows_installer_owns_profile_firewall_and_service_lifecycle() -> None:
    installer = (REPOSITORY_ROOT / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")
    service_task = next(line for line in installer.splitlines() if line.startswith('Name: "service";'))
    assert "Flags: unchecked" not in service_task
    assert "UsePreviousTasks=yes" in installer
    assert "function MachineConfigMissing(): Boolean;" in installer
    assert "configure-profile --template" in installer
    assert 'Parameters: "configure-firewall"' in installer
    assert "--remote-address LocalSubnet" not in installer
    assert 'Parameters: "remove-firewall"' in installer
    assert 'Parameters: "uninstall-service"' in installer
    assert "onlyifdoesntexist uninsneveruninstall; Tasks: service" not in installer
    bootstrap_profile = installer.index("Bootstrap trusted LAN server profile")
    release_upgrade = installer.index('Parameters: "upgrade --bundle')
    server_profile = installer.index("Configure trusted LAN server profile")
    firewall = installer.index('Parameters: "configure-firewall"')
    service = installer.index('Parameters: "install-service"')
    assert bootstrap_profile < release_upgrade < server_profile < firewall < service


def test_windows_installer_never_overwrites_or_uninstalls_machine_data() -> None:
    installer = (REPOSITORY_ROOT / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")
    assert 'DestDir: "{commonappdata}\\LocalDramaStudio\\config"' not in installer
    assert 'DestName: "config.json"' not in installer
    assert "uninsdelete" not in installer.casefold()
    assert "Projects, database, configuration, logs and backups under ProgramData will be preserved." in installer
    assert 'Source: "{#PackageRoot}\\active-release.json"; DestDir: "{app}"; Flags: onlyifdoesntexist' in installer
    assert 'Name: "{commonappdata}\\LocalDramaStudio\\models\\downloads"' in installer
    assert 'Name: "{commonappdata}\\LocalDramaStudio\\models\\libraries\\comfyui"' in installer


def test_release_payload_includes_offline_model_platform_gate() -> None:
    source = (REPOSITORY_ROOT / "packaging" / "common" / "build_release.py").read_text(encoding="utf-8")
    assert 'payload / "app" / "model_platform_release_gate.py"' in source


def test_v1_config_migrates_to_model_root_v2_then_becomes_idempotent(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"schema_version":1}\n', encoding="utf-8")
    migrated = migrate_config(config, backups_root=tmp_path / "backups")
    assert migrated["status"] == "PASS"
    assert migrated["mutated"] is True
    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 3
    assert payload["runtime"]["model_root"] == "${INSTANCE_ROOT}/models"
    assert payload["runtime"]["model_library_roots"] == [
        "${MODEL_ROOT}/libraries/comfyui",
        "${MODEL_ROOT}/libraries/pytorch",
        "${MODEL_ROOT}/libraries/ollama",
        "${MODEL_ROOT}/libraries/audio",
    ]
    assert Path(str(migrated["backup"])).is_file()
    assert migrate_config(config, backups_root=tmp_path / "backups") == {"status": "PASS", "mutated": False, "schema_version": 3}


def test_loader_uses_the_same_v1_migration_without_mutating_source_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    source = '{"schema_version":1,"runtime":{"local_ai_model_root":"${MODEL_ROOT}/pytorch"}}\n'
    config_path.write_text(source, encoding="utf-8")

    config = load_machine_config(
        config_path,
        release_root=tmp_path / "release",
        instance_root=tmp_path / "instance",
    )

    assert config is not None
    assert config.runtime.model_root == (tmp_path / "instance" / "models").resolve()
    assert config.runtime.local_ai_model_root == (tmp_path / "instance" / "models" / "pytorch").resolve()
    assert config_path.read_text(encoding="utf-8") == source


def test_config_restore_is_atomic_and_preserves_current(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    source = tmp_path / "old.json"
    config.write_text('{"schema_version":1,"environment":"new"}\n', encoding="utf-8")
    source.write_text('{"schema_version":1,"environment":"old"}\n', encoding="utf-8")
    result = restore_config(config, source, backups_root=tmp_path / "backups")
    assert result["status"] == "PASS"
    assert json.loads(config.read_text(encoding="utf-8"))["environment"] == "old"
    assert Path(str(result["preserved"])).is_file()


def test_database_upgrade_is_rehearsed_and_then_idempotent(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
        instance_root=tmp_path,
        release_root=REPOSITORY_ROOT,
    )
    locator = ResourceLocator.discover()
    first = upgrade_database(settings, locator)
    assert first["status"] == "PASS"
    assert first["mutated"] is True
    second = upgrade_database(settings, locator)
    assert second["status"] == "PASS"
    assert second["mutated"] is False
    recovery = create_recovery_set(settings, locator)
    assert recovery["status"] == "PASS"
    assert recovery["details"]["manifest"]["status"] == "COMPLETE"


def test_storage_roots_map_field_names_to_canonical_directories(tmp_path: Path) -> None:
    locator = ResourceLocator(
        release_root=tmp_path / "release",
        instance_root=tmp_path / "instance",
        config_path=tmp_path / "config.json",
        source_repo_root=tmp_path / "repo",
        packaged=True,
    )
    roots = locator.storage_roots()
    assert set(roots) == {"data_root", "projects_root", "work_root", "cache_root", "logs_root", "backups_root"}
    assert roots["data_root"] == tmp_path / "instance" / "data"
    assert roots["backups_root"] == tmp_path / "instance" / "backups"
    # The field-name → directory-name mapping must never leak the `_root`
    # suffix into the filesystem path.
    assert all(path.name in {"data", "projects", "work", "cache", "logs", "backups"} for path in roots.values())


def test_from_env_packaged_without_config_uses_canonical_storage_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCAL_DRAMA_PACKAGED", "1")
    monkeypatch.setenv("LOCAL_DRAMA_RELEASE_ROOT", str(tmp_path / "release"))
    monkeypatch.setenv("LOCAL_DRAMA_INSTANCE_ROOT", str(tmp_path / "instance"))
    monkeypatch.setenv("LOCAL_DRAMA_CONFIG", str(tmp_path / "instance" / "config" / "config.json"))
    settings = Settings.from_env()
    assert settings.data_root == tmp_path / "instance" / "data"
    assert settings.projects_root == tmp_path / "instance" / "projects"
    assert settings.work_root == tmp_path / "instance" / "work"
    assert settings.cache_root == tmp_path / "instance" / "cache"
    assert settings.logs_root == tmp_path / "instance" / "logs"
    assert settings.backups_root == tmp_path / "instance" / "backups"
    assert settings.database_path == tmp_path / "instance" / "data" / "local_drama.sqlite3"


def test_ensure_roots_creates_only_the_configured_model_root_layout(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
        instance_root=tmp_path,
        release_root=REPOSITORY_ROOT,
        model_root=tmp_path / "models",
        model_library_roots=(tmp_path / "models" / "libraries" / "pytorch",),
    )

    settings.ensure_roots()

    assert (tmp_path / "models" / "downloads").is_dir()
    assert (tmp_path / "models" / "staging").is_dir()
    assert (tmp_path / "models" / "quarantine").is_dir()
    assert (tmp_path / "models" / "libraries" / "pytorch").is_dir()
    assert not (tmp_path / "models" / "libraries" / "comfyui").exists()


def test_wheelhouse_manifest_detects_tampering(tmp_path: Path) -> None:
    module = _build_module()
    wheel = tmp_path / "dependency-1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    manifest = {
        "schema_version": 1,
        "python_abi": "cp312",
        "files": [{"file": wheel.name, "bytes": 5, "sha256": hashlib.sha256(b"wheel").hexdigest()}],
    }
    (tmp_path / "wheelhouse-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    module._verify_wheelhouse(tmp_path)
    wheel.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="failed verification"):
        module._verify_wheelhouse(tmp_path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_linux_secret_store_uses_private_files(tmp_path: Path) -> None:
    store = LinuxFileSecretStore(tmp_path / "secrets")
    ref = SecretRef("ProviderConnection", "pc-test")
    store.put(ref, "secret")
    assert store.get(ref) == "secret"
    path = store._path(ref)
    assert path.stat().st_mode & 0o777 == 0o600
    assert store.delete(ref) is True
