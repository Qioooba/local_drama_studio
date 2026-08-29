from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from local_drama.application.documents import DocumentImportService
from local_drama.application.local_llm import LocalLLMService, _mask_key
from local_drama.application.projects import ProjectService
from local_drama.application.worker import LocalMediaWorker
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.local_llm import LocalLLMClient
from local_drama.main import create_app


def test_settings_from_env_supports_openai_compat(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_DRAMA_LLM_PROVIDER", "OPENAI_COMPAT")
    monkeypatch.setenv("LOCAL_DRAMA_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LOCAL_DRAMA_LLM_MODEL", "deepseek-v4-flash-vision-exp")
    monkeypatch.setenv("LOCAL_DRAMA_LLM_API_KEY", "sk-test-deepseek-key-123456")

    settings = Settings.from_env()
    assert settings.mode == "LOCAL_ONLY"
    assert settings.llm_provider == "OPENAI_COMPAT"
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_model == "deepseek-v4-flash-vision-exp"
    assert settings.llm_api_key == "sk-test-deepseek-key-123456"


def test_settings_from_env_supports_deepseek_key_alias(monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_DRAMA_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-direct-key-9999")
    settings = Settings.from_env()
    assert settings.llm_api_key == "sk-deepseek-direct-key-9999"


def test_mask_key_utility() -> None:
    assert _mask_key(None) is None
    assert _mask_key("") is None
    assert _mask_key("123") == "••••"
    assert _mask_key("sk-1234567890abcdef") == "••••••••cdef"


def test_local_llm_client_ollama_loopback_guard() -> None:
    with pytest.raises(DomainRuleError) as caught:
        LocalLLMClient("https://api.deepseek.com", "deepseek-chat", provider="OLLAMA_LOOPBACK")
    assert caught.value.code == "LOCAL_ONLY_ENDPOINT_REQUIRED"

    with pytest.raises(DomainRuleError) as query_err:
        LocalLLMClient("http://127.0.0.1:11434/?key=123", "qwen2.5:7b", provider="OLLAMA_LOOPBACK")
    assert query_err.value.code == "LOCAL_ONLY_ENDPOINT_AMBIGUOUS"


def test_local_llm_client_openai_compat_initialization() -> None:
    client = LocalLLMClient(
        "https://api.deepseek.com",
        "deepseek-v4-flash-vision-exp",
        provider="OPENAI_COMPAT",
        api_key="sk-secret-key-1234",
    )
    assert client.provider == "OPENAI_COMPAT"
    assert client.base_url == "https://api.deepseek.com"
    assert client.model == "deepseek-v4-flash-vision-exp"
    assert client.api_key == "sk-secret-key-1234"

    # URL path normalization
    assert client._endpoint_url("/v1/chat/completions") == "https://api.deepseek.com/v1/chat/completions"
    client_v1 = LocalLLMClient(
        "https://api.deepseek.com/v1",
        "deepseek-v4-flash-vision-exp",
        provider="OPENAI_COMPAT",
    )
    assert client_v1._endpoint_url("/v1/chat/completions") == "https://api.deepseek.com/v1/chat/completions"


def test_ollama_model_catalog_is_read_only_and_preserves_runtime_metadata(workspace, monkeypatch) -> None:
    contacted_base_urls: list[str] = []

    def local_catalog(self):
        contacted_base_urls.append(self.base_url)
        return [
            {
                "name": "qwen3:8b",
                "model": "qwen3:8b",
                "modified_at": "2026-08-28T12:00:00Z",
                "size": 5_200_000_000,
                "digest": "sha256:catalog-test",
                "details": {
                    "format": "gguf",
                    "family": "qwen3",
                    "families": ["qwen3"],
                    "parameter_size": "8.2B",
                    "quantization_level": "Q4_K_M",
                },
            }
        ]

    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.tags",
        local_catalog,
    )

    remote_default = workspace.model_copy(update={
        "llm_provider": "OPENAI_COMPAT",
        "llm_base_url": "https://api.deepseek.com",
        "llm_model": "deepseek-chat",
    })
    with TestClient(create_app(remote_default)) as client:
        response = client.get("/api/v1/local-llm/models")

    assert response.status_code == 200
    catalog = response.json()["catalog"]
    assert catalog["provider"] == "OLLAMA_LOOPBACK"
    assert catalog["base_url"] == "http://127.0.0.1:11434"
    assert catalog["count"] == 1
    assert catalog["read_only"] is True
    assert catalog["runtime_contacted"] is True
    assert catalog["mutated"] is False
    assert contacted_base_urls == ["http://127.0.0.1:11434"]
    assert catalog["items"][0] == {
        "name": "qwen3:8b",
        "model": "qwen3:8b",
        "modified_at": "2026-08-28T12:00:00Z",
        "size_bytes": 5_200_000_000,
        "digest": "sha256:catalog-test",
        "format": "gguf",
        "family": "qwen3",
        "families": ["qwen3"],
        "parameter_size": "8.2B",
        "quantization_level": "Q4_K_M",
    }


def test_ollama_model_catalog_rejects_public_endpoints(workspace) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/local-llm/models", params={"base_url": "http://example.com:11434"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "LOCAL_ONLY_ENDPOINT_REQUIRED"


def test_local_llm_client_openai_compat_probe_four_levels(monkeypatch) -> None:
    client = LocalLLMClient(
        "https://api.deepseek.com",
        "deepseek-v4-flash-vision-exp",
        provider="OPENAI_COMPAT",
        api_key="sk-test-key",
    )

    def mock_request(path: str, payload=None, timeout_seconds=None):
        if path == "/v1/models":
            return {"data": [{"id": "deepseek-v4-flash-vision-exp"}, {"id": "deepseek-chat"}]}
        if path == "/v1/chat/completions":
            return {"choices": [{"message": {"role": "assistant", "content": '{"ready":true}'}}]}
        return {}

    monkeypatch.setattr(client, "_request", mock_request)
    probe_result = client.probe(load_test=True)

    assert probe_result["status"] == "PASS"
    assert probe_result["probe_level_passed"] == 4
    levels = probe_result["probe_levels"]
    assert levels["level_1_network"]["passed"] is True
    assert levels["level_2_auth"]["passed"] is True
    assert levels["level_3_model"]["passed"] is True
    assert levels["level_4_inference"]["passed"] is True
    assert probe_result["model_present"] is True


def test_local_llm_client_ollama_probe_disables_thinking(monkeypatch) -> None:
    client = LocalLLMClient("http://127.0.0.1:11434", "qwen3.8:27b", provider="OLLAMA_LOOPBACK")
    captured: dict[str, object] = {}

    def mock_request(path: str, payload=None, timeout_seconds=None):
        if path == "/api/tags":
            return {"models": [{"name": "qwen3.8:27b"}]}
        captured.update(payload or {})
        return {"response": '{"ready":true}'}

    monkeypatch.setattr(client, "_request", mock_request)

    result = client.probe(load_test=True)

    assert result["status"] == "PASS"
    assert captured["think"] is False
    assert captured["options"] == {"temperature": 0, "num_predict": 32}


def test_local_llm_client_ollama_chat_json_disables_thinking(monkeypatch) -> None:
    client = LocalLLMClient("http://127.0.0.1:11434", "qwen3.8:27b", provider="OLLAMA_LOOPBACK")
    captured: dict[str, object] = {}

    def mock_request(path: str, payload=None, timeout_seconds=None):
        captured.update(payload or {})
        return {"message": {"content": '{"ready":true}'}}

    monkeypatch.setattr(client, "_request", mock_request)

    assert client.chat_json("Return JSON.", "Ready?") == {"ready": True}
    assert captured["think"] is False


def test_local_llm_client_openai_compat_probe_auth_failure(monkeypatch) -> None:
    client = LocalLLMClient(
        "https://api.deepseek.com",
        "deepseek-v4-flash-vision-exp",
        provider="OPENAI_COMPAT",
        api_key="sk-invalid-key",
    )

    def mock_request(path: str, payload=None, timeout_seconds=None):
        raise DomainRuleError("LLM_AUTH_FAILED", "LLM 认证失败，请检查 API Key", {"status_code": 401})

    monkeypatch.setattr(client, "_request", mock_request)
    probe_result = client.probe(load_test=True)

    assert probe_result["status"] == "BLOCKED"
    assert probe_result["probe_level_passed"] == 1
    assert probe_result["probe_levels"]["level_1_network"]["passed"] is True
    assert probe_result["probe_levels"]["level_2_auth"]["passed"] is False
    assert probe_result["error_code"] == "LLM_AUTH_FAILED"


def test_verified_deepseek_probe_can_store_key_in_os_credential_manager(workspace, monkeypatch) -> None:
    remembered: list[str] = []
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.probe",
        lambda self, load_test=False: {
            "status": "PASS",
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "probe_level_passed": 4,
            "probe_levels": {},
            "load_test": load_test,
        },
    )
    monkeypatch.setattr(
        "local_drama.platform.windows.credentials.WindowsCredentialStore.put",
        lambda _store, _ref, value: remembered.append(value),
    )

    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v1/local-llm/probe",
            json={
                "provider": "OPENAI_COMPAT",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "api_key": "sk-verified-probe-secret",
                "remember_api_key": True,
                "load_test": True,
                "allow_remote_outbound": True,
            },
        )

    assert response.status_code == 200
    assert remembered == ["sk-verified-probe-secret"]
    assert response.json()["probe"]["secret_persisted"] is True
    assert response.json()["probe"]["credential_store"] == "WINDOWS_CREDENTIAL_MANAGER"


