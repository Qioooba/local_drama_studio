from __future__ import annotations

import json

import pytest

from local_drama.application.profiles import ProfileService
from local_drama.domain.errors import DomainRuleError


def _contracts() -> dict[str, dict[str, object]]:
    return {
        "input_contract": {"transport": "LOOPBACK_HTTP", "input_slots": {"FIRST_FRAME": {"min": 1, "max": 1}}},
        "parameter_schema": {"seed": {"type": "integer", "determinism": "EXPLICIT"}},
        "output_contract": {"media_kind": "VIDEO", "container": "mp4"},
        "resource_policy": {"gpu_heavy_concurrency": 1, "worker_policy": "EPHEMERAL"},
    }


def test_profile_contract_editor_derives_immutable_version_and_validates_without_runtime(workspace, database) -> None:
    service = ProfileService(database, workspace.manifest_path)
    source = service.sync_manifest()["profiles"][0]
    contracts = _contracts()
    draft = service.derive_contract_version(
        str(source["version_id"]),
        1,
        contracts["input_contract"],
        contracts["parameter_schema"],
        contracts["output_contract"],
        contracts["resource_policy"],
    )
    validation = service.validate_contract_version(str(draft["id"]))

    assert draft["status"] == "DRAFT"
    assert draft["version_no"] == 2
    assert validation["status"] == "PASS"
    assert validation["runtime_contacted"] is False
    assert validation["network_contacted"] is False
    original = service.get_version(str(source["version_id"]))
    assert original["version_no"] == 1
    assert original["input_contract"] != draft["input_contract"]


def test_profile_contract_publish_requires_real_evidence_when_execution_fingerprint_changes(workspace, database) -> None:
    service = ProfileService(database, workspace.manifest_path)
    source = service.sync_manifest()["profiles"][0]
    with database.transaction() as connection:
        connection.execute("UPDATE execution_profile_versions SET status='PUBLISHED' WHERE id=?", (source["version_id"],))
    contracts = _contracts()
    draft = service.derive_contract_version(
        str(source["version_id"]),
        1,
        contracts["input_contract"],
        contracts["parameter_schema"],
        contracts["output_contract"],
        contracts["resource_policy"],
    )
    service.validate_contract_version(str(draft["id"]))

    with pytest.raises(DomainRuleError) as raised:
        service.publish_validated_contract(str(draft["id"]))
    assert raised.value.code == "PROFILE_REAL_EVIDENCE_REQUIRED"
    assert service.get_version(str(draft["id"]))["status"] == "DRAFT"


def test_profile_contract_publish_allows_validated_no_execution_change_clone(workspace, database) -> None:
    service = ProfileService(database, workspace.manifest_path)
    source = service.sync_manifest()["profiles"][0]
    contracts = _contracts()
    with database.transaction() as connection:
        connection.execute(
            """UPDATE execution_profile_versions SET status='PUBLISHED', input_contract_json=?,
            parameter_schema_json=?, output_contract_json=?, resource_policy_json=? WHERE id=?""",
            (
                json.dumps(contracts["input_contract"]),
                json.dumps(contracts["parameter_schema"]),
                json.dumps(contracts["output_contract"]),
                json.dumps(contracts["resource_policy"]),
                source["version_id"],
            ),
        )
    draft = service.derive_contract_version(
        str(source["version_id"]),
        1,
        contracts["input_contract"],
        contracts["parameter_schema"],
        contracts["output_contract"],
        contracts["resource_policy"],
    )
    service.validate_contract_version(str(draft["id"]))
    published = service.publish_validated_contract(str(draft["id"]))

    assert published["status"] == "PUBLISHED"
    assert published["revision"] == 2


def test_profile_contract_validation_rejects_incomplete_local_contract(workspace, database) -> None:
    service = ProfileService(database, workspace.manifest_path)
    source = service.sync_manifest()["profiles"][0]
    draft = service.derive_contract_version(
        str(source["version_id"]), 1, {"transport": "REMOTE_HTTP_SERVICE"}, {}, {}, {}
    )
    validation = service.validate_contract_version(str(draft["id"]))
    assert validation["status"] == "FAIL"
    failed = {item["code"] for item in validation["checks"] if not item["passed"]}
    assert {"PARAMETER_SCHEMA", "OUTPUT_CONTRACT", "RESOURCE_POLICY", "LOCAL_TRANSPORT"}.issubset(failed)


@pytest.mark.parametrize("field_name", ["api_key", "client_secret", "provider_url", "remote_endpoint"])
def test_profile_contract_rejects_reserved_remote_configuration_without_persisting(workspace, database, field_name: str) -> None:
    service = ProfileService(database, workspace.manifest_path)
    source = service.sync_manifest()["profiles"][0]
    contracts = _contracts()
    contracts["parameter_schema"] = {"nested": [{field_name: "must-not-persist"}]}

    with pytest.raises(DomainRuleError) as raised:
        service.derive_contract_version(
            str(source["version_id"]),
            1,
            contracts["input_contract"],
            contracts["parameter_schema"],
            contracts["output_contract"],
            contracts["resource_policy"],
        )

    assert raised.value.code == "LOCAL_CONFIG_FIELD_FORBIDDEN"
    assert raised.value.details["field_path"] == f"parameter_schema.nested[0].{field_name}"
    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM execution_profile_versions WHERE execution_profile_id=?", (source["id"],)).fetchone()[0]
    assert count == 1
