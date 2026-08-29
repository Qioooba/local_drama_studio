from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.main import create_app
from local_drama.model_platform.application.capability_smoke import CapabilitySmokeResult
from local_drama.model_platform.application.comfy_capability_smoke_jobs import SubmittedComfyCapabilitySmoke
from local_drama.model_platform.application.comfy_workflow_bindings import ComfyWorkflowBinding
from local_drama.model_platform.application.discovery_registration import DiscoveryRegistrationService
from local_drama.model_platform.application.installation_integrity import InstallationIntegrityResult
from local_drama.model_platform.application.ollama_discovery import OllamaDiscoveryOrchestrator
from local_drama.model_platform.application.ollama_text_profiles import OllamaProfileSmokeResult, ProvisionedOllamaProfile
from local_drama.model_platform.application.profile_catalog import ProfileLifecycleItem


class _CandidateCatalog:
    def tags(self):
        return [{"name": "nomic-embed-text", "digest": "digest-1", "size": 2}]

    def show(self, model: str):
        assert model == "nomic-embed-text"
        return {"capabilities": ["embedding"], "details": {"family": "nomic"}}


def test_model_platform_overview_separates_discovery_from_executable_state(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v2/model-platform/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["read_only"] is True
    assert payload["overview"] == {
        "capability_count": 35,
        "registered_model_release_count": 0,
        "runtime_installation_count": 0,
        "discovery_observation_count": 0,
        "published_profile_count": 0,
    }


def test_quick_create_v2_readiness_is_read_only_and_never_switches_execution(workspace, database, monkeypatch) -> None:
    class _Readiness:
        def __init__(self, *_args):
            pass

        def list(self):
            class _Item:
                mode = "TEXT_TO_IMAGE"
                capability_code = "IMAGE_CONCEPT"
                execution_profile_version_id = "v2-profile"
                ready = True
                blocker = None

            return (_Item(),)

    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.QuickCreateV2ReadinessService", _Readiness)
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v2/model-platform/quick-create-v2-readiness")

    assert response.status_code == 200
    assert response.json() == {
        "items": [{"mode": "TEXT_TO_IMAGE", "capability_code": "IMAGE_CONCEPT", "execution_profile_version_id": "v2-profile", "ready": True, "blocker": None}],
        "read_only": True,
        "execution_switched": False,
    }


def test_quick_create_v2_direct_image_is_explicitly_v2_only(workspace, database, monkeypatch) -> None:
    class _DirectImage:
        def __init__(self, *_args):
            pass

        def preview(self, **_kwargs):
            return type("Preview", (), {
                "capability_code": "IMAGE_CONCEPT", "execution_profile_version_id": "v2-image", "resolution_hash": "a" * 64,
                "executable": True, "blockers": (),
            })()

        def submit(self, **_kwargs):
            return type("Submitted", (), {
                "job_id": "v2-job", "execution_snapshot_id": "v2-snapshot", "execution_snapshot_hash": "b" * 64,
                "handler_code": "comfy.workflow.v2", "handler_version": "v1", "idempotent_replay": False,
            })()

        def status(self, _job_id):
            return type("Status", (), {
                "job_id": "v2-job", "state": "SUCCEEDED", "progress": {"percent": 100},
                "error_code": None, "error_detail_redacted": None, "execution_snapshot_id": "v2-snapshot",
                "execution_snapshot_hash": "b" * 64,
                "artifacts": ({"artifact_id": "artifact-v2", "kind": "COMFY_OUTPUT", "download_url": "/api/v1/artifacts/artifact-v2/download"},),
            })()

    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.QuickCreateV2DirectImageService", _DirectImage)
    with TestClient(create_app(workspace)) as client:
        preview = client.post("/api/v2/model-platform/quick-create-v2/direct-image:preview", json={"prompt": "雨夜的橘猫"})
        submitted = client.post(
            "/api/v2/model-platform/quick-create-v2/direct-image:submit",
            headers={"Idempotency-Key": "quick-v2-api"},
            json={"prompt": "雨夜的橘猫", "expected_resolution_hash": "a" * 64},
        )
        status = client.get("/api/v2/model-platform/quick-create-v2/direct-image/jobs/v2-job")

    assert preview.status_code == 200
    assert preview.json()["execution_switched"] is True
    assert preview.json()["legacy_quick_generation_touched"] is False
    assert submitted.status_code == 201
    assert submitted.json()["execution"] == {
        "capability_code": "IMAGE_CONCEPT", "job_id": "v2-job", "execution_snapshot_id": "v2-snapshot",
        "execution_snapshot_hash": "b" * 64, "handler_code": "comfy.workflow.v2", "handler_version": "v1", "idempotent_replay": False,
    }
    assert submitted.json()["execution_switched"] is True
    assert submitted.json()["legacy_quick_generation_touched"] is False
    assert status.status_code == 200
    assert status.json() == {
        "execution": {
            "capability_code": "IMAGE_CONCEPT", "job_id": "v2-job", "state": "SUCCEEDED", "progress": {"percent": 100},
            "error_code": None, "error_detail_redacted": None, "execution_snapshot_id": "v2-snapshot",
            "execution_snapshot_hash": "b" * 64,
            "artifacts": [{"artifact_id": "artifact-v2", "kind": "COMFY_OUTPUT", "download_url": "/api/v1/artifacts/artifact-v2/download"}],
        },
        "read_only": True,
        "execution_switched": True,
        "legacy_quick_generation_touched": False,
    }


def test_quick_create_v2_i2v_api_uses_only_candidate_selection_and_v2_aggregate(workspace, database, monkeypatch) -> None:
    class _Candidates:
        def __init__(self, *_args):
            pass

        def preview(self, **_kwargs):
            return type("Plan", (), {
                "execution_profile_version_id": "v2-image", "executable": True, "blockers": (),
                "candidates": (type("Candidate", (), {"ordinal": 1, "seed": 42, "resolution_hash": "a" * 64})(),),
            })()

        def submit(self, **_kwargs):
            return type("Submitted", (), {
                "run": type("Run", (), {"id": "run-v2", "mode": "TEXT_TO_IMAGE_TO_VIDEO", "state": "PLANNED", "idempotent_replay": False})(),
                "job_ids": ("image-job-v2",), "execution_snapshot_ids": ("image-snapshot-v2",),
            })()

    class _Runs:
        def __init__(self, *_args):
            pass

        def select_image_candidate(self, run_id, step_id):
            assert (run_id, step_id) == ("run-v2", "image-step-v2")

        def public_run(self, run_id):
            assert run_id == "run-v2"
            return {"id": "run-v2", "state": "IMAGE_SELECTED", "steps": []}

    class _I2V:
        def __init__(self, *_args):
            pass

        def preview(self, *, run_id):
            assert run_id == "run-v2"
            return type("Preview", (), {
                "run_id": run_id, "selected_image_artifact_id": "image-artifact-v2", "execution_profile_version_id": "v2-i2v",
                "resolution_hash": "b" * 64, "executable": True, "blockers": (),
            })()

        def submit(self, **kwargs):
            assert kwargs["run_id"] == "run-v2"
            return type("Submitted", (), {
                "run_id": "run-v2", "job_id": "video-job-v2", "execution_snapshot_id": "video-snapshot-v2",
                "execution_snapshot_hash": "c" * 64, "handler_code": "comfy.workflow.v1", "handler_version": "v1", "idempotent_replay": False,
            })()

    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.QuickCreateV2ImageCandidateService", _Candidates)
    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.QuickCreateV2RunService", _Runs)
    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.QuickCreateV2ImageToVideoService", _I2V)
    with TestClient(create_app(workspace)) as client:
        candidate_preview = client.post("/api/v2/model-platform/quick-create-v2/image-candidates:preview", json={"prompt": "雨夜的橘猫", "candidate_count": 1})
        candidate_submit = client.post(
            "/api/v2/model-platform/quick-create-v2/image-candidates:submit", headers={"Idempotency-Key": "candidate-v2-api"},
            json={"prompt": "雨夜的橘猫", "candidates": [{"ordinal": 1, "seed": 42, "resolution_hash": "a" * 64}]},
        )
        selected = client.post("/api/v2/model-platform/quick-create-v2/runs/run-v2/candidates/image-step-v2:select")
        video_preview = client.post("/api/v2/model-platform/quick-create-v2/runs/run-v2/image-to-video:preview")
        video_submit = client.post(
            "/api/v2/model-platform/quick-create-v2/runs/run-v2/image-to-video:submit", headers={"Idempotency-Key": "video-v2-api"},
            json={"expected_resolution_hash": "b" * 64},
        )
        aggregate = client.get("/api/v2/model-platform/quick-create-v2/runs/run-v2")

    assert candidate_preview.status_code == 200
    assert candidate_submit.status_code == 201
    assert selected.status_code == 200
    assert video_preview.status_code == 200
    assert video_submit.status_code == 201
    assert aggregate.status_code == 200
    for response in (candidate_preview, candidate_submit, selected, video_preview, video_submit, aggregate):
        assert response.json()["execution_switched"] is True
        assert response.json()["legacy_quick_generation_touched"] is False
    assert video_submit.json()["execution"]["capability_code"] == "VIDEO_I2V"


def test_model_platform_storage_policy_is_safe_machine_configuration_summary(workspace, database) -> None:
    settings = workspace.model_copy(update={
        "model_root": workspace.instance_root / "models",
        "model_library_roots": (
            workspace.instance_root / "models" / "libraries" / "pytorch",
            workspace.instance_root / "models" / "libraries" / "custom-private-library",
        ),
    })
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v2/model-platform/storage-policy")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "storage_policy": {
            "model_root_configured": True,
            "root_kind": "INSTANCE_DEFAULT",
            "discovery_library_count": 2,
            "discovery_libraries": ["PyTorch 模型库", "受管模型库 2"],
            "operational_areas": ["下载队列", "暂存区", "隔离区"],
            "configuration_authority": "HOST_CLI",
            "absolute_paths_exposed": False,
            "trusted_download_source_count": 0,
            "online_download_default_enabled": False,
        },
        "read_only": True,
    }
    assert str(workspace.instance_root) not in str(payload)
    assert "custom-private-library" not in str(payload)


