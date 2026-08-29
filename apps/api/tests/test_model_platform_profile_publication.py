from __future__ import annotations

from dataclasses import replace

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.profile_publication import ProfilePublicationService, ProfileVersionDraft


def _prepare_references(database) -> str:
    with database.transaction() as connection:
        capability_id = connection.execute(
            "SELECT id FROM mp_capability_definitions WHERE code='EMBEDDING_TEXT'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO mp_compute_nodes (id,code,display_name,fingerprint,host_json,last_seen_at,created_at,updated_at)
            VALUES ('profile-node','profile-node','Profile Node','profile-node-fingerprint','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT INTO mp_runtime_installations (id,node_id,code,kind,owner_mode,display_name,created_at,updated_at)
            VALUES ('profile-runtime','profile-node','pytorch','PYTORCH_PROCESS','SERVICE_MANAGED','PyTorch',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT INTO mp_runtime_installation_versions
            (id,runtime_installation_id,version_no,adapter_code,adapter_version,transport,configuration_json,fingerprint,status,created_at,updated_at)
            VALUES ('profile-runtime-v1','profile-runtime',1,'pytorch.embedding.qwen3','v1','LOCAL_PROCESS','{}','profile-runtime-fingerprint','ACTIVE',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT INTO mp_model_families (id,code,title,vendor,license_json,created_at,updated_at)
            VALUES ('profile-family','profile-family','Profile test model',NULL,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT INTO mp_model_releases
            (id,family_id,code,upstream_id,revision,format,quantization,metadata_json,created_at,updated_at)
            VALUES ('profile-release','profile-family','profile-release','profile-release','v1','TEST',NULL,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT INTO mp_runtime_model_installations
            (id,release_id,runtime_installation_version_id,native_locator,install_state,metadata_json,created_at,updated_at)
            VALUES ('profile-runtime-model','profile-release','profile-runtime-v1','profile-native','READY','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT INTO mp_capability_offerings
            (id,runtime_model_installation_id,capability_definition_id,native_metadata_json,validation_status,created_at,updated_at)
            VALUES ('profile-offering','profile-runtime-model',?,'{}','SMOKE_PASSED',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (capability_id,),
        )
        connection.execute(
            """INSERT INTO mp_validation_runs
            (id,target_kind,target_id,validation_kind,status,result_json,started_at,finished_at,created_at,updated_at)
            VALUES ('profile-source-smoke','CAPABILITY_OFFERING','profile-offering','CAPABILITY_SMOKE','SMOKE_PASSED','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT INTO mp_validation_evidence
            (id,validation_run_id,kind,content_hash,payload_json,artifact_ref,created_at,updated_at)
            VALUES ('profile-source-evidence','profile-source-smoke','CAPABILITY_SMOKE','test-hash','{}',NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT INTO mp_parameter_contract_versions
            (id,capability_definition_id,version_no,schema_json,ui_schema_json,content_hash,created_at,updated_at)
            VALUES ('parameter-v1',?,1,'{}','{}','parameter-hash',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (capability_id,),
        )
        connection.execute(
            """INSERT INTO mp_adapter_binding_contract_versions
            (id,runtime_kind,adapter_code,version_no,binding_json,content_hash,created_at,updated_at)
            VALUES ('binding-v1','PYTORCH_PROCESS','pytorch.embedding.qwen3',1,'{}','binding-hash',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
        connection.execute(
            """INSERT INTO mp_resource_policy_versions
            (id,code,version_no,policy_json,content_hash,created_at,updated_at)
            VALUES ('resource-v1','gpu-heavy',1,'{}','resource-hash',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
        )
    return str(capability_id)


def _draft(capability_id: str) -> ProfileVersionDraft:
    return ProfileVersionDraft(
        profile_code="qwen-embedding",
        profile_title="Qwen Embedding",
        capability_definition_id=capability_id,
        runtime_installation_version_id="profile-runtime-v1",
        parameter_contract_version_id="parameter-v1",
        adapter_binding_contract_version_id="binding-v1",
        resource_policy_version_id="resource-v1",
        payload={"runtime_model_installation_ids": ["profile-runtime-model"], "defaults": {"normalize": True}},
    )


def test_profile_candidate_is_immutable_and_requires_payload_matched_smoke_evidence(database) -> None:
    capability_id = _prepare_references(database)
    service = ProfilePublicationService(database)
    with pytest.raises(DomainRuleError) as unbound:
        service.create_candidate(
            replace(_draft(capability_id), profile_code="unbound-embedding", payload={"defaults": {"normalize": True}})
        )
    assert unbound.value.code == "MP_PROFILE_RUNTIME_BINDING_REQUIRED"
    created = service.create_candidate(_draft(capability_id))

    with pytest.raises(DomainRuleError) as duplicate:
        service.create_candidate(_draft(capability_id))
    assert duplicate.value.code == "MP_PROFILE_PAYLOAD_ALREADY_EXISTS"

    with pytest.raises(DomainRuleError) as mismatch:
        service.record_validation(
            created.profile_version_id,
            validation_kind="PROFILE_SMOKE",
            status="SMOKE_PASSED",
            result={"payload_hash": "wrong", "source_capability_validation_run_id": "profile-source-smoke"},
            evidence={"result": "pass"},
    )
    assert mismatch.value.code == "MP_VALIDATION_PAYLOAD_MISMATCH"

    with pytest.raises(DomainRuleError) as missing_source:
        service.record_validation(
            created.profile_version_id,
            validation_kind="PROFILE_SMOKE",
            status="SMOKE_PASSED",
            result={"payload_hash": created.payload_hash},
            evidence={"result": "pass"},
        )
    assert missing_source.value.code == "MP_PROFILE_VALIDATION_SOURCE_REQUIRED"

    validation = service.record_validation(
        created.profile_version_id,
        validation_kind="PROFILE_SMOKE",
        status="SMOKE_PASSED",
        result={"payload_hash": created.payload_hash, "output_contract_verified": True, "source_capability_validation_run_id": "profile-source-smoke"},
        evidence={"adapter": "pytorch.embedding.qwen3", "status": "pass"},
    )
    service.publish(created.profile_version_id, validation_run_id=validation.validation_run_id, reason="verified locally")

    with database.connect() as connection:
        publication = connection.execute(
            "SELECT status, validation_run_id FROM mp_profile_publications WHERE execution_profile_version_id=?",
            (created.profile_version_id,),
        ).fetchone()
    assert tuple(publication) == ("PUBLISHED", validation.validation_run_id)

    with pytest.raises(DomainRuleError) as republish:
        service.publish(created.profile_version_id, validation_run_id=validation.validation_run_id, reason="again")
    assert republish.value.code == "MP_PROFILE_ALREADY_PUBLISHED"


def test_profile_cannot_publish_failed_validation(database) -> None:
    capability_id = _prepare_references(database)
    service = ProfilePublicationService(database)
    created = service.create_candidate(_draft(capability_id))
    validation = service.record_validation(
        created.profile_version_id,
        validation_kind="PROFILE_SMOKE",
        status="FAILED",
        result={"payload_hash": created.payload_hash, "source_capability_validation_run_id": "profile-source-smoke"},
        evidence={"error_code": "ADAPTER_SMOKE_FAILED"},
    )

    with pytest.raises(DomainRuleError) as raised:
        service.publish(created.profile_version_id, validation_run_id=validation.validation_run_id, reason="not allowed")

    assert raised.value.code == "MP_PUBLICATION_VALIDATION_NOT_PASSED"