def test_local_llm_client_chat_json_multimodal_formatting(monkeypatch) -> None:
    client = LocalLLMClient(
        "https://api.deepseek.com",
        "deepseek-v4-flash-vision-exp",
        provider="OPENAI_COMPAT",
        api_key="sk-test",
    )

    captured_payloads = []

    def mock_request(path: str, payload=None, timeout_seconds=None):
        captured_payloads.append(payload)
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '<think>Analysis complete</think>```json\n{"visual_qc":"PASS","score":0.95}\n```',
                    }
                }
            ]
        }

    monkeypatch.setattr(client, "_request", mock_request)
    result = client.chat_json(
        "You are a QC inspector.",
        "Inspect image frame.",
        images=["https://example.com/frame1.jpg", "base64encodedrawbytes=="],
        inference_options={"temperature": 0.35, "top_p": 0.8, "max_tokens": 1536},
    )

    assert result == {"visual_qc": "PASS", "score": 0.95}
    assert len(captured_payloads) == 1
    assert captured_payloads[0]["temperature"] == 0.35
    assert captured_payloads[0]["top_p"] == 0.8
    assert captured_payloads[0]["max_tokens"] == 1536
    user_content = captured_payloads[0]["messages"][1]["content"]
    assert isinstance(user_content, list)
    assert user_content[0] == {"type": "text", "text": "Inspect image frame."}
    assert user_content[1] == {"type": "image_url", "image_url": {"url": "https://example.com/frame1.jpg"}}
    assert user_content[2] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,base64encodedrawbytes=="}}


