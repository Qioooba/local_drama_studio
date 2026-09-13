from __future__ import annotations

import json
from typing import Any

import pytest

from local_drama.application.provider_connections import ProviderConnectionService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.service_composition import build_story_ai


def _insert_profile(
    database,
    *,
    version_id: str,
    status: str = "PUBLISHED",
    capability: str = "LLM_STORY_PARSE",
    capability_json: dict[str, Any] | None = None,
    model_bundle_json: dict[str, Any] | None = None,
    updated_at: str = "2026-09-12T00:00:00+00:00",
) -> None:
    profile_id = f"profile-{version_id}"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO execution_profiles
            (id, code, title, created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, ?, ?, ?, 'test', 1, 'v2')""",
            (profile_id, profile_id, profile_id, updated_at, updated_at),
        )
        connection.execute(
            """INSERT INTO execution_profile_versions
            (id, execution_profile_id, version_no, capability, model_bundle_json,
             input_contract_json, parameter_schema_json, status, capability_json,
             created_at, updated_at, created_by, revision, schema_version)
            VALUES (?, ?, 1, ?, ?, '{}', '{}', ?, ?, ?, ?, 'test', 1, 'v2')""",
            (
                version_id,
                profile_id,
                capability,
                json.dumps(model_bundle_json or {}),
                status,
                json.dumps(capability_json or {}),
                updated_at,
                updated_at,
            ),
        )


class _HTTPResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "_HTTPResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_build_story_ai_uses_explicit_published_profile_connection_for_request(
    workspace,
    database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("K01_REMOTE_API_KEY", "profile-secret")
    connection = ProviderConnectionService(database, workspace).create(
        code="k01-remote",
        title="K01 Remote",
        provider_kind="OPENAI_COMPAT",
        base_url="https://llm.example.test/v1",
        model="connection-default-model",
        credential_source="ENVIRONMENT",
        environment_variable_name="K01_REMOTE_API_KEY",
    )
    _insert_profile(
        database,
        version_id="story-remote-v1",
        capability_json={"provider": "OLLAMA_LOOPBACK", "base_url": "http://127.0.0.1:1"},
        model_bundle_json={
            "provider_connection_id": connection["id"],
            "model": "profile-bound-model",
        },
    )

    captured: dict[str, Any] = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _HTTPResponse({"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})

    monkeypatch.setattr("local_drama.infrastructure.local_llm.urlopen", fake_urlopen)

    client, info = build_story_ai(database, workspace).resolve_client("story-remote-v1")
    assert client.chat_json("system", "user") == {}
    assert info == {
        "required": True,
        "ready": True,
        "profile_version_id": "story-remote-v1",
        "provider": "OPENAI_COMPAT",
        "model": "profile-bound-model",
        "base_url": "https://llm.example.test/v1",
    }
    assert captured["url"] == "https://llm.example.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer profile-secret"
    assert captured["payload"]["model"] == "profile-bound-model"


def test_build_story_ai_deterministically_uses_newest_published_local_profile_without_cloud_key(
    workspace,
    database,
) -> None:
    settings = workspace.model_copy(update={"llm_api_key": "global-cloud-key", "llm_model": "global-default-model"})
    _insert_profile(
        database,
        version_id="story-local-old",
        capability_json={
            "provider": "OLLAMA_LOOPBACK",
            "base_url": "http://127.0.0.1:11434",
            "model": "old-local-model",
        },
        model_bundle_json={"model": "old-local-model"},
        updated_at="2026-09-11T00:00:00+00:00",
    )
    _insert_profile(
        database,
        version_id="story-local-new",
        capability_json={
            "provider": "OLLAMA_LOOPBACK",
            "base_url": "http://127.0.0.1:11435",
            "model": "new-local-model",
        },
        model_bundle_json={"model": "new-local-model"},
        updated_at="2026-09-12T00:00:00+00:00",
    )

    client, info = build_story_ai(database, settings).resolve_client()

    assert info["profile_version_id"] == "story-local-new"
    assert client.provider == "OLLAMA_LOOPBACK"
    assert client.base_url == "http://127.0.0.1:11435"
    assert client.model == "new-local-model"
    assert client.api_key is None


def test_build_story_ai_uses_profile_id_as_stable_tiebreaker_for_default_profile(
    workspace,
    database,
) -> None:
    common_config = {
        "provider": "OLLAMA_LOOPBACK",
        "base_url": "http://127.0.0.1:11434",
    }
    _insert_profile(
        database,
        version_id="story-tie-z",
        capability_json={**common_config, "model": "z-model"},
        model_bundle_json={"model": "z-model"},
    )
    _insert_profile(
        database,
        version_id="story-tie-a",
        capability_json={**common_config, "model": "a-model"},
        model_bundle_json={"model": "a-model"},
    )

    client, info = build_story_ai(database, workspace).resolve_client()

    assert info["profile_version_id"] == "story-tie-a"
    assert client.model == "a-model"


@pytest.mark.parametrize(
    ("status", "capability"),
    [
        ("DRAFT", "LLM_STORY_PARSE"),
        ("RETIRED", "LLM_STORY_PARSE"),
        ("PUBLISHED", "VIDEO_I2V"),
    ],
)
def test_build_story_ai_rejects_unpublished_or_mismatched_explicit_profile(
    workspace,
    database,
    status: str,
    capability: str,
) -> None:
    _insert_profile(
        database,
        version_id=f"invalid-{status}-{capability}",
        status=status,
        capability=capability,
        capability_json={
            "provider": "OLLAMA_LOOPBACK",
            "base_url": "http://127.0.0.1:11434",
            "model": "must-not-run",
        },
    )

    with pytest.raises(DomainRuleError) as caught:
        build_story_ai(database, workspace).resolve_client(f"invalid-{status}-{capability}")

    assert caught.value.code == "PIPELINE_LLM_PROFILE_INVALID"
