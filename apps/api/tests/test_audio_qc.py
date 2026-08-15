from __future__ import annotations

import math
import struct
import wave

import pytest

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.reviews import ReviewService
from local_drama.domain.errors import DomainRuleError


def _wav(path, amplitude: float) -> None:
    sample_rate = 48_000
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames = [struct.pack("<h", round(32767 * amplitude * math.sin(2 * math.pi * 440 * index / sample_rate))) for index in range(sample_rate)]
        output.writeframes(b"".join(frames))


def _audio(workspace, database, amplitude: float):
    project = ProjectService(database, workspace.projects_root).create_project(code=f"audio_qc_{str(amplitude).replace('.', '_')}", title="Audio QC", episode_count=1, aspect_ratio="16:9", fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True)
    source = workspace.work_root / f"tone-{amplitude}.wav"
    _wav(source, amplitude)
    imported = MediaService(database, workspace).import_file(str(project["id"]), source, media_kind="AUDIO")
    return str(imported["media_version_id"])


def test_audio_qc_records_real_lufs_true_peak_peak_and_clipping(workspace, database) -> None:
    media_version_id = _audio(workspace, database, 0.2)
    service = ReviewService(database, workspace)
    run = service.machine_check(media_version_id)
    results = {item["item_id"]: item for item in run["results"]}
    assert run["policy_version"] == "g8_audio_qc_v1"
    assert run["status"] == "PASS"
    assert -30 <= results["integrated_loudness"]["details"]["value_lufs"] <= -14
    assert results["true_peak"]["details"]["value_dbfs"] <= -1
    assert results["peak"]["details"]["value_dbfs"] < -0.1
    assert results["clipping"]["details"]["detected"] is False
    waveform, mime = MediaService(database, workspace).waveform(media_version_id)
    assert waveform.is_file() and mime == "image/png"


def test_audio_approval_requires_latest_audio_qc_pass(workspace, database) -> None:
    media_version_id = _audio(workspace, database, 0.2)
    service = ReviewService(database, workspace)
    service.ensure_templates()
    context = service.review_context(media_version_id)
    checks = [{"item_id": item["id"], "result": "PASS"} for item in context["template"]["items"]]
    with pytest.raises(DomainRuleError) as blocked:
        service.submit_review(media_version_id, context["template"]["id"], "APPROVED", context["subject_revision"], checks)
    assert blocked.value.code == "AUDIO_QC_REQUIRED"
    assert service.machine_check(media_version_id)["status"] == "PASS"
    approved = service.submit_review(media_version_id, context["template"]["id"], "APPROVED", context["subject_revision"], checks)
    assert approved["decision"] == "APPROVED"


def test_clipping_audio_fails_machine_qc_and_remains_unapprovable(workspace, database) -> None:
    media_version_id = _audio(workspace, database, 1.0)
    service = ReviewService(database, workspace)
    service.ensure_templates()
    run = service.machine_check(media_version_id)
    assert run["status"] == "FAIL"
    results = {item["item_id"]: item for item in run["results"]}
    assert results["clipping"]["result"] == "FAIL"
    context = service.review_context(media_version_id)
    checks = [{"item_id": item["id"], "result": "PASS"} for item in context["template"]["items"]]
    with pytest.raises(DomainRuleError) as blocked:
        service.submit_review(media_version_id, context["template"]["id"], "APPROVED", context["subject_revision"], checks)
    assert blocked.value.code == "AUDIO_QC_REQUIRED"
