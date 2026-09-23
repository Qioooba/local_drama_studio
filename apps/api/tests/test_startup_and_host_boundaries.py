"""Startup and request boundaries: optional manifest, untrusted Host.

Reproduced defects on the audit snapshot:

* ``S2`` — ``load_manifest`` validated only the outer type, ``manifest_type``,
  ``read_only_inventory`` and ``canonical_model_root.path``.  A manifest that is
  *valid JSON* but semantically wrong (no ``manifest_version``,
  ``runtime: null``, ``models: null``) raised ``KeyError`` / ``TypeError`` /
  ``AttributeError``, which escaped the optional step's narrow ``except`` clause
  and aborted the whole lifespan instead of only disabling model capabilities.
* ``S3`` — the Host trust check ran only for ``state_changing and origin``, so an
  untrusted ``Host`` could still read business data with a plain ``GET`` (a
  DNS-rebound request normally sends no ``Origin`` at all).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from local_drama.infrastructure.manifest import ManifestValidationError, load_manifest
from local_drama.middleware import _host_is_trusted, _request_host_authority


def _valid_manifest() -> dict[str, Any]:
    return {
        "manifest_type": "canonical_model_inventory",
        "manifest_version": "2026.09.22",
        "read_only_inventory": True,
        "canonical_model_root": {"path": "E:/models"},
        "runtime": {"comfyui_api": {"base_url": "http://127.0.0.1:8188", "port_8188_listening": True}},
        "models": {"partitions": {}},
        "h3_capabilities": {},
        "authoritative_current_state": {"worker_policy": "LOCAL_ONLY", "route_status": {}, "forbidden_assets": []},
    }


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "model_manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("mutation", "expected_field"),
    [
        (lambda data: data.pop("manifest_version"), "manifest_version"),
        (lambda data: data.update(runtime=None), "runtime"),
        (lambda data: data.update(manifest_version=""), "manifest_version"),
        (lambda data: data.update(manifest_version=123), "manifest_version"),
        (lambda data: data.update(runtime=[]), "runtime"),
        (lambda data: data.update(models="none"), "models"),
        (lambda data: data.update(h3_capabilities=7), "h3_capabilities"),
        (lambda data: data.update(authoritative_current_state="x"), "authoritative_current_state"),
        (lambda data: data.update(models={"partitions": 3}), "partitions"),
        (lambda data: data.update(models={"partitions": ["a"]}), "partitions"),
        (lambda data: data["runtime"].update(comfyui_api="http://x"), "comfyui_api"),
        (lambda data: data["authoritative_current_state"].update(forbidden_assets="x"), "forbidden_assets"),
    ],
)
def test_semantically_invalid_manifests_raise_a_typed_validation_error(
    tmp_path: Path, mutation: Any, expected_field: str
) -> None:
    """Every shape of "valid JSON, wrong semantics" is a ManifestValidationError."""

    data = _valid_manifest()
    mutation(data)
    path = _write(tmp_path, data)
    with pytest.raises(ManifestValidationError) as error:
        load_manifest(path)
    assert expected_field in str(error.value)


def test_a_valid_manifest_still_loads(tmp_path: Path) -> None:
    snapshot = load_manifest(_write(tmp_path, _valid_manifest()))
    assert snapshot.version == "2026.09.22"
    assert snapshot.runtime["comfyui_api"]["base_url"] == "http://127.0.0.1:8188"
    assert snapshot.as_public_dict()["worker_policy"] == "LOCAL_ONLY"


def test_a_missing_version_does_not_crash_the_public_projection() -> None:
    from local_drama.infrastructure.manifest import ManifestSnapshot

    path = Path("nowhere.json")
    snapshot = ManifestSnapshot(path=path, sha256="a" * 64, data={})
    assert snapshot.version == ""
    assert snapshot.as_public_dict()["manifest_version"] == ""


# --------------------------------------------------------------------------- #
# S2 through the real application lifespan
# --------------------------------------------------------------------------- #
def _app_client(
    workspace: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: object
) -> TestClient:
    del monkeypatch  # kept in the signature: the app under test shares this fixture's tmp roots
    from scripts.migrate import migrate

    from local_drama.main import create_app

    # The manifest the app reads must be a per-test file.  ``Settings.manifest_path``
    # falls back to the host's real ``model_manifest.json`` next to the release root,
    # so writing to ``settings.manifest_path`` would overwrite the machine's model
    # inventory with this fixture (which then broke every manifest-driven test).
    manifest_path = tmp_path / "host-boundary-model_manifest.json"
    settings = workspace.model_copy(deep=True).model_copy(
        update={"model_manifest_override": manifest_path}
    )
    settings.ensure_roots()
    settings.manifest_path.write_text(
        manifest if isinstance(manifest, str) else json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    migrate(settings.database_path)
    client = TestClient(create_app(settings))
    client.__enter__()
    return client


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.pop("manifest_version"),
        lambda data: data.update(runtime=None),
        lambda data: data.update(models="none"),
    ],
)
def test_optional_manifest_failure_never_blocks_the_api(
    workspace: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: Any
) -> None:
    data = _valid_manifest()
    mutation(data)
    client = _app_client(workspace, tmp_path, monkeypatch, data)
    try:
        ready = client.get("/api/v1/health/ready")
        assert ready.status_code == 200, ready.text
        report = ready.json()
        initialization = report.get("initialization") or {}
        assert initialization.get("model_manifest") == "failed"
        assert initialization.get("review_templates") == "completed"
        # The required steps are untouched: the review templates are still seeded.
        templates = client.get("/api/v1/review-templates")
        assert templates.status_code == 200
        assert len(templates.json()["items"]) == 6
    finally:
        client.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# S3: Host trust applies to reads too
# --------------------------------------------------------------------------- #
def test_untrusted_host_reads_are_rejected_before_any_business_data(
    workspace: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _app_client(workspace, tmp_path, monkeypatch, _valid_manifest())
    try:
        created = client.post(
            "/api/v2/explainers",
            json={
                "title": "审查用机密标题",
                "topic": "",
                "content_kind": "FACTUAL_EXPLAINER",
                "input_kind": "TOPIC",
                "target_seconds": 300,
                "source_locale": "zh-CN",
                "outputs": [{"edition_key": "main", "voice_locale": "zh-CN"}],
            },
            headers={"Idempotency-Key": "host-1"},
        )
        assert created.status_code == 201, created.text

        # A DNS-rebound GET carries the attacker's Host and normally no Origin.
        for headers in (
            {"Host": "untrusted.example:3210"},
            {"Host": "untrusted.example:3210", "Origin": "http://untrusted.example:3210"},
        ):
            response = client.get("/api/v2/explainers", headers=headers)
            assert response.status_code == 403, response.text
            assert response.json()["error"]["code"] == "UNTRUSTED_HOST"
            assert "审查用机密标题" not in response.text
            live = client.get("/api/v1/health/live", headers=headers)
            assert live.status_code == 403
            head = client.head("/api/v2/explainers", headers=headers)
            assert head.status_code == 403
            media = client.get("/api/v2/explainers/proj-x", headers=headers)
            assert media.status_code == 403
            write = client.post(
                "/api/v2/explainers",
                json={},
                headers={**headers, "Idempotency-Key": "host-2"},
            )
            assert write.status_code == 403
    finally:
        client.__exit__(None, None, None)


def test_loopback_and_configured_hosts_still_work(
    workspace: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _app_client(workspace, tmp_path, monkeypatch, _valid_manifest())
    try:
        for host in ("127.0.0.1:3210", "localhost:3210", "[::1]:3210", "192.168.1.120:3210"):
            response = client.get("/api/v1/health/live", headers={"Host": host})
            assert response.status_code == 200, (host, response.text)
    finally:
        client.__exit__(None, None, None)


def test_host_authority_parsing_refuses_unusable_values() -> None:
    class _Request:
        def __init__(self, value: str | None) -> None:
            self.headers = {} if value is None else {"host": value}

    assert _request_host_authority(_Request("Example.COM:8080")) == "example.com"
    assert _request_host_authority(_Request("[2001:db8::1]:3210")) == "2001:db8::1"
    assert _request_host_authority(_Request(None)) == ""
    assert _request_host_authority(_Request("  ")) == ""
    assert _request_host_authority(_Request("a.example, b.example")) == ""
    assert _request_host_authority(_Request("bad host")) == ""


def test_explicit_trusted_hosts_list_is_authoritative() -> None:
    # An explicit list drops the built-in IP/loopback rules ...
    assert _host_is_trusted("127.0.0.1", set(), ("studio.local",)) is False
    assert _host_is_trusted("studio.local", set(), ("studio.local",)) is True
    # ... and even the in-process test authority is not exempt.
    assert _host_is_trusted("testserver", set(), ("studio.local",)) is False
    # Without a list, loopback, IP literals and the in-process authority are allowed.
    assert _host_is_trusted("127.0.0.1", set(), None) is True
    assert _host_is_trusted("192.168.1.5", set(), None) is True
    assert _host_is_trusted("testserver", set(), None) is True
    assert _host_is_trusted("evil.example", set(), None) is False