def test_sync_and_publish_openai_compat_profile(workspace, database, monkeypatch) -> None:
    service = LocalLLMService(database, workspace)

    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.probe",
        lambda self, load_test=False: {
            "status": "PASS",
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "probe_level_passed": 4,
            "probe_levels": {
                "level_1_network": {"passed": True},
                "level_2_auth": {"passed": True},
                "level_3_model": {"passed": True},
                "level_4_inference": {"passed": True},
            },
            "model_present": True,
            "load_test": load_test,
        },
    )

    # 1. Without allow_remote_outbound, remote sync fails with OUTBOUND_CONFIRMATION_REQUIRED
    with pytest.raises(DomainRuleError) as outbound_err:
        service.sync_candidate(
            model="deepseek-v4-flash-vision-exp",
            capability="LLM_STORY_PARSE",
            provider="OPENAI_COMPAT",
            base_url="https://api.deepseek.com",
            api_key="sk-test-key-123456",
            allow_remote_outbound=False,
        )
    assert outbound_err.value.code == "OUTBOUND_CONFIRMATION_REQUIRED"

    # 2. With allow_remote_outbound=True, candidate sync succeeds
    candidate = service.sync_candidate(
        model="deepseek-v4-flash-vision-exp",
        capability="LLM_STORY_PARSE",
        provider="OPENAI_COMPAT",
        base_url="https://api.deepseek.com",
        api_key="sk-test-key-123456",
        allow_remote_outbound=True,
    )
    assert candidate["status"] in {"CANDIDATE_UNVERIFIED", "PUBLISHED"}
    assert "llm-openai-deepseek-v4-flash-vision-exp-llm-story-parse" in candidate["profile_code"]

    # Verify no plaintext key in database
    with database.connect() as conn:
        row = conn.execute(
            "SELECT capability_json FROM execution_profile_versions WHERE id=?",
            (candidate["profile_version_id"],),
        ).fetchone()
        cap_data = json.loads(row["capability_json"])
        assert "sk-test-key-123456" not in row["capability_json"]
        assert cap_data["has_api_key"] is True
        assert cap_data["masked_api_key"] == "••••••••3456"

    # 3. Publish profile
    published = service.publish(
        candidate["profile_version_id"],
        api_key="sk-test-key-123456",
        allow_remote_outbound=True,
    )
    assert published["status"] == "PUBLISHED"
    assert published["profile_code"] == candidate["profile_code"]
    assert published["version_no"] == 1
    publication = published["publication"]
    assert publication["destination"] == "GLOBAL_CAPABILITY_CATALOG"
    assert publication["scope"] == "LOCAL_STUDIO"
    assert publication["consumer_scope"] == "ALL_PROJECTS"
    assert publication["capability"] == "LLM_STORY_PARSE"
    assert publication["model"] == "deepseek-v4-flash-vision-exp"
    assert publication["provider"] == "OPENAI_COMPAT"
    assert publication["published_at"]


