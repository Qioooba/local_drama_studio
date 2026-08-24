from __future__ import annotations

from local_drama.application.profiles import ProfileService


def test_profile_version_returns_normalized_execution_detail(workspace, database) -> None:
    service = ProfileService(database, workspace.manifest_path)
    synced = service.sync_manifest()
    version_id = str(synced["profiles"][0]["version_id"])

    detail = service.get_version(version_id)
    execution = detail["execution"]

    assert detail["model_bundle"]["runtime_id"] == synced["runtime"]["id"]
    assert execution["schema_version"] == "localdrama.profile-execution-detail.v1"
    assert execution["runtime"]["id"] == synced["runtime"]["id"]
    assert execution["runtime"]["status"] in {"AVAILABLE", "BLOCKED_OFFLINE"}
    assert execution["components"]
    assert all("artifact_id" in component and "role" in component for component in execution["components"])
    assert execution["fingerprints"]["execution"]
    assert execution["read_only"] is True
    assert execution["local_only"] is True


def test_profile_version_normalizes_legacy_artifact_only_bundle(workspace, database) -> None:
    service = ProfileService(database, workspace.manifest_path)
    synced = service.sync_manifest()
    version_id = str(synced["profiles"][0]["version_id"])
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET model_bundle_json=? WHERE id=?",
            ('{"artifact_ids": []}', version_id),
        )

    detail = service.get_version(version_id)
    assert detail["execution"]["components"] == []
    assert detail["execution"]["model_bundle"] == {"artifact_ids": []}
