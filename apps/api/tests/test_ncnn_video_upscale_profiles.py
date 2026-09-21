from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app
from local_drama.model_platform.application.capability_resolution import CapabilityScopeContext
from local_drama.model_platform.application.execution_planning import ExecutionPlanningService, ExecutionPreviewRequest
from local_drama.model_platform.application.ncnn_video_upscale_execution import NcnnVideoUpscaleExecutor
from local_drama.model_platform.application.ncnn_video_upscale_profiles import NcnnVideoUpscaleProfileService


def _configured_workspace(workspace, tmp_path: Path):
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"ffmpeg")
    ffprobe.write_bytes(b"ffprobe")
    return workspace.model_copy(update={"ffmpeg_override": ffmpeg, "ffprobe_override": ffprobe})


def _local_ncnn_files(tmp_path: Path) -> tuple[Path, Path]:
    executable = tmp_path / "realesrgan-ncnn-vulkan.exe"
    executable.write_bytes(b"ncnn-runtime-v1")
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "realesr-animevideov3.param").write_bytes(b"param-v1")
    (model_dir / "realesr-animevideov3.bin").write_bytes(b"weights-v1")
    return executable, model_dir


def _passed_smoke(**options):
    return {
        "schema_version": "localdrama.ncnn-video-upscale-profile-smoke.v1",
        "status": "PASS",
        "adapter_code": "ncnn.realesrgan.video.v1",
        "model_name": options["model_name"],
        "verified_native_scales": list(options["scales"]),
        "network_used": False,
    }


def test_one_click_ncnn_smoke_publishes_exact_immutable_profile(database, workspace, tmp_path: Path) -> None:
    settings = _configured_workspace(workspace, tmp_path)
    executable, model_dir = _local_ncnn_files(tmp_path)
    service = NcnnVideoUpscaleProfileService(database, settings, smoke_runner=_passed_smoke)

    first = service.configure_and_publish(
        executable_path=str(executable),
        model_directory=str(model_dir),
        model_name="realesr-animevideov3",
        gpu_device=0,
        tile_size=128,
        load_threads=1,
        proc_threads=1,
        save_threads=2,
        actor="test",
    )
    assert first.profile_status == "PUBLISHED"
    assert first.verified_native_scales == (2, 3, 4)
    assert first.reused_profile is False

    with database.connect() as connection:
        profile = connection.execute(
            """SELECT version.payload_json,publication.status,runtime.status AS runtime_status,
                      installation.install_state,offering.validation_status
               FROM mp_execution_profile_versions version
               JOIN mp_profile_publications publication ON publication.execution_profile_version_id=version.id
               JOIN mp_runtime_installation_versions runtime ON runtime.id=version.runtime_installation_version_id
               JOIN mp_runtime_model_installations installation ON installation.runtime_installation_version_id=runtime.id
               JOIN mp_capability_offerings offering ON offering.runtime_model_installation_id=installation.id
               WHERE version.id=?""",
            (first.profile_version_id,),
        ).fetchone()
        payload = json.loads(profile["payload_json"])
        assert payload["template"] == "ncnn.video-upscale.profile.v1"
        assert payload["model_name"] == "realesr-animevideov3"
        assert payload["verified_native_scales"] == [2, 3, 4]
        assert (profile["status"], profile["runtime_status"], profile["install_state"], profile["validation_status"]) == (
            "PUBLISHED", "ACTIVE", "READY", "SMOKE_PASSED",
        )
        evidence = connection.execute(
            "SELECT payload_json FROM mp_validation_evidence WHERE validation_run_id=?",
            (first.validation_run_id,),
        ).fetchone()
        assert json.loads(evidence["payload_json"])["network_used"] is False

    preview = ExecutionPlanningService(database).preview(
        ExecutionPreviewRequest(
            capability_code="UPSCALE_VIDEO",
            scope=CapabilityScopeContext(),
            semantic_inputs={"video_upscale_purpose": "PREVIEW"},
            run_overrides={"tile_size": 256, "tta": False, "load_threads": 1, "proc_threads": 1, "save_threads": 2},
            execution_profile_version_id=first.profile_version_id,
        )
    )
    assert preview.executable is True
    assert preview.adapter_code == "ncnn.realesrgan.video.v1"
    assert preview.resolved_parameters["tile_size"].value == 256

    replay = service.configure_and_publish(
        executable_path=str(executable),
        model_directory=str(model_dir),
        model_name="realesr-animevideov3",
        gpu_device=0,
        tile_size=128,
        load_threads=1,
        proc_threads=1,
        save_threads=2,
        actor="test",
    )
    assert replay.profile_version_id == first.profile_version_id
    assert replay.reused_profile is True

    with database.connect() as connection:
        runtime = connection.execute(
            """SELECT runtime.configuration_json FROM mp_execution_profile_versions profile
               JOIN mp_runtime_installation_versions runtime ON runtime.id=profile.runtime_installation_version_id
               WHERE profile.id=?""",
            (first.profile_version_id,),
        ).fetchone()
    frozen_runtime = json.loads(runtime["configuration_json"])
    executor = NcnnVideoUpscaleExecutor(database, settings)
    executor._verify_frozen_model_files(frozen_runtime, model_dir)
    (model_dir / "realesr-animevideov3.bin").write_bytes(b"weights-changed")
    with pytest.raises(DomainRuleError) as changed:
        executor._verify_frozen_model_files(frozen_runtime, model_dir)
    assert changed.value.code == "UPSCALE_MODEL_ARTIFACT_CHANGED"