def test_deepseek_expands_one_sentence_video_prompt_without_persisting_key(workspace, database, monkeypatch) -> None:
    service = LocalLLMService(database, workspace)
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.probe",
        lambda self, load_test=False: {
            "status": "PASS",
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "probe_level_passed": 4,
            "probe_levels": {},
            "model_present": True,
            "load_test": load_test,
        },
    )
    candidate = service.sync_candidate(
        model="deepseek-chat",
        capability="LLM_STORY_PARSE",
        provider="OPENAI_COMPAT",
        base_url="https://api.deepseek.com",
        api_key="sk-one-sentence-secret",
        allow_remote_outbound=True,
    )
    service.publish(
        str(candidate["profile_version_id"]),
        api_key="sk-one-sentence-secret",
        allow_remote_outbound=True,
    )
    system_prompts: list[str] = []

    def expand_response(self, system, user, **kwargs):
        system_prompts.append(system)
        return {
            "title": "雾桥纸伞",
            "video_prompt": "晨雾中的江南石桥，女子撑纸伞缓步前行，柔和逆光，镜头稳定向前推进。",
            "keyframe_prompt": "A woman with a paper umbrella prepares to walk across a misty Jiangnan stone bridge, soft backlight, medium composition.",
            "subject_action": "女子撑纸伞缓步过桥",
            "environment": "晨雾中的江南石桥，柔和逆光",
            "shot_type": "中景",
            "camera_movement": "缓慢推镜",
        }

    monkeypatch.setattr("local_drama.infrastructure.local_llm.LocalLLMClient.chat_json", expand_response)

    with pytest.raises(DomainRuleError) as outbound:
        service.expand_video_prompt(
            str(candidate["profile_version_id"]),
            "晨雾里有人撑伞过桥",
            api_key="sk-one-sentence-secret",
        )
    assert outbound.value.code == "OUTBOUND_CONFIRMATION_REQUIRED"

    plan = service.expand_video_prompt(
        str(candidate["profile_version_id"]),
        "晨雾里有人撑伞过桥",
        api_key="sk-one-sentence-secret",
        allow_remote_outbound=True,
    )
    assert plan["provider"] == "OPENAI_COMPAT"
    assert plan["remote"] is True
    assert plan["network_contacted"] is True
    assert plan["secret_persisted"] is False
    assert plan["director_intent"]["target_duration_ms"] == 4000
    assert "keyframe_prompt 必须使用 English" in system_prompts[-1]
    serialized = json.dumps(plan, ensure_ascii=False)
    assert "sk-one-sentence-secret" not in serialized


