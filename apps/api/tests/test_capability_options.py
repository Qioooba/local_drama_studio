from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.capability_options import CapabilityOptionService
from local_drama.application.profiles import ProfileService
from local_drama.application.queries.generation_preferences import GenerationPreferenceQueryService
from local_drama.infrastructure.database.generation_preference_repository import SqliteGenerationPreferenceRepository
from local_drama.main import create_app


def _capability_service(database, configured) -> CapabilityOptionService:
    def resolve_preference(**kwargs):
        with database.connect() as connection:
            return GenerationPreferenceQueryService(
                SqliteGenerationPreferenceRepository(connection)
            ).resolve(**kwargs)

    return CapabilityOptionService(
        ProfileService(database, configured.manifest_path),
        resolve_preference,
        configured_llm_provider=configured.llm_provider,
        configured_llm_base_url=configured.llm_base_url,
        configured_llm_model=configured.llm_model,
    )


def _project_and_llm_profile(database, *, model: str, status: str = "PUBLISHED") -> tuple[str, str]:
    project_id = "project-capability-options"
    profile_id = f"profile-{model.replace(':', '-')}"
    version_id = f"version-{model.replace(':', '-')}"
    with database.transaction() as connection:
        connection.execute(
            """INSERT OR IGNORE INTO projects
            (id,code,title,status,template_version,root_rel,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?, 'capability-options', 'Capability Options', 'ACTIVE', 'v2',
                    'projects/capability-options', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'test', 1, 'v2')""",
            (project_id,),
        )
        connection.execute(
            """INSERT OR IGNORE INTO local_runtimes
            (id,code,title,transport,base_url,executable_ref,runtime_version,status,details_json,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES ('runtime-capability-options','runtime-capability-options','Ollama Runtime','LOOPBACK_HTTP',
                    'http://127.0.0.1:11434','ollama',NULL,'AVAILABLE','{}',
                    CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')"""
        )
        connection.execute(
            """INSERT INTO execution_profiles
            (id,code,title,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'test', 1, 'v2')""",
            (profile_id, profile_id, f"{model} 故事拆解"),
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id,execution_profile_id,version_no,capability,model_bundle_json,input_contract_json,
             parameter_schema_json,status,manifest_sha256,capability_json,worker_policy,runtime_version_id,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,1,'LLM_STORY_PARSE',?,'{}','{}',?,NULL,'{}','ONE_LOCAL_LLM_TASK',
                    'runtime-capability-options',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'test',1,'v2')""",
            (version_id, profile_id, f'{{"runtime_id":"runtime-capability-options","model":"{model}","provider":"OLLAMA_LOOPBACK"}}', status),
        )
    return project_id, version_id


def test_capability_options_explain_configured_but_unpublished_runtime(workspace, database) -> None:
    project_id, published_id = _project_and_llm_profile(database, model="deepseek-r1:14b")
    configured = workspace.model_copy(update={"llm_model": "qwen3.8:27b", "llm_provider": "OLLAMA_LOOPBACK"})

    result = _capability_service(database, configured).list_options(
        capability="LLM_STORY_PARSE", project_id=project_id
    )

    assert result["selection"]["ready"] is True
    assert result["selection"]["profile_version_id"] == published_id
    assert result["options"][0]["model"]["name"] == "deepseek-r1:14b"
    assert result["options"][0]["selectable"] is True
    assert result["configured_runtime"] == {
        "provider": "OLLAMA_LOOPBACK",
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen3.8:27b",
        "publication_status": "NOT_PUBLISHED",
        "matching_profile_version_id": None,
        "message": "当前默认运行模型已配置，但尚未发布为这项能力",
    }
    assert result["runtime_contacted"] is False
    assert result["network_contacted"] is False


def test_capability_options_route_returns_same_server_owned_resolution(workspace, database) -> None:
    project_id, published_id = _project_and_llm_profile(database, model="qwen3.8:27b")
    configured = workspace.model_copy(update={"llm_model": "qwen3.8:27b", "llm_provider": "OLLAMA_LOOPBACK"})

    with TestClient(create_app(configured)) as client:
        response = client.get(
            "/api/v1/capability-options",
            params={"capability": "LLM_STORY_PARSE", "project_id": project_id},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selection"]["profile_version_id"] == published_id
    assert payload["selection"]["ready"] is True
    assert payload["configured_runtime"]["publication_status"] == "PUBLISHED"


def test_capability_options_keep_unpublished_profile_visible_but_unselectable(workspace, database) -> None:
    project_id, candidate_id = _project_and_llm_profile(database, model="qwen3.8:27b", status="CANDIDATE_UNVERIFIED")
    configured = workspace.model_copy(update={"llm_model": "qwen3.8:27b", "llm_provider": "OLLAMA_LOOPBACK"})

    result = _capability_service(database, configured).list_options(
        capability="LLM_STORY_PARSE", project_id=project_id
    )

    option = next(item for item in result["options"] if item["profile_version_id"] == candidate_id)
    assert option["selectable"] is False
    assert option["availability"] == "BLOCKED"
    assert option["blockers"][0]["code"] == "PROFILE_NOT_PUBLISHED"
    assert result["configured_runtime"]["publication_status"] == "CANDIDATE"
    assert result["selection"]["ready"] is False
