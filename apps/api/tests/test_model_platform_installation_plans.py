from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app
from local_drama.model_platform.application.installation_plans import (
    ExpectedInstallArtifact,
    InstallationPlanService,
    OfflineImportPlanRequest,
)


def _configured_library(database, settings, root) -> str:
    now = datetime.now(UTC).isoformat()
    node_id = "node-install-plan"
    library_id = "library-install-plan"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO mp_compute_nodes
            (id,code,display_name,fingerprint,host_json,last_seen_at,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (node_id, "local-install-plan", "本机服务节点", "install-plan-node", "{}", now, now, now),
        )
        connection.execute(
            """INSERT INTO mp_model_libraries
            (id,node_id,code,kind,root_path_local,managed,read_only,scan_policy_json,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (library_id, node_id, "model-library-pytorch", "MODEL_ROOT", str(root.resolve()), False, False, "{}", now, now),
        )
    return library_id


def test_offline_import_plan_persists_hash_bound_contract_without_source_path(workspace, database, tmp_path) -> None:
    library_root = tmp_path / "models" / "libraries" / "pytorch"
    library_root.mkdir(parents=True)
    settings = workspace.model_copy(update={"model_library_roots": (library_root,)})
    library_id = _configured_library(database, settings, library_root)

    plan = InstallationPlanService(database, settings).create_offline_import(
        OfflineImportPlanRequest(
            target_library_id=library_id,
            release_code="qwen3-embedding-8b",
            bundle_reference="qwen3-embedding-8b-20260829",
            license_id="apache-2.0",
            expected_artifacts=(
                ExpectedInstallArtifact("Embedding/model.safetensors", "a" * 64, 1234),
                ExpectedInstallArtifact("Embedding/config.json", "b" * 64, 88),
            ),
        )
    )

    assert plan.status == "AWAITING_OFFLINE_IMPORT"
    assert plan.expected_total_bytes == 1322
    listed = InstallationPlanService(database, settings).list()
    assert listed == [plan]
    with database.connect() as connection:
        source_json = connection.execute("SELECT source_json FROM mp_install_plans WHERE id=?", (plan.id,)).fetchone()[0]
    assert "qwen3-embedding-8b-20260829" in source_json
    assert str(library_root) not in source_json


def test_offline_import_plan_rejects_windows_paths_and_stale_library(workspace, database, tmp_path) -> None:
    library_root = tmp_path / "models" / "libraries" / "pytorch"
    library_root.mkdir(parents=True)
    settings = workspace.model_copy(update={"model_library_roots": (library_root,)})
    library_id = _configured_library(database, settings, library_root)
    service = InstallationPlanService(database, settings)

    with pytest.raises(DomainRuleError) as invalid_path:
        service.create_offline_import(OfflineImportPlanRequest(
            target_library_id=library_id,
            release_code="unsafe",
            bundle_reference="offline-bundle",
            license_id="license",
            expected_artifacts=(ExpectedInstallArtifact("F:\\private\\model.bin", "a" * 64, 1),),
        ))
    assert invalid_path.value.code == "MP_INSTALL_ARTIFACT_PATH_INVALID"

    stale = workspace.model_copy(update={"model_library_roots": (tmp_path / "other-library",)})
    with pytest.raises(DomainRuleError) as stale_target:
        InstallationPlanService(database, stale).create_offline_import(OfflineImportPlanRequest(
            target_library_id=library_id,
            release_code="stale",
            bundle_reference="offline-bundle",
            license_id="license",
            expected_artifacts=(ExpectedInstallArtifact("model.bin", "a" * 64, 1),),
        ))
    assert stale_target.value.code == "MP_INSTALL_TARGET_LIBRARY_STALE"


def test_offline_import_plan_api_returns_safe_summary_and_never_starts_file_operations(workspace, database, tmp_path) -> None:
    library_root = tmp_path / "models" / "libraries" / "pytorch"
    library_root.mkdir(parents=True)
    settings = workspace.model_copy(update={"model_library_roots": (library_root,)})
    library_id = _configured_library(database, settings, library_root)

    with TestClient(create_app(settings)) as client:
        targets = client.get("/api/v2/model-platform/installation-targets")
        created = client.post("/api/v2/model-platform/installation-plans/offline", json={
            "target_library_id": library_id,
            "release_code": "qwen3-embedding-8b",
            "bundle_reference": "offline-qwen3",
            "license_id": "apache-2.0",
            "expected_artifacts": [{"relative_path": "Embedding/model.safetensors", "sha256": "a" * 64, "size_bytes": 1234}],
        })
        listed = client.get("/api/v2/model-platform/installation-plans")

    assert targets.status_code == 200
    assert targets.json() == {
        "items": [{"id": library_id, "label": "模型库 pytorch"}],
        "count": 1,
        "read_only": True,
        "absolute_paths_exposed": False,
    }
    assert str(library_root) not in str(targets.json())
    assert created.status_code == 201
    payload = created.json()
    assert payload["file_operations_started"] is False
    assert payload["plan"]["status"] == "AWAITING_OFFLINE_IMPORT"
    assert "offline-qwen3" not in str(payload)
    assert str(library_root) not in str(payload)
    assert listed.status_code == 200
    assert listed.json()["host_import_available"] is False
    assert listed.json()["items"] == [payload["plan"]]
