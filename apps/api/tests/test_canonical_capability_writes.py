"""Fail-closed canonical capability coverage for mutable write paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_drama.application.configuration import ConfigurationService
from local_drama.application.documents import DocumentImportService
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError


def _project(workspace, database, code: str, *, profile_bindings=None):
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
        profile_bindings=profile_bindings or [],
    )


def _published_i2v(workspace, database) -> dict[str, object]:
    profiles = ProfileService(database, workspace.manifest_path)
    profile = next(
        item for item in profiles.sync_manifest()["profiles"] if item["capability"] == "VIDEO_I2V"
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET status='PUBLISHED' WHERE id=?",
            (profile["version_id"],),
        )
    return profile


def test_manifest_profile_writes_are_canonical_and_ambiguous_manifest_fails_closed(
    workspace, database, tmp_path: Path
) -> None:
    synced = ProfileService(database, workspace.manifest_path).sync_manifest()
    capabilities = {str(item["capability"]) for item in synced["profiles"]}
    assert {"VIDEO_T2V", "VIDEO_I2V", "VIDEO_REFERENCE"} <= capabilities
    with database.connect() as connection:
        persisted = {
            str(row[0])
            for row in connection.execute("SELECT capability FROM execution_profile_versions").fetchall()
        }
    assert persisted == capabilities
    assert "T2V" not in persisted
    assert "I2V" not in persisted

    bad_manifest = json.loads(workspace.manifest_path.read_text(encoding="utf-8"))
    bad_manifest["h3_capabilities"]["IMAGE_GENERATION"] = {
        "status": "candidate",
        "required_nodes": [],
    }
    bad_path = tmp_path / "ambiguous-model-manifest.json"
    bad_path.write_text(json.dumps(bad_manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DomainRuleError) as caught:
        ProfileService(database, bad_path).sync_manifest()
    assert caught.value.code == "PROFILE_CAPABILITY_INVALID"


def test_configuration_and_project_creation_persist_only_canonical_binding_keys(workspace, database) -> None:
    profile = _published_i2v(workspace, database)
    profile_version_id = str(profile["version_id"])

    project = _project(workspace, database, "canonical_configuration")
    bound = ConfigurationService(database).bind_profile(
        str(project["id"]), " i2v ", profile_version_id
    )
    assert bound["capability"] == "VIDEO_I2V"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT capability FROM project_profile_bindings WHERE project_id=?", (project["id"],)
        ).fetchone()[0] == "VIDEO_I2V"

    with pytest.raises(DomainRuleError) as ambiguous:
        ConfigurationService(database).bind_profile(
            str(project["id"]), "IMAGE_GENERATION", profile_version_id
        )
    assert ambiguous.value.code == "PROFILE_CAPABILITY_INVALID"
    with pytest.raises(DomainRuleError) as mismatch:
        ConfigurationService(database).bind_profile(
            str(project["id"]), "T2V", profile_version_id
        )
    assert mismatch.value.code == "PROFILE_CAPABILITY_MISMATCH"

    created = _project(
        workspace,
        database,
        "canonical_project_create",
        profile_bindings=[{"capability": "image_to_video", "profile_version_id": profile_version_id}],
    )
    with database.connect() as connection:
        assert connection.execute(
            "SELECT capability FROM project_profile_bindings WHERE project_id=?", (created["id"],)
        ).fetchone()[0] == "VIDEO_I2V"


def test_local_llm_uses_exact_story_parse_capability_and_repairs_legacy_resync(
    workspace, database, monkeypatch
) -> None:
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.probe",
        lambda self, load_test=False: {"status": "PASS", "model": "qwen-test", "runtime": "ollama"},
    )
    configured = workspace.model_copy(update={"llm_model": "qwen-test"})
    service = LocalLLMService(database, configured)
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO local_runtimes
            (id,code,title,transport,base_url,executable_ref,runtime_version,status,details_json,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES ('legacy-random-runtime-id','ollama-loopback','legacy runtime','LOOPBACK_HTTP',
                    'http://127.0.0.1:11434','ollama',NULL,'CANDIDATE_UNVERIFIED','{}',
                    CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )
        connection.execute(
            """INSERT INTO execution_profiles
            (id,code,title,created_at,updated_at,created_by,revision,schema_version)
            VALUES ('legacy-random-profile-id','local-llm-ollama-qwen-test','legacy',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,
             parameter_schema_json,status,manifest_sha256,capability_json,worker_policy,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES ('legacy-random-version-id','legacy-random-profile-id',1,'SCRIPT_BREAKDOWN_LLM','{}','{}','{}',
                    'CANDIDATE_UNVERIFIED',NULL,'{}','ONE_LOCAL_LLM_TASK',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )
    candidate = service.sync_candidate()
    profile_version_id = str(candidate["profile_version_id"])
    assert profile_version_id == "legacy-random-version-id"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT capability FROM execution_profile_versions WHERE id=?", (profile_version_id,)
        ).fetchone()[0] == "LLM_STORY_PARSE"
        model_bundle = json.loads(connection.execute(
            "SELECT model_bundle_json FROM execution_profile_versions WHERE id=?", (profile_version_id,)
        ).fetchone()[0])
        assert model_bundle["runtime_id"] == "legacy-random-runtime-id"

    # A stale post-0049 writer is repaired by the canonical ON CONFLICT path.
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET capability='SCRIPT_BREAKDOWN_LLM' WHERE id=?",
            (profile_version_id,),
        )
    service.sync_candidate()
    with database.connect() as connection:
        assert connection.execute(
            "SELECT capability FROM execution_profile_versions WHERE id=?", (profile_version_id,)
        ).fetchone()[0] == "LLM_STORY_PARSE"

    project = _project(workspace, database, "canonical_llm_exact")
    source = workspace.work_root / "canonical-llm.md"
    source.write_text("# 第一场\n\n人物进入房间。", encoding="utf-8")
    documents = DocumentImportService(database, configured)
    imported = documents.import_document(str(project["id"]), source)
    documents.commit(str(imported["import_session_id"]), str(imported["preview_hash"]))

    # LLM_STORYBOARD contains the old fuzzy token but is not authorized for
    # script breakdown.  The exact SQL gate must reject it before model use.
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET capability='LLM_STORYBOARD',status='PUBLISHED' WHERE id=?",
            (profile_version_id,),
        )
    with pytest.raises(DomainRuleError) as unavailable:
        service.breakdown(str(imported["import_session_id"]), profile_version_id)
    assert unavailable.value.code == "LOCAL_LLM_PROFILE_UNAVAILABLE"
    with pytest.raises(DomainRuleError) as mismatch:
        service.publish(profile_version_id)
    assert mismatch.value.code == "PROFILE_CAPABILITY_MISMATCH"

    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET capability='IMAGE_GENERATION' WHERE id=?",
            (profile_version_id,),
        )
    with pytest.raises(DomainRuleError) as invalid:
        service.publish(profile_version_id)
    assert invalid.value.code == "PROFILE_CAPABILITY_INVALID"