def test_model_platform_capability_catalog_is_surface_filtered_and_read_only(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v2/model-platform/capabilities", params={"business_surface": "project-knowledge"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["read_only"] is True
    assert {item["code"] for item in payload["items"]} >= {"EMBEDDING_TEXT", "RERANK"}
    assert all("project-knowledge" in item["business_surfaces"] for item in payload["items"])


def test_model_platform_business_rollout_api_records_an_eligibility_gate_without_switching_execution(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        saved = client.put("/api/v2/model-platform/business-selection-rollouts", json={
            "business_surface": "quick-create",
            "capability_code": "IMAGE_CONCEPT",
            "scope_type": "PROJECT",
            "state": "CUTOVER_APPROVED",
            "approval_reason": "已完成预发布对账",
            "approved_by": "release-operator",
        })
        listed = client.get(
            "/api/v2/model-platform/business-selection-rollouts",
            params={"business_surface": "quick-create"},
        )

    assert saved.status_code == 200
    assert saved.json()["rollout"]["state"] == "CUTOVER_APPROVED"
    assert saved.json()["rollout"]["execution_switched"] is False
    assert listed.status_code == 200
    assert listed.json() == {
        "items": [saved.json()["rollout"]],
        "count": 1,
        "read_only": True,
        "execution_switched": False,
    }


def test_model_platform_system_assignment_catalog_is_read_only_and_never_returns_runtime_wiring(workspace, database, monkeypatch) -> None:
    class _Catalog:
        def __init__(self, *_args):
            pass

        def list_system(self):
            return [{
                "capability_code": "EMBEDDING_TEXT",
                "title": "文本向量化",
                "family": "RETRIEVAL",
                "background_only": True,
                "assignment": {
                    "resolution_mode": "AUTO",
                    "execution_profile_version_id": None,
                    "revision": None,
                    "overrides": {},
                    "has_unrenderable_override": False,
                },
                "profiles": [],
            }]

    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.CapabilityAssignmentCatalogService", _Catalog)
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v2/model-platform/system-capability-assignments")

    assert response.status_code == 200
    assert response.json() == {
        "items": [_Catalog().list_system()[0]],
        "count": 1,
        "read_only": True,
        "scope_type": "SYSTEM",
    }


def test_model_platform_scope_assignment_catalog_passes_only_verified_scope_to_control_plane(workspace, database, monkeypatch) -> None:
    class _Catalog:
        def __init__(self, *_args):
            pass

        def list_scope(self, scope_type: str, scope_id: str):
            assert (scope_type, scope_id) == ("PROJECT", "project-1")
            return [{"capability_code": "EMBEDDING_TEXT", "profiles": []}]

    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.CapabilityAssignmentCatalogService", _Catalog)
    with TestClient(create_app(workspace)) as client:
        response = client.get(
            "/api/v2/model-platform/capability-assignment-catalog",
            params={"scope_type": "project", "scope_id": "project-1"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "items": [{"capability_code": "EMBEDDING_TEXT", "profiles": []}],
        "count": 1,
        "read_only": True,
        "scope_type": "PROJECT",
        "scope_id": "project-1",
    }


def test_model_platform_facade_evaluation_is_read_only_and_cannot_switch_execution(workspace, database, monkeypatch) -> None:
    class _Facade:
        def __init__(self, *_args):
            pass

        def evaluate(self, **kwargs):
            assert kwargs["business_surface"] == "project-knowledge"
            assert kwargs["capability_code"] == "EMBEDDING_TEXT"
            assert kwargs["scope"].project_id == "project-1"

            class _Evaluation:
                def as_dict(self):
                    return {
                        "decision": "CUTOVER_CANDIDATE",
                        "execution_owner": "LEGACY_V1",
                        "execution_switched": False,
                    }

            return _Evaluation()

    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.GenerationCapabilityConfigurationFacade", _Facade)
    with TestClient(create_app(workspace)) as client:
        response = client.get(
            "/api/v2/model-platform/business-selection-facade-evaluation",
            params={
                "business_surface": "project-knowledge",
                "capability_code": "EMBEDDING_TEXT",
                "project_id": "project-1",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "evaluation": {"decision": "CUTOVER_CANDIDATE", "execution_owner": "LEGACY_V1", "execution_switched": False},
        "read_only": True,
        "execution_switched": False,
    }


def test_project_knowledge_index_api_only_accepts_server_owned_source_and_returns_safe_state(workspace, database, monkeypatch) -> None:
    class _Preparation:
        def __init__(self, *_args):
            pass

        def prepare(self, **kwargs):
            assert kwargs == {
                "project_id": "project-1",
                "source_document_version_id": "source-version-1",
                "actor": "release-operator",
            }

            class _Result:
                index_run_id = "knowledge-run-1"
                execution_profile_version_id = "profile-version-1"
                chunk_count = 34
                reused = False
                status = "PREPARED"
                attempt_no = 1

            return _Result()

    class _Queue:
        def __init__(self, *_args):
            pass

        def queue(self, index_run_id):
            assert index_run_id == "knowledge-run-1"

            class _Result:
                index_run_id = "knowledge-run-1"
                queued_batch_count = 2
                job_ids = ("job-1", "job-2")

            return _Result()

    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.ProjectKnowledgeIndexPreparationService", _Preparation)
    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.ProjectKnowledgeIndexQueueService", _Queue)
    with TestClient(create_app(workspace)) as client:
        rejected = client.post("/api/v2/model-platform/project-knowledge-indexes", json={
            "project_id": "project-1",
            "source_document_version_id": "source-version-1",
            "path": "F:/must-not-be-accepted.txt",
        })
        prepared = client.post("/api/v2/model-platform/project-knowledge-indexes", json={
            "project_id": "project-1",
            "source_document_version_id": "source-version-1",
            "actor": "release-operator",
        })
        queued = client.post("/api/v2/model-platform/project-knowledge-indexes/knowledge-run-1:queue")

    assert rejected.status_code == 422
    assert prepared.status_code == 201
    assert prepared.json() == {
        "index": {
            "index_run_id": "knowledge-run-1",
            "execution_profile_version_id": "profile-version-1",
            "chunk_count": 34,
            "reused": False,
            "status": "PREPARED",
            "attempt_no": 1,
        }
    }
    assert queued.status_code == 200
    assert queued.json()["index"] == {
        "index_run_id": "knowledge-run-1",
        "queued_batch_count": 2,
        "job_ids": ["job-1", "job-2"],
        "status": "QUEUED",
    }


def test_project_knowledge_search_api_accepts_text_only_and_returns_v2_provenance(workspace, database, monkeypatch) -> None:
    class _Search:
        def __init__(self, *_args):
            pass

        def search(self, *, project_id, query, limit):
            assert (project_id, query, limit) == ("project-1", "谁进入了房间？", 3)

            class _Hit:
                index_run_id = "run-2"
                source_document_version_id = "source-v1"
                ordinal = 4
                source_start = 18
                source_end = 46
                score = 0.91

            return "profile-v2", (_Hit(),)

    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.ProjectKnowledgeRetrievalService", _Search)
    with TestClient(create_app(workspace)) as client:
        rejected = client.post("/api/v2/model-platform/project-knowledge-search", json={
            "project_id": "project-1", "query": "谁进入了房间？", "vector": [0.1],
        })
        response = client.post("/api/v2/model-platform/project-knowledge-search", json={
            "project_id": "project-1", "query": "谁进入了房间？", "limit": 3,
        })

    assert rejected.status_code == 422
    assert response.status_code == 200
    assert response.json() == {
        "search": {
            "execution_profile_version_id": "profile-v2",
            "items": [{
                "index_run_id": "run-2", "source_document_version_id": "source-v1", "ordinal": 4,
                "source_start": 18, "source_end": 46, "score": 0.91,
            }],
        }
    }


def test_model_platform_discovery_list_is_read_only_and_redacts_runtime_wiring(workspace, database) -> None:
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO mp_discovery_runs
            (id,library_id,runtime_installation_version_id,source,status,summary_json,started_at,finished_at,created_at,updated_at)
            VALUES ('scan-1',NULL,NULL,'OLLAMA','SUCCEEDED','{}','2026-08-29T01:00:00+00:00','2026-08-29T01:01:00+00:00','2026-08-29T01:01:00+00:00','2026-08-29T01:01:00+00:00')"""
        )
        connection.execute(
            """INSERT INTO mp_discovery_observations
            (id,discovery_run_id,native_id,kind,observed_json,content_hint,status,created_at,updated_at)
            VALUES ('scan-observation-1','scan-1','qwen3.8:27b','OLLAMA',?,NULL,'PRESENT','2026-08-29T01:01:00+00:00','2026-08-29T01:01:00+00:00')""",
            ('{"presence":"PRESENT","size_bytes":123,"metadata":{"family":"qwen3","base_url":"http://127.0.0.1:11434","absolute_path":"F:/private"},"candidate_capabilities":[{"capability":"LLM_STORY_PARSE"}]}',),
        )
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v2/model-platform/discovery-observations")

    assert response.status_code == 200
    payload = response.json()
    assert payload["read_only"] is True
    assert payload["items"] == [{
        "id": "scan-observation-1",
        "native_id": "qwen3.8:27b",
        "runtime_kind": "OLLAMA",
        "presence": "PRESENT",
        "size_bytes": 123,
        "candidate_capabilities": ["LLM_STORY_PARSE"],
        "metadata": {"family": "qwen3"},
        "observed_at": "2026-08-29T01:01:00+00:00",
        "discovery_run_status": "SUCCEEDED",
    }]
    assert "base_url" not in str(payload)
    assert "F:/private" not in str(payload)


def test_model_platform_registered_candidates_expose_readiness_not_runtime_wiring(workspace, database) -> None:
    run = OllamaDiscoveryOrchestrator(database, workspace).scan(_CandidateCatalog())
    with database.connect() as connection:
        observation = connection.execute(
            "SELECT id FROM mp_discovery_observations WHERE discovery_run_id=?", (run.id,)
        ).fetchone()
    DiscoveryRegistrationService(database).register(str(observation["id"]))

    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v2/model-platform/registered-candidates")

    assert response.status_code == 200
    payload = response.json()
    assert payload["read_only"] is True
    assert payload["count"] == 1
    candidate = payload["items"][0]
    assert candidate["runtime_kind"] == "OLLAMA"
    assert candidate["install_state"] == "DISCOVERED"
    assert candidate["integrity_status"] == "NOT_RUN"
    assert candidate["readiness_status"] == "VALIDATION_REQUIRED"
    assert candidate["capabilities"][0]["code"] == "EMBEDDING_TEXT"
    assert candidate["capabilities"][0]["workflow_binding_count"] == 0
    assert candidate["capabilities"][0]["workflow_schema_validated_count"] == 0
    assert candidate["capabilities"][0]["blockers"] == ["CAPABILITY_SMOKE_NOT_PASSED", "PROFILE_REQUIRED"]
    assert "base_url" not in str(payload)
    assert "native_locator" not in str(payload)


def test_model_platform_validation_history_is_installation_scoped_and_redacts_evidence_payload(workspace, database) -> None:
    run = OllamaDiscoveryOrchestrator(database, workspace).scan(_CandidateCatalog())
    with database.connect() as connection:
        observation = connection.execute(
            "SELECT id FROM mp_discovery_observations WHERE discovery_run_id=?", (run.id,)
        ).fetchone()
    registered = DiscoveryRegistrationService(database).register(str(observation["id"]))
    with database.transaction() as connection:
        offering = connection.execute(
            "SELECT id FROM mp_capability_offerings WHERE runtime_model_installation_id=?",
            (registered.runtime_model_installation_id,),
        ).fetchone()
        connection.execute(
            """INSERT INTO mp_validation_runs
            (id,target_kind,target_id,validation_kind,status,result_json,started_at,finished_at,created_at,updated_at)
            VALUES ('history-integrity','RUNTIME_MODEL_INSTALLATION',?,'INSTALLATION_INTEGRITY','INTEGRITY_PASSED',?,
                    '2026-08-29T01:00:00+00:00','2026-08-29T01:01:00+00:00','2026-08-29T01:00:00+00:00','2026-08-29T01:01:00+00:00')""",
            (registered.runtime_model_installation_id, '{"absolute_path":"F:/private/models"}'),
        )
        connection.execute(
            """INSERT INTO mp_validation_runs
            (id,target_kind,target_id,validation_kind,status,result_json,started_at,finished_at,created_at,updated_at)
            VALUES ('history-capability','CAPABILITY_OFFERING',?,'CAPABILITY_SMOKE','SMOKE_PASSED',?,
                    '2026-08-29T02:00:00+00:00','2026-08-29T02:01:00+00:00','2026-08-29T02:00:00+00:00','2026-08-29T02:01:00+00:00')""",
            (offering["id"], '{"endpoint":"http://127.0.0.1:11434","model":"private"}'),
        )

    with TestClient(create_app(workspace)) as client:
        response = client.get(
            f"/api/v2/model-platform/registered-candidates/{registered.runtime_model_installation_id}/validation-history"
        )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "validation_run_id": "history-capability",
                "target": "CAPABILITY",
                "capability_code": "EMBEDDING_TEXT",
                "validation_kind": "CAPABILITY_SMOKE",
                "status": "SMOKE_PASSED",
                "occurred_at": "2026-08-29T02:01:00+00:00",
            },
            {
                "validation_run_id": "history-integrity",
                "target": "INSTALLATION",
                "capability_code": None,
                "validation_kind": "INSTALLATION_INTEGRITY",
                "status": "INTEGRITY_PASSED",
                "occurred_at": "2026-08-29T01:01:00+00:00",
            },
        ],
        "count": 2,
        "read_only": True,
        "evidence_payload_exposed": False,
    }
    assert "F:/private" not in response.text
    assert "127.0.0.1" not in response.text


def test_model_platform_capability_smoke_route_uses_implementation_bound_service(workspace, database, monkeypatch) -> None:
    class _SmokeService:
        def __init__(self, *_args):
            pass

        def smoke(self, installation_id: str, capability_code: str) -> CapabilitySmokeResult:
            assert installation_id == "runtime-model-1"
            assert capability_code == "LLM_STORY_PARSE"
            return CapabilitySmokeResult("validation-1", installation_id, capability_code, "SMOKE_PASSED", False, False)

    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.CapabilitySmokeService", _SmokeService)
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v2/model-platform/registered-candidates/runtime-model-1/capability-offerings/LLM_STORY_PARSE:smoke"
        )

    assert response.status_code == 200
    assert response.json() == {"validation": {
        "validation_run_id": "validation-1",
        "runtime_model_installation_id": "runtime-model-1",
        "capability_code": "LLM_STORY_PARSE",
        "status": "SMOKE_PASSED",
        "installation_ready": False,
        "runtime_active": False,
    }}


def test_model_platform_integrity_route_uses_service_identity_validator(workspace, database, monkeypatch) -> None:
    class _IntegrityService:
        def __init__(self, *_args):
            pass

        def verify(self, installation_id: str) -> InstallationIntegrityResult:
            assert installation_id == "runtime-model-1"
            return InstallationIntegrityResult("integrity-1", installation_id, "INTEGRITY_PASSED", "INTEGRITY_VERIFIED")

    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.InstallationIntegrityService", _IntegrityService)
    with TestClient(create_app(workspace)) as client:
        response = client.post("/api/v2/model-platform/registered-candidates/runtime-model-1:verify-integrity")

    assert response.status_code == 200
    assert response.json() == {"validation": {
        "validation_run_id": "integrity-1",
        "runtime_model_installation_id": "runtime-model-1",
        "status": "INTEGRITY_PASSED",
        "install_state": "INTEGRITY_VERIFIED",
    }}


def test_model_platform_comfy_workflow_binding_route_accepts_only_an_existing_workflow_reference(workspace, database, monkeypatch) -> None:
    class _BindingService:
        def __init__(self, *_args):
            pass

        def bind(self, installation_id: str, capability_code: str, workflow_version_id: str) -> ComfyWorkflowBinding:
            assert (installation_id, capability_code, workflow_version_id) == ("runtime-model-1", "IMAGE_CONCEPT", "workflow-1")
            return ComfyWorkflowBinding(
                "binding-1",
                installation_id,
                capability_code,
                workflow_version_id,
                "SCHEMA_VALIDATED",
                True,
            )

    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.ComfyWorkflowBindingService", _BindingService)
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v2/model-platform/registered-candidates/runtime-model-1/capability-offerings/IMAGE_CONCEPT:bind-workflow",
            json={"workflow_version_id": "workflow-1"},
        )

    assert response.status_code == 200
    assert response.json() == {"workflow_binding": {
        "id": "binding-1",
        "runtime_model_installation_id": "runtime-model-1",
        "capability_code": "IMAGE_CONCEPT",
        "workflow_version_id": "workflow-1",
        "status": "SCHEMA_VALIDATED",
        "created": True,
    }}


def test_model_platform_comfy_workflow_binding_list_recovers_safe_ids(workspace, database, monkeypatch) -> None:
    class _BindingService:
        def __init__(self, *_args):
            pass

        def list(self, installation_id: str, capability_code: str):
            assert (installation_id, capability_code) == ("runtime-model-1", "IMAGE_CONCEPT")
            return (type("Binding", (), {"id": "binding-1", "workflow_version_id": "workflow-1", "binding_status": "SCHEMA_VALIDATED", "capability_smoke_passed": True})(),)

    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.ComfyWorkflowBindingService", _BindingService)
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v2/model-platform/registered-candidates/runtime-model-1/capability-offerings/IMAGE_CONCEPT/workflow-bindings")

    assert response.status_code == 200
    assert response.json() == {"items": [{"id": "binding-1", "workflow_version_id": "workflow-1", "status": "SCHEMA_VALIDATED", "capability_smoke_passed": True}], "count": 1, "read_only": True}


def test_model_platform_comfy_smoke_route_queues_a_durable_job_with_an_idempotency_key(workspace, database, monkeypatch) -> None:
    class _SmokeService:
        def __init__(self, *_args):
            pass

        def submit(self, workflow_binding_id: str, idempotency_key: str, **kwargs) -> SubmittedComfyCapabilitySmoke:
            assert (workflow_binding_id, idempotency_key, kwargs) == ("binding-1", "comfy-smoke-request-1", {"runtime_model_installation_id": "runtime-model-1", "capability_code": "IMAGE_CONCEPT"})
            return SubmittedComfyCapabilitySmoke("job-1", "binding-1", "runtime-model-1", "IMAGE_CONCEPT", "workflow-1", "a" * 64, False)

    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.ComfyCapabilitySmokeSubmissionService", _SmokeService)
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v2/model-platform/registered-candidates/runtime-model-1/capability-offerings/IMAGE_CONCEPT:queue-comfy-smoke",
            json={"workflow_binding_id": "binding-1"},
            headers={"Idempotency-Key": "comfy-smoke-request-1"},
        )

    assert response.status_code == 200
    assert response.json() == {"smoke_job": {
        "job_id": "job-1",
        "workflow_binding_id": "binding-1",
        "runtime_model_installation_id": "runtime-model-1",
        "capability_code": "IMAGE_CONCEPT",
        "workflow_version_id": "workflow-1",
        "smoke_contract_hash": "a" * 64,
        "idempotent_replay": False,
    }}


def test_model_platform_profile_catalog_recovers_lifecycle_without_runtime_wiring(workspace, database, monkeypatch) -> None:
    class _CatalogService:
        def __init__(self, *_args):
            pass

        def list(self, **kwargs):
            assert kwargs == {"runtime_model_installation_id": "runtime-model-1", "capability_code": "LLM_STORY_PARSE", "limit": 20}
            return (
                ProfileLifecycleItem(
                    profile_version_id="profile-1",
                    profile_code="ollama-qwen-story-parse",
                    profile_title="Qwen · Story parse",
                    version_no=1,
                    capability_code="LLM_STORY_PARSE",
                    runtime_model_installation_ids=("runtime-model-1",),
                    lifecycle_status="PROFILE_SMOKE_PASSED",
                    latest_validation_run_id="profile-smoke-1",
                    latest_validation_status="SMOKE_PASSED",
                ),
            )

    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.ProfileCatalogService", _CatalogService)
    with TestClient(create_app(workspace)) as client:
        response = client.get(
            "/api/v2/model-platform/profile-versions",
            params={"runtime_model_installation_id": "runtime-model-1", "capability_code": "LLM_STORY_PARSE", "limit": 20},
        )

    assert response.status_code == 200
    assert response.json() == {"items": [{
        "profile_version_id": "profile-1",
        "profile_code": "ollama-qwen-story-parse",
        "profile_title": "Qwen · Story parse",
        "version_no": 1,
        "capability_code": "LLM_STORY_PARSE",
        "runtime_model_installation_ids": ["runtime-model-1"],
        "lifecycle_status": "PROFILE_SMOKE_PASSED",
        "latest_validation_run_id": "profile-smoke-1",
        "latest_validation_status": "SMOKE_PASSED",
    }], "count": 1, "read_only": True}
    assert "native_locator" not in response.text
    assert "base_url" not in response.text


def test_model_platform_ollama_profile_routes_require_explicit_provision_smoke_and_publish(workspace, database, monkeypatch) -> None:
    class _ProfileService:
        def __init__(self, *_args):
            pass

        def provision(self, installation_id: str, capability_code: str) -> ProvisionedOllamaProfile:
            assert (installation_id, capability_code) == ("runtime-model-1", "LLM_STORY_PARSE")
            return ProvisionedOllamaProfile("profile-1", "ollama-qwen-llm-story-parse", True)

        def smoke(self, profile_version_id: str) -> OllamaProfileSmokeResult:
            assert profile_version_id == "profile-1"
            return OllamaProfileSmokeResult("profile-validation-1", profile_version_id, "SMOKE_PASSED")

        def publish(self, profile_version_id: str, validation_run_id: str, reason: str) -> None:
            assert (profile_version_id, validation_run_id, reason) == ("profile-1", "profile-validation-1", "已确认")

    monkeypatch.setattr("local_drama.api.routes.model_platform_v2.ProfileTemplateService", _ProfileService)
    with TestClient(create_app(workspace)) as client:
        provision = client.post(
            "/api/v2/model-platform/registered-candidates/runtime-model-1/capability-offerings/LLM_STORY_PARSE:provision-profile"
        )
        smoke = client.post("/api/v2/model-platform/profile-versions/profile-1:smoke")
        publish = client.post(
            "/api/v2/model-platform/profile-versions/profile-1:publish",
            json={"validation_run_id": "profile-validation-1", "reason": "已确认"},
        )

    assert provision.json() == {"profile": {"profile_version_id": "profile-1", "profile_code": "ollama-qwen-llm-story-parse", "created": True}}
    assert smoke.json() == {"validation": {"validation_run_id": "profile-validation-1", "profile_version_id": "profile-1", "status": "SMOKE_PASSED"}}
    assert publish.json() == {"profile": {"profile_version_id": "profile-1", "status": "PUBLISHED"}}


def test_model_platform_assignment_and_resolution_do_not_expose_runtime_fields(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        saved = client.put(
            "/api/v2/model-platform/capability-assignments",
            json={
                "scope_type": "SYSTEM",
                "scope_id": "",
                "capability_code": "EMBEDDING_TEXT",
                "resolution_mode": "AUTO",
            },
        )
        response = client.get("/api/v2/model-platform/capability-resolution", params={"capability_code": "EMBEDDING_TEXT"})

    assert saved.status_code == 200
    assert response.status_code == 200
    resolution = response.json()["resolution"]
    assert resolution["blocked_reason"] == "NO_PUBLISHED_PROFILE"
    assert resolution["assignment_chain"] == [
        {"scope_type": "SYSTEM", "scope_id": "", "resolution_mode": "AUTO", "revision": 1}
    ]
    assert "base_url" not in str(resolution)
    assert "path" not in str(resolution)


def test_model_platform_profile_crosswalk_route_refuses_unpublished_or_unknown_profiles(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v2/model-platform/profile-version-crosswalks",
            json={
                "legacy_execution_profile_version_id": "missing-legacy",
                "v2_execution_profile_version_id": "missing-v2",
                "approval_reason": "必须拒绝从名称或不存在记录推断映射",
                "approved_by": "release-operator",
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MP_PROFILE_CROSSWALK_LEGACY_NOT_PUBLISHED"


def test_model_platform_execution_preview_fails_closed_without_a_published_profile(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v2/model-platform/execution-resolution:preview",
            json={"capability_code": "EMBEDDING_TEXT", "semantic_inputs": {"document_id": "document-1"}},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["read_only"] is True
    assert payload["preview"]["executable"] is False
    assert payload["preview"]["blockers"] == ["NO_PUBLISHED_PROFILE"]
    assert "base_url" not in str(payload)
    assert "path" not in str(payload)


def test_model_platform_execution_submit_requires_a_fresh_executable_preview(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v2/model-platform/executions",
            headers={"Idempotency-Key": "model-platform-no-profile"},
            json={
                "capability_code": "EMBEDDING_TEXT",
                "semantic_inputs": {"document_id": "document-1"},
                "expected_resolution_hash": "0" * 64,
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MP_EXECUTION_NOT_READY"
