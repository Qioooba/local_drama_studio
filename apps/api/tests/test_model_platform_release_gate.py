from __future__ import annotations

import json
import sqlite3

from scripts.migrate import migrate
from scripts.model_platform_release_gate import expected_heads, verify


def test_model_platform_release_gate_verifies_configured_windows_contract(tmp_path) -> None:
    instance = tmp_path / "instance"
    release = tmp_path / "release"
    instance.mkdir()
    release.mkdir()
    database = instance / "data" / "local_drama.sqlite3"
    database.parent.mkdir()
    migrate(database)
    config = instance / "config.json"
    config.write_text(json.dumps({"schema_version": 2, "runtime": {"model_root": "${INSTANCE_ROOT}/models", "model_library_roots": ["${MODEL_ROOT}/libraries/comfyui", "${MODEL_ROOT}/libraries/pytorch", "${MODEL_ROOT}/libraries/ollama", "${MODEL_ROOT}/libraries/audio"]}}), encoding="utf-8")
    for name in ("downloads", "staging", "quarantine", "libraries/comfyui", "libraries/pytorch", "libraries/ollama", "libraries/audio"):
        (instance / "models" / name).mkdir(parents=True, exist_ok=True)
    result = verify(config_path=config, release_root=release, instance_root=instance, database_path=database)
    assert result["status"] == "PASS"
    assert result["runtime_contacted"] is False
    assert {item["code"] for item in result["checks"]} >= {"MIGRATION_HEAD", "PROJECT_KNOWLEDGE_V2_SCHEMA"}


def test_model_platform_release_gate_rejects_operational_directory_as_library(tmp_path) -> None:
    instance = tmp_path / "instance"
    release = tmp_path / "release"
    instance.mkdir()
    release.mkdir()
    database = instance / "data" / "local_drama.sqlite3"
    database.parent.mkdir()
    migrate(database)
    model_root = instance / "models"
    for name in ("downloads", "staging", "quarantine", "libraries/comfyui", "libraries/pytorch", "libraries/ollama", "libraries/audio"):
        (model_root / name).mkdir(parents=True, exist_ok=True)
    config = instance / "config.json"
    config.write_text(json.dumps({"schema_version": 2, "runtime": {"model_root": "${INSTANCE_ROOT}/models", "model_library_roots": ["${MODEL_ROOT}/downloads", "${MODEL_ROOT}/libraries/pytorch", "${MODEL_ROOT}/libraries/ollama", "${MODEL_ROOT}/libraries/audio"]}}), encoding="utf-8")
    result = verify(config_path=config, release_root=release, instance_root=instance, database_path=database)
    assert result["status"] == "FAIL"
    assert next(item for item in result["checks"] if item["code"] == "MODEL_LIBRARIES")["status"] == "FAIL"


def test_model_platform_release_gate_rejects_published_profile_without_worker_handler(tmp_path) -> None:
    instance = tmp_path / "instance"
    release = tmp_path / "release"
    instance.mkdir()
    release.mkdir()
    database = instance / "data" / "local_drama.sqlite3"
    database.parent.mkdir()
    migrate(database)
    model_root = instance / "models"
    for name in ("downloads", "staging", "quarantine", "libraries/comfyui", "libraries/pytorch", "libraries/ollama", "libraries/audio"):
        (model_root / name).mkdir(parents=True, exist_ok=True)
    config = instance / "config.json"
    config.write_text(json.dumps({"schema_version": 2, "runtime": {"model_root": "${INSTANCE_ROOT}/models", "model_library_roots": ["${MODEL_ROOT}/libraries/comfyui", "${MODEL_ROOT}/libraries/pytorch", "${MODEL_ROOT}/libraries/ollama", "${MODEL_ROOT}/libraries/audio"]}}), encoding="utf-8")
    with sqlite3.connect(database) as connection:
        capability_id = connection.execute("SELECT id FROM mp_capability_definitions WHERE code='EMBEDDING_TEXT'").fetchone()[0]
        connection.execute(
            """INSERT INTO mp_adapter_binding_contract_versions
            (id,runtime_kind,adapter_code,version_no,binding_json,content_hash,created_at,updated_at)
            VALUES ('unsupported-binding','PYTORCH_PROCESS','unsupported.adapter',1,'{}','hash','2026-08-29','2026-08-29')"""
        )
        connection.execute(
            """INSERT INTO mp_execution_profiles (id,code,title,created_at,updated_at)
            VALUES ('unsupported-profile','unsupported-profile','Unsupported profile','2026-08-29','2026-08-29')"""
        )
        connection.execute(
            """INSERT INTO mp_execution_profile_versions
            (id,profile_id,version_no,capability_definition_id,runtime_installation_version_id,parameter_contract_version_id,
             adapter_binding_contract_version_id,resource_policy_version_id,workflow_version_id,payload_json,payload_hash,created_at,updated_at)
            VALUES ('unsupported-profile-v1','unsupported-profile',1,?,'runtime-v1','parameter-v1','unsupported-binding',
                    'resource-v1',NULL,'{}','hash','2026-08-29','2026-08-29')""",
            (capability_id,),
        )
        connection.execute(
            """INSERT INTO mp_profile_publications
            (id,execution_profile_version_id,status,validation_run_id,published_at,retired_at,reason,created_at,updated_at)
            VALUES ('unsupported-publication','unsupported-profile-v1','PUBLISHED',NULL,'2026-08-29',NULL,'test','2026-08-29','2026-08-29')"""
        )
    result = verify(config_path=config, release_root=release, instance_root=instance, database_path=database)
    assert result["status"] == "FAIL"
    assert next(item for item in result["checks"] if item["code"] == "PUBLISHED_PROFILE_HANDLER_COVERAGE") == {
        "code": "PUBLISHED_PROFILE_HANDLER_COVERAGE",
        "status": "FAIL",
        "detail": "1 published Profile(s) lack a declared production handler",
    }


def test_model_platform_release_gate_reads_the_contract_from_packaged_payload(monkeypatch, tmp_path) -> None:
    payload = tmp_path / "payload"
    (payload / "app").mkdir(parents=True)
    (payload / "migration-contract.json").write_text(
        json.dumps({"expected_heads": ["release-head"]}), encoding="utf-8"
    )
    monkeypatch.setattr("scripts.model_platform_release_gate.ROOT", payload)
    assert expected_heads() == ["release-head"]
