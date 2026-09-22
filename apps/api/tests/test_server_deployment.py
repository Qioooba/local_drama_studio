"""Server deployment (LAN_SERVICE) contract tests.

``LOCAL_ONLY`` stays the default: every assertion that pins legacy loopback
behaviour doubles as a regression guard for the original single-machine
deployment.  ``LAN_SERVICE`` opts into the relaxed-but-bounded server rules.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from local_drama.api.contract_version import API_CONTRACT_HEADER, API_CONTRACT_VERSION
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.network_policy import is_allowed_runtime_host
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.main import create_app


def _lan_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "network_mode": "LAN_SERVICE",
        "host": "0.0.0.0",
        "trusted_lan_unauthenticated": True,
        "data_root": tmp_path / "data",
        "projects_root": tmp_path / "projects",
        "work_root": tmp_path / "work",
        "cache_root": tmp_path / "cache",
        "logs_root": tmp_path / "logs",
        "backups_root": tmp_path / "backups",
    }
    values.update(overrides)
    return Settings(**values)


# ---------------------------------------------------------------------------
# Settings: bind host rules per network mode
# ---------------------------------------------------------------------------


def test_local_only_settings_reject_non_loopback_bind_host() -> None:
    with pytest.raises(ValidationError, match="literal loopback"):
        Settings(host="0.0.0.0")
    with pytest.raises(ValidationError, match="literal loopback"):
        Settings(host="192.168.1.42")


def test_local_only_settings_accept_literal_loopback_bind_hosts() -> None:
    assert Settings(host="127.0.0.1").host == "127.0.0.1"
    assert Settings(host="LOCALHOST").host == "localhost"
    assert Settings(host="::1").host == "::1"


def test_lan_service_settings_accept_wildcard_and_lan_binds(tmp_path: Path) -> None:
    assert _lan_settings(tmp_path).host == "0.0.0.0"
    assert _lan_settings(tmp_path, host="192.168.1.42").host == "192.168.1.42"
    assert _lan_settings(tmp_path, host="127.0.0.1").is_lan_service is True


def test_lan_service_requires_explicit_trusted_lan_acceptance(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="trusted_lan_unauthenticated=true"):
        _lan_settings(tmp_path, trusted_lan_unauthenticated=False)


def test_lan_service_settings_still_reject_hostnames(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="literal IP"):
        _lan_settings(tmp_path, host="drama.internal.example")


def test_network_mode_rejects_unknown_values() -> None:
    with pytest.raises(ValidationError, match="LOCAL_ONLY or LAN_SERVICE"):
        Settings(network_mode="PUBLIC")


def test_upload_limits_are_configurable(tmp_path: Path) -> None:
    settings = _lan_settings(tmp_path, upload_max_video_mb=512)
    assert settings.upload_max_video_mb == 512
    assert settings.upload_max_image_mb == 25


def test_from_env_reads_network_mode_host_roots_and_limits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCAL_DRAMA_NETWORK_MODE", "LAN_SERVICE")
    monkeypatch.setenv("LOCAL_DRAMA_HOST", "0.0.0.0")
    monkeypatch.setenv("LOCAL_DRAMA_DATA_ROOT", str(tmp_path / "srv-data"))
    monkeypatch.setenv("LOCAL_DRAMA_PROJECTS_ROOT", str(tmp_path / "srv-projects"))
    monkeypatch.setenv("LOCAL_DRAMA_UPLOAD_MAX_VIDEO_MB", "2048")
    monkeypatch.setenv("LOCAL_DRAMA_TOOL_FALLBACK_DIRS", f"{tmp_path}\\tools;{tmp_path}\\tools2")

    settings = Settings.from_env()
    assert settings.network_mode == "LAN_SERVICE"
    assert settings.host == "0.0.0.0"
    assert settings.data_root == tmp_path / "srv-data"
    assert settings.projects_root == tmp_path / "srv-projects"
    assert settings.work_root.name == "work"  # untouched default survives
    assert settings.upload_max_video_mb == 2048
    assert settings.tool_fallback_dirs == (str(tmp_path / "tools"), str(tmp_path / "tools2"))


def test_relative_environment_roots_are_anchored_to_instance_not_process_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    unrelated_cwd = tmp_path / "launch-directory"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)
    monkeypatch.setenv("LOCAL_DRAMA_INSTANCE_ROOT", str(instance))
    monkeypatch.setenv("LOCAL_DRAMA_DATA_ROOT", "mutable-data")
    monkeypatch.setenv("LOCAL_DRAMA_MODEL_LIBRARY_ROOTS", "models;shared/models")
    settings = Settings.from_env()
    assert settings.data_root == (instance / "mutable-data").resolve()
    assert settings.model_library_roots == (
        (instance / "models").resolve(),
        (instance / "shared" / "models").resolve(),
    )


def test_remote_browser_cannot_submit_arbitrary_server_paths(tmp_path: Path) -> None:
    settings = _lan_settings(tmp_path)
    source = tmp_path / "private.txt"
    source.write_text("server secret", encoding="utf-8")
    origin = "http://10.8.0.20:3210"
    headers = {"Host": "10.8.0.20:3210", "Origin": origin, API_CONTRACT_HEADER: API_CONTRACT_VERSION}
    with TestClient(create_app(settings), client=("10.8.0.99", 50000)) as client:
        token = client.get("/api/v1/session/bootstrap", headers=headers).json()["token"]
        rejected = client.post(
            "/api/v1/media:import",
            headers={**headers, "X-Local-Instance-Token": token},
            json={"project_id": "missing", "source_path": str(source), "media_kind": "OTHER"},
        )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "SERVER_PATH_REMOTE_CLIENT"


def test_lan_model_registration_requires_configured_library_and_member_file(tmp_path: Path) -> None:
    model_root = tmp_path / "models"
    model_root.mkdir()
    allowed = model_root / "allowed.safetensors"
    outside = tmp_path / "outside.safetensors"
    allowed.write_bytes(b"model")
    outside.write_bytes(b"model")
    settings = _lan_settings(tmp_path, model_library_roots=(model_root,))
    origin = "http://10.8.0.20:3210"
    headers = {"Host": "10.8.0.20:3210", "Origin": origin, API_CONTRACT_HEADER: API_CONTRACT_VERSION}
    with TestClient(create_app(settings), client=("10.8.0.99", 50000)) as client:
        token = client.get("/api/v1/session/bootstrap", headers=headers).json()["token"]
        rejected = client.post(
            "/api/v1/model-registry/artifacts",
            headers={**headers, "X-Local-Instance-Token": token},
            json={"code": "outside", "kind": "T2V", "machine_path_ref": str(outside)},
        )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "MODEL_LIBRARY_FILE_NOT_ALLOWED"


def test_tool_fallback_dirs_resolve_executables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "ffmpeg.exe").write_bytes(b"stub")
    monkeypatch.delenv("LOCAL_DRAMA_FFMPEG", raising=False)
    monkeypatch.setattr("local_drama.config.shutil.which", lambda _name: None)
    settings = Settings(data_root=tmp_path, tool_fallback_dirs=(str(tools_dir),))
    resolved = settings.ffmpeg_path
    assert resolved is not None and Path(resolved).name.casefold() == "ffmpeg.exe"


# ---------------------------------------------------------------------------
# Runtime endpoint boundary: loopback vs private network
# ---------------------------------------------------------------------------


def test_runtime_host_guard_allows_loopback_always() -> None:
    assert is_allowed_runtime_host("127.0.0.1", allow_private_network=False)
    assert is_allowed_runtime_host("localhost", allow_private_network=False)
    assert is_allowed_runtime_host("::1", allow_private_network=True)


@pytest.mark.parametrize("hostname", ["192.168.1.101", "10.0.0.8", "172.16.4.4"])
def test_runtime_host_guard_allows_private_addresses_only_in_lan(hostname: str) -> None:
    assert is_allowed_runtime_host(hostname, allow_private_network=True)
    assert not is_allowed_runtime_host(hostname, allow_private_network=False)


@pytest.mark.parametrize("hostname", ["8.8.8.8", "203.0.113.5", "0.0.0.0", "example.com"])
def test_runtime_host_guard_never_allows_public_or_unspecified(hostname: str) -> None:
    assert not is_allowed_runtime_host(hostname, allow_private_network=True)


def test_comfy_client_keeps_loopback_rule_in_local_only() -> None:
    with pytest.raises(DomainRuleError, match="endpoint"):
        ComfyClient("http://192.168.1.101:8188")


def test_comfy_client_accepts_private_comfy_in_lan_service() -> None:
    client = ComfyClient("http://192.168.1.101:8188", allow_private_network=True)
    assert client.base_url == "http://192.168.1.101:8188"


# ---------------------------------------------------------------------------
# HTTP boundary: origin checks per network mode
# ---------------------------------------------------------------------------


def test_untrusted_origin_is_rejected_for_writes(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/system/contract", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"]["code"] == "ORIGIN_NOT_ALLOWED"
    assert "evil.example" not in response.text


def test_loopback_dev_origin_remains_writable_when_frontend_port_drifts(tmp_path: Path) -> None:
    with TestClient(create_app(_lan_settings(tmp_path))) as client:
        bootstrap = client.get("/api/v1/session/bootstrap", headers={"Origin": "http://127.0.0.1:5175"})
        accepted = client.post(
            "/api/v1/system/contract",
            headers={"Origin": "http://127.0.0.1:5175", "X-Local-Instance-Token": bootstrap.json()["token"]},
        )
    # The route is GET-only; reaching routing after middleware proves the
    # valid token crossed the CSRF boundary without mutating any state.
    assert accepted.status_code == 405


def test_lan_service_same_origin_write_is_accepted(tmp_path: Path) -> None:
    with TestClient(create_app(_lan_settings(tmp_path))) as client:
        origin = "http://10.8.0.20:3210"
        bootstrap = client.get("/api/v1/session/bootstrap", headers={"Host": "10.8.0.20:3210", "Origin": origin})
        accepted = client.post(
            "/api/v1/system/contract",
            headers={
                "Host": "10.8.0.20:3210",
                "Origin": origin,
                "X-Local-Instance-Token": bootstrap.json()["token"],
            },
        )
    assert accepted.status_code == 405


def test_lan_service_cross_host_origin_is_still_rejected(tmp_path: Path) -> None:
    with TestClient(create_app(_lan_settings(tmp_path))) as client:
        rejected = client.post(
            "/api/v1/system/contract",
            headers={"Host": "10.8.0.20:3210", "Origin": "http://10.8.0.99:3210"},
        )
        port_mismatch = client.post(
            "/api/v1/system/contract",
            headers={"Host": "10.8.0.20:3210", "Origin": "http://10.8.0.20:5173"},
        )
        lookalike = client.post(
            "/api/v1/system/contract",
            headers={"Host": "10.8.0.20:3210", "Origin": "http://10.8.0.20.evil.example:3210"},
        )
    assert rejected.status_code == 403
    assert port_mismatch.status_code == 403
    assert lookalike.status_code == 403


def test_lan_dns_rebinding_host_is_rejected_without_explicit_registration(tmp_path: Path) -> None:
    """R-01: a matching Origin/Host pair alone must not grant write trust.

    The reported risk was that the same-origin fallback only compared ``Origin``
    with the client's own ``Host``.  A hostile domain resolving to this LAN server
    satisfies that comparison by itself, so an unconfigured domain-name Host must
    not be trusted even when the token was fetched over GET.
    """
    settings = _lan_settings(tmp_path)
    attacker_host = "rebind.attacker.example:3210"
    with TestClient(create_app(settings), client=("10.8.0.99", 50000)) as client:
        token = client.get(
            "/api/v1/session/bootstrap",
            headers={"Host": attacker_host, "Origin": f"http://{attacker_host}"},
        ).json()["token"]
        rejected = client.post(
            "/api/v1/system/contract",
            headers={
                "Host": attacker_host,
                "Origin": f"http://{attacker_host}",
                "X-Local-Instance-Token": token,
            },
        )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"


def test_lan_ip_literal_host_keeps_working_and_registered_host_is_trusted(tmp_path: Path) -> None:
    """The rebinding fix must not break legitimate LAN access.

    An IP-literal Host (the documented LAN workflow, reachable without
    pre-registration) and any explicitly configured host stay trusted.
    """
    registered = "http://drama.internal.example:3210"
    settings = _lan_settings(tmp_path, allowed_origins=(registered,))
    with TestClient(create_app(settings), client=("10.8.0.99", 50000)) as client:
        literal = client.get("/api/v1/session/bootstrap", headers={"Host": "10.8.0.20:3210"})
        accepted_literal = client.post(
            "/api/v1/system/contract",
            headers={
                "Host": "10.8.0.20:3210",
                "Origin": "http://10.8.0.20:3210",
                "X-Local-Instance-Token": literal.json()["token"],
            },
        )
        bootstrap = client.get(
            "/api/v1/session/bootstrap",
            headers={"Host": "drama.internal.example:3210"},
        )
        accepted_registered = client.post(
            "/api/v1/system/contract",
            headers={
                "Host": "drama.internal.example:3210",
                "Origin": registered,
                "X-Local-Instance-Token": bootstrap.json()["token"],
            },
        )
    # 405 proves the request passed the origin/token boundary and reached routing.
    assert accepted_literal.status_code == 405
    assert accepted_registered.status_code == 405


def test_explicit_trusted_hosts_override_replaces_origin_derived_hosts(tmp_path: Path) -> None:
    """An explicit ``trusted_hosts`` list is authoritative, including empty.

    ``allowed_origins`` is cleared here so the only possible grant would come from
    the trusted-host rule, which proves ``trusted_hosts=()`` really denies rather
    than silently falling back to the origin-derived hosts.
    """
    settings = _lan_settings(tmp_path, allowed_origins=(), trusted_hosts=())
    with TestClient(create_app(settings), client=("10.8.0.99", 50000)) as client:
        token = client.get("/api/v1/session/bootstrap", headers={"Host": "localhost:3210"}).json()["token"]
        rejected = client.post(
            "/api/v1/system/contract",
            headers={
                "Host": "localhost:3210",
                "Origin": "http://localhost:3210",
                "X-Local-Instance-Token": token,
            },
        )
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"


def test_lan_service_emits_cors_headers_for_registered_origins(tmp_path: Path) -> None:
    settings = _lan_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        preflight = client.options(
            "/api/v1/health/live",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-local-instance-token",
            },
        )
        actual = client.get("/api/v1/health/live", headers={"Origin": "http://127.0.0.1:5173"})
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert "x-local-instance-token" in preflight.headers["access-control-allow-headers"].casefold()
    assert actual.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_local_only_never_emits_cors_headers(tmp_path: Path) -> None:
    settings = Settings(
        data_root=tmp_path / "data",
        projects_root=tmp_path / "projects",
        work_root=tmp_path / "work",
        cache_root=tmp_path / "cache",
        logs_root=tmp_path / "logs",
        backups_root=tmp_path / "backups",
    )
    with TestClient(create_app(settings)) as client:
        actual = client.get("/api/v1/health/live", headers={"Origin": "http://127.0.0.1:5173"})
    assert "access-control-allow-origin" not in actual.headers


# ---------------------------------------------------------------------------
# Static SPA hosting
# ---------------------------------------------------------------------------


def _write_dist(dist: Path) -> None:
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><html><body>studio</body></html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")


def test_frontend_dist_is_served_with_spa_fallback(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _write_dist(dist)
    settings = _lan_settings(tmp_path, frontend_dist_root=dist)
    with TestClient(create_app(settings)) as client:
        index = client.get("/")
        asset = client.get("/assets/app.js")
        route = client.get("/projects/p-1/delivery")
        missing_api = client.get("/api/v1/definitely-not-a-route")
    assert index.status_code == 200 and "studio" in index.text
    assert asset.status_code == 200 and asset.text == "console.log(1)"
    assert route.status_code == 200 and "studio" in route.text
    # Unknown API paths must stay JSON errors, never the SPA shell.
    assert missing_api.status_code == 404
    assert missing_api.json()["error"]["code"] == "NOT_FOUND"


def test_missing_frontend_dist_disables_static_hosting(tmp_path: Path) -> None:
    settings = _lan_settings(tmp_path, frontend_dist_root=tmp_path / "nope")
    with pytest.raises(ValueError, match="index.html"):
        create_app(settings)


def test_work_root_derives_comfy_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCAL_DRAMA_NETWORK_MODE", "LAN_SERVICE")
    monkeypatch.setenv("LOCAL_DRAMA_HOST", "0.0.0.0")
    monkeypatch.setenv("LOCAL_DRAMA_WORK_ROOT", str(tmp_path / "runtime-work"))
    settings = Settings.from_env()
    assert settings.comfy_input_root == tmp_path / "runtime-work" / "comfy-production" / "input"
    assert settings.comfy_output_root == tmp_path / "runtime-work" / "comfy-production" / "output"


def test_from_env_lan_settings_are_hermetic_and_platform_path_safe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``Settings.from_env`` LAN reads must not depend on the developer machine.

    The two historical failures this covers set only the LAN mode/host/roots. That
    is not a complete LAN declaration: the supported contract requires the explicit
    ``network.trusted_lan_unauthenticated`` acceptance, which today only the machine
    JSON provides. Without an explicit temporary instance root and config the test
    silently read whatever ``config.json`` happened to exist on the machine, and one
    of them hardcoded Windows backslashes for the tool fallback directories.
    """
    instance = tmp_path / "instance"
    config_dir = instance / "config"
    config_dir.mkdir(parents=True)
    work_root = tmp_path / "runtime-work"
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "instance_id": "ci-lan",
                "network": {
                    "mode": "LAN_SERVICE",
                    "host": "0.0.0.0",
                    "trusted_lan_unauthenticated": True,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCAL_DRAMA_INSTANCE_ROOT", str(instance))
    monkeypatch.setenv("LOCAL_DRAMA_CONFIG", str(config_dir / "config.json"))
    monkeypatch.setenv("LOCAL_DRAMA_NETWORK_MODE", "LAN_SERVICE")
    monkeypatch.setenv("LOCAL_DRAMA_HOST", "0.0.0.0")
    monkeypatch.setenv("LOCAL_DRAMA_DATA_ROOT", str(tmp_path / "srv-data"))
    monkeypatch.setenv("LOCAL_DRAMA_PROJECTS_ROOT", str(tmp_path / "srv-projects"))
    monkeypatch.setenv("LOCAL_DRAMA_WORK_ROOT", str(work_root))
    monkeypatch.setenv("LOCAL_DRAMA_UPLOAD_MAX_VIDEO_MB", "2048")
    # Build the search path with the current platform's separator instead of
    # hardcoded Windows backslashes, so the assertion is valid on every target.
    tools = (tmp_path / "tools", tmp_path / "tools2")
    monkeypatch.setenv("LOCAL_DRAMA_TOOL_FALLBACK_DIRS", os.pathsep.join(str(item) for item in tools))

    settings = Settings.from_env()
    assert settings.network_mode == "LAN_SERVICE"
    assert settings.host == "0.0.0.0"
    assert settings.trusted_lan_unauthenticated is True
    assert settings.instance_root == instance
    assert settings.data_root == tmp_path / "srv-data"
    assert settings.projects_root == tmp_path / "srv-projects"
    assert settings.work_root == work_root
    assert settings.upload_max_video_mb == 2048
    assert settings.tool_fallback_dirs == (str(tools[0]), str(tools[1]))
    # Directory derivation is a pure function of work_root and must agree with the
    # value the historical failure asserted.
    assert settings.comfy_input_root == work_root / "comfy-production" / "input"
    assert settings.comfy_output_root == work_root / "comfy-production" / "output"


def test_from_env_lan_without_explicit_acceptance_is_rejected_hermetically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The LAN guard must still refuse a LAN mode with no explicit acceptance.

    This is the contract the historical failures tripped over; it must keep
    failing closed, and it must do so without reading a machine config.
    """
    instance = tmp_path / "instance"
    config_dir = instance / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "instance_id": "ci-lan-untrusted",
                "network": {"mode": "LAN_SERVICE", "host": "0.0.0.0"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCAL_DRAMA_INSTANCE_ROOT", str(instance))
    monkeypatch.setenv("LOCAL_DRAMA_CONFIG", str(config_dir / "config.json"))
    monkeypatch.setenv("LOCAL_DRAMA_NETWORK_MODE", "LAN_SERVICE")
    monkeypatch.setenv("LOCAL_DRAMA_HOST", "0.0.0.0")
    with pytest.raises(ValidationError, match="trusted_lan_unauthenticated=true"):
        Settings.from_env()
    # There is deliberately no supported environment-variable mapping for the
    # acceptance flag; setting one must not silently turn the guard off.
    monkeypatch.setenv("LOCAL_DRAMA_TRUSTED_LAN_UNAUTHENTICATED", "true")
    with pytest.raises(ValidationError, match="trusted_lan_unauthenticated=true"):
        Settings.from_env()


def test_upload_limits_reject_non_positive_values(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _lan_settings(tmp_path, upload_max_video_mb=0)


def test_spa_fallback_blocks_path_traversal(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _write_dist(dist)
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    settings = _lan_settings(tmp_path, frontend_dist_root=dist)
    with TestClient(create_app(settings)) as client:
        traversal = client.get("/..%2F..%2Fsecret.txt")
    assert traversal.status_code in {200, 404}
    if traversal.status_code == 200:
        assert "top secret" not in traversal.text