def test_deepseek_one_sentence_can_remember_key_in_os_credential_store(workspace, database, monkeypatch) -> None:
    service = LocalLLMService(database, workspace)
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.probe",
        lambda self, load_test=False: {
            "status": "PASS",
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "probe_level_passed": 4,
            "probe_levels": {},
            "model_present": True,
            "load_test": load_test,
        },
    )
    candidate = service.sync_candidate(
        model="deepseek-chat",
        capability="LLM_STORY_PARSE",
        provider="OPENAI_COMPAT",
        base_url="https://api.deepseek.com",
        api_key="sk-remember-without-database",
        allow_remote_outbound=True,
    )
    profile_id = str(candidate["profile_version_id"])
    service.publish(profile_id, api_key="sk-remember-without-database", allow_remote_outbound=True)
    remembered: list[str] = []
    monkeypatch.setattr(
        "local_drama.platform.windows.credentials.WindowsCredentialStore.put",
        lambda _store, _ref, value: remembered.append(value),
    )
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.chat_json",
        lambda self, system, user, **kwargs: {
            "title": "记住密钥测试",
            "video_prompt": "一只白鸟掠过湖面，晨光，稳定跟拍。",
            "keyframe_prompt": "A white bird prepares to skim across a dawn-lit lake, medium composition.",
            "subject_action": "白鸟掠过湖面",
            "environment": "晨光湖面",
            "shot_type": "中景",
            "camera_movement": "跟拍",
        },
    )

    plan = service.expand_video_prompt(
        profile_id,
        "白鸟掠过晨光中的湖面",
        api_key="sk-remember-without-database",
        remember_api_key=True,
        allow_remote_outbound=True,
    )

    assert remembered == ["sk-remember-without-database"]
    assert plan["secret_persisted"] is True
    assert plan["credential_store"] == "WINDOWS_CREDENTIAL_MANAGER"
    with database.connect() as connection:
        serialized_rows = "\n".join(
            str(value) for row in connection.execute("SELECT capability_json FROM execution_profile_versions").fetchall() for value in row
        )
    assert "sk-remember-without-database" not in serialized_rows