def test_ncnn_profile_is_not_registered_when_real_smoke_fails(database, workspace, tmp_path: Path) -> None:
    settings = _configured_workspace(workspace, tmp_path)
    executable, model_dir = _local_ncnn_files(tmp_path)

    def failed_smoke(**_options):
        return {"status": "FAIL", "network_used": False}

    service = NcnnVideoUpscaleProfileService(database, settings, smoke_runner=failed_smoke)
    with pytest.raises(DomainRuleError) as raised:
        service.configure_and_publish(
            executable_path=str(executable), model_directory=str(model_dir), model_name="realesr-animevideov3",
            gpu_device=0, tile_size=0, load_threads=1, proc_threads=1, save_threads=2, actor="test",
        )
    assert raised.value.code == "UPSCALE_NCNN_SMOKE_FAILED"
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM mp_runtime_installations WHERE code='ncnn.realesrgan.video'").fetchone()[0] == 0


def test_ncnn_profile_rejects_arbitrary_executable_name(database, workspace, tmp_path: Path) -> None:
    settings = _configured_workspace(workspace, tmp_path)
    executable, model_dir = _local_ncnn_files(tmp_path)
    renamed = executable.with_name("arbitrary.exe")
    executable.rename(renamed)
    service = NcnnVideoUpscaleProfileService(database, settings, smoke_runner=_passed_smoke)
    with pytest.raises(DomainRuleError) as raised:
        service.configure_and_publish(
            executable_path=str(renamed), model_directory=str(model_dir), model_name="realesr-animevideov3",
            gpu_device=0, tile_size=0, load_threads=1, proc_threads=1, save_threads=2, actor="test",
        )
    assert raised.value.code == "UPSCALE_NCNN_EXECUTABLE_INVALID"


def test_ncnn_profile_rejects_unc_paths_before_filesystem_access(database, workspace, tmp_path: Path) -> None:
    settings = _configured_workspace(workspace, tmp_path)
    service = NcnnVideoUpscaleProfileService(database, settings, smoke_runner=_passed_smoke)

    with pytest.raises(DomainRuleError) as executable:
        service._resolve_executable(r"\\server\share\realesrgan-ncnn-vulkan.exe")
    assert executable.value.code == "UPSCALE_NCNN_REMOTE_PATH_FORBIDDEN"

    with pytest.raises(DomainRuleError) as model:
        service._resolve_model_directory(r"\\server\share\models")
    assert model.value.code == "UPSCALE_NCNN_REMOTE_PATH_FORBIDDEN"


def test_ncnn_one_click_http_contract_publishes_only_after_confirmation(
    database, workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _configured_workspace(workspace, tmp_path)
    executable, model_dir = _local_ncnn_files(tmp_path)
    monkeypatch.setattr(NcnnVideoUpscaleProfileService, "_run_real_smoke", lambda self, **options: _passed_smoke(**options))
    payload = {
        "executable_path": str(executable),
        "model_directory": str(model_dir),
        "model_name": "realesr-animevideov3",
        "gpu_device": 0,
        "tile_size": 0,
        "load_threads": 1,
        "proc_threads": 1,
        "save_threads": 2,
        "actor": "test",
    }
    with TestClient(create_app(settings)) as client:
        unconfirmed = client.post("/api/v2/model-platform/ncnn-video-upscale:configure-and-publish", json=payload)
        assert unconfirmed.status_code == 422
        published = client.post(
            "/api/v2/model-platform/ncnn-video-upscale:configure-and-publish",
            json={**payload, "confirm_publish": True},
        )
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["network_used"] is False
    assert body["real_smoke_required"] is True
    assert body["profile"]["status"] == "PUBLISHED"
    assert body["profile"]["verified_native_scales"] == [2, 3, 4]
    assert "executable_path" not in json.dumps(body)
