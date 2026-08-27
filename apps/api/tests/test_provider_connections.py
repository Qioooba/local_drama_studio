from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.main import create_app


def test_provider_connection_metadata_never_returns_secret_in_list(workspace, database, monkeypatch) -> None:
    secrets: dict[str, str] = {}
    monkeypatch.setattr(
        "local_drama.platform.windows.credentials.WindowsCredentialStore.get",
        lambda _store, ref: secrets.get(ref.key),
    )
    monkeypatch.setattr(
        "local_drama.platform.windows.credentials.WindowsCredentialStore.put",
        lambda _store, ref, value: secrets.__setitem__(ref.key, value),
    )
    monkeypatch.setattr(
        "local_drama.platform.windows.credentials.WindowsCredentialStore.delete",
        lambda _store, ref: secrets.pop(ref.key, None) is not None,
    )

    with TestClient(create_app(workspace)) as client:
        created = client.post(
            "/api/v1/provider-connections",
            json={
                "code": "deepseek-main",
                "title": "DeepSeek 主连接",
                "provider_kind": "DEEPSEEK",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
                "credential_source": "WINDOWS_CREDENTIAL_MANAGER",
            },
        )
        assert created.status_code == 201
        connection = created.json()["connection"]
        connection_id = connection["id"]

        replaced = client.put(f"/api/v1/provider-connections/{connection_id}/secret", json={"secret": "sk-test-secret-123"})
        assert replaced.status_code == 200
        assert replaced.json()["connection"]["masked_secret"] != "sk-test-secret-123"
        listed = client.get("/api/v1/provider-connections")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["has_secret"] is True
        assert "sk-test-secret-123" not in listed.text

        revealed = client.post(f"/api/v1/provider-connections/{connection_id}:reveal-secret")
        assert revealed.status_code == 200
        assert revealed.json() == {"secret": "sk-test-secret-123", "expires_in_seconds": 60}
        assert revealed.headers["cache-control"] == "no-store, private"

        deleted = client.delete(f"/api/v1/provider-connections/{connection_id}/secret")
        assert deleted.status_code == 200
        assert deleted.json()["connection"]["has_secret"] is False

    with database.connect() as connection:
        audit_rows = connection.execute(
            "SELECT action, metadata_redacted_json FROM audit_events WHERE subject_id=? ORDER BY event_id",
            (connection_id,),
        ).fetchall()
    assert any(row["action"] == "PROVIDER_SECRET_REVEALED" for row in audit_rows)
    assert all("sk-test-secret-123" not in str(row["metadata_redacted_json"]) for row in audit_rows)


def test_provider_connection_rejects_ambiguous_url(workspace) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            "/api/v1/provider-connections",
            json={
                "code": "bad-provider",
                "title": "不安全连接",
                "provider_kind": "OPENAI_COMPAT",
                "base_url": "https://user:password@example.com/v1?token=bad",
                "credential_source": "NONE",
            },
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PROVIDER_BASE_URL_AMBIGUOUS"
