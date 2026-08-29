from __future__ import annotations

from local_drama.model_platform.application.legacy_inventory import LegacyModelPlatformInventoryReader


def test_legacy_inventory_is_read_only_and_makes_uncertainty_explicit(database) -> None:
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO local_runtimes
            (id,code,title,transport,base_url,executable_ref,runtime_version,status,details_json,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES ('legacy-ollama','ollama-local','Ollama 本机','LOOPBACK_HTTP','http://127.0.0.1:11434',
                    'ollama','0.11','AVAILABLE','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )
        connection.execute(
            """INSERT INTO model_artifacts
            (id,runtime_id,code,kind,machine_path_ref,sha256,size_bytes,license_note,compatibility_json,status,
             manifest_sha256,created_at,updated_at,created_by,revision,schema_version)
            VALUES ('legacy-artifact','legacy-ollama','nomic-embed','OLLAMA_MODEL','ollama:nomic-embed-text',
                    NULL,NULL,'verify','{not-json}','CANDIDATE',NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )
        connection.execute(
            """INSERT INTO execution_profiles
            (id,code,title,created_at,updated_at,created_by,revision,schema_version)
            VALUES ('legacy-profile','knowledge-index','知识库向量化',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id,execution_profile_id,version_no,capability,runtime_version_id,workflow_version_id,model_bundle_json,
             input_contract_json,parameter_schema_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES ('legacy-profile-v1','legacy-profile',1,'EMBEDDING','legacy-ollama',NULL,
                    '{"runtime_id":"legacy-ollama","artifact_ids":["legacy-artifact"],"components":[{"artifact_id":"legacy-reranker"}]}',
                    '{}','{}','PUBLISHED',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )
        connection.execute(
            """INSERT INTO execution_profiles
            (id,code,title,created_at,updated_at,created_by,revision,schema_version)
            VALUES ('unknown-profile','unknown','未知能力',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id,execution_profile_id,version_no,capability,runtime_version_id,workflow_version_id,model_bundle_json,
             input_contract_json,parameter_schema_json,status,created_at,updated_at,created_by,revision,schema_version)
            VALUES ('unknown-profile-v1','unknown-profile',1,'MODEL_MAGIC',NULL,NULL,'broken-json',
                    '{}','{}','DRAFT',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )

    with database.connect() as connection:
        before = connection.execute("SELECT COUNT(*) FROM mp_model_artifacts").fetchone()[0]

    inventory = LegacyModelPlatformInventoryReader().read(database)

    with database.connect() as connection:
        after = connection.execute("SELECT COUNT(*) FROM mp_model_artifacts").fetchone()[0]

    assert after == before
    assert inventory.counts["local_runtimes"] >= 1
    assert inventory.artifacts[0].code == "nomic-embed"
    profile = next(item for item in inventory.profile_versions if item.profile_version_id == "legacy-profile-v1")
    assert profile.capability == "EMBEDDING_TEXT"
    assert profile.runtime_reference == "legacy-ollama"
    assert profile.artifact_ids == ("legacy-artifact", "legacy-reranker")
    assert {(warning.code, warning.field) for warning in inventory.warnings} >= {
        ("LEGACY_JSON_INVALID", "compatibility_json"),
        ("LEGACY_JSON_INVALID", "model_bundle_json"),
        ("LEGACY_PROFILE_CAPABILITY_UNKNOWN", "capability"),
    }