def test_deepseek_story_breakdown_full_pipeline(workspace, database, monkeypatch) -> None:
    # Set up project and source document
    project = ProjectService(database, workspace.projects_root).create_project(
        code="ds_drama",
        title="DeepSeek Drama",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )
    source = workspace.work_root / "deepseek-story.md"
    source.write_text("# 第一幕\n\n夜色深沉，女侦探林岚推开实验室的大门，发现地上的金色试管已被打破。", encoding="utf-8")

    import_svc = DocumentImportService(database, workspace)
    imported = import_svc.import_document(str(project["id"]), source)
    import_svc.commit(imported["import_session_id"], imported["preview_hash"])

    # Configure and publish DeepSeek profile
    llm_svc = LocalLLMService(database, workspace)
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.probe",
        lambda self, load_test=False: {
            "status": "PASS",
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "probe_level_passed": 4,
            "probe_levels": {
                "level_1_network": {"passed": True},
                "level_2_auth": {"passed": True},
                "level_3_model": {"passed": True},
                "level_4_inference": {"passed": True},
            },
            "model_present": True,
            "load_test": load_test,
        },
    )

    candidate = llm_svc.sync_candidate(
        model="deepseek-v4-flash-vision-exp",
        capability="LLM_STORY_PARSE",
        provider="OPENAI_COMPAT",
        base_url="https://api.deepseek.com",
        api_key="sk-deepseek-pipeline-test",
        allow_remote_outbound=True,
    )
    published = llm_svc.publish(
        candidate["profile_version_id"],
        api_key="sk-deepseek-pipeline-test",
        allow_remote_outbound=True,
    )

    expected_output = {
        "scenes": [
            {
                "scene_no": 1,
                "title": "实验室潜入",
                "summary": "林岚发现破碎试管",
                "characters": ["林岚"],
                "shots": [
                    {
                        "shot_no": 1,
                        "visual": "中景",
                        "action": "夜色深沉，推开大门",
                        "dialogue": "",
                        "duration_seconds": 3,
                    },
                    {
                        "shot_no": 2,
                        "visual": "特写",
                        "action": "发现地上的金色试管已被打破",
                        "dialogue": "",
                        "duration_seconds": 3,
                    },
                ],
            }
        ],
        "confidence": {"overall": 0.96, "notes": ["场景细节充分"]},
        "questions": ["试管内的液体颜色是否需要特写提示？"],
        "source_passages": [{"scene_no": 1, "quote": "夜色深沉，女侦探林岚推开实验室的大门，发现地上的金色试管已被打破。"}],
    }

    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.chat_json",
        lambda self, prompt, text, images=None, **_kwargs: expected_output,
    )

    with TestClient(create_app(workspace)) as client:
        # 1. Test probe API endpoint
        probe_resp = client.post(
            "/api/v1/local-llm/probe",
            json={
                "provider": "OPENAI_COMPAT",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash-vision-exp",
                "api_key": "sk-deepseek-pipeline-test",
                "load_test": True,
                "allow_remote_outbound": True,
            },
        )
        assert probe_resp.status_code == 200, f"probe_resp failed with {probe_resp.status_code}: {probe_resp.json()}"
        assert probe_resp.json()["probe"]["status"] == "PASS"

        # 2. Request breakdown with published DeepSeek profile
        resp = client.post(
            f"/api/v1/import-sessions/{imported['import_session_id']}:request-breakdown",
            headers={"Idempotency-Key": "ds-breakdown-key-1"},
            json={"profile_version_id": published["profile_version_id"]},
        )
        assert resp.status_code == 202
        submission = resp.json()
        assert submission["automatic_apply"] is False
        assert submission["requires_human_action"] is True
        job = submission["job"]
        assert job["type"] == "SCRIPT_BREAKDOWN_LOCAL_LLM"

    # Worker executes the breakdown job
    outcome = LocalMediaWorker(database, workspace).run_once("deepseek-worker", ["CPU"])
    assert outcome is not None
    assert outcome["result"]["job_state"] == "SUCCEEDED"

    # Verify breakdown draft was generated with exact facts
    drafts = llm_svc.list_breakdown_drafts(str(project["id"]))
    assert len(drafts) == 1
    assert drafts[0]["status"] == "DRAFT_READY"
    assert drafts[0]["draft"]["scenes"][0]["characters"] == ["林岚"]
    assert drafts[0]["requires_human_action"] is True
    assert drafts[0]["automatic_apply"] is False


def test_system_runs_normally_without_cloud_key(workspace, database) -> None:
    # When no remote key is set, local LLM service status fails gracefully with model required/not configured
    # without making any outbound requests or crashing
    llm_svc = LocalLLMService(database, workspace)
    status = llm_svc.status()
    assert status["status"] == "BLOCKED"
    assert status["provider"] == "OLLAMA_LOOPBACK"
    assert status["has_api_key"] is False
