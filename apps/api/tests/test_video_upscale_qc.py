from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_drama.application.video_upscale.plans import _border_evidence, _cfr_timestamp_evidence
from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.ncnn_video_upscale_execution import NcnnVideoUpscaleExecutor


def test_cfr_timestamp_evidence_checks_actual_frame_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "0.000000\n0.041667\n0.083333\n", ""),
    )
    evidence = _cfr_timestamp_evidence(Path("ffprobe"), Path("source.mp4"), "24/1")
    assert evidence["constant"] is True
    assert evidence["irregular_step_count"] == 0

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "0.000000\n0.041667\n0.100000\n", ""),
    )
    evidence = _cfr_timestamp_evidence(Path("ffprobe"), Path("source.mp4"), "24/1")
    assert evidence["constant"] is False
    assert evidence["irregular_step_count"] == 1


def test_border_detection_warns_only_for_stable_large_borders(monkeypatch: pytest.MonkeyPatch) -> None:
    stderr = "\n".join(["crop=640:320:0:80"] * 8 + ["crop=640:312:0:84"])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", stderr),
    )
    evidence = _border_evidence(Path("ffmpeg"), Path("source.mp4"), 640, 480)
    assert evidence["status"] == "DETECTED"
    assert evidence["large_borders"] is True
    assert evidence["auto_crop_applied"] is False


def test_output_qc_requires_frozen_geometry_fps_duration_and_audio() -> None:
    source = {
        "streams": [
            {"codec_type": "video", "avg_frame_rate": "24000/1001"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "1.001"},
    }
    output = {
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "24000/1001",
                "nb_read_frames": "24",
                "pix_fmt": "yuv420p",
                "field_order": "progressive",
                "sample_aspect_ratio": "1:1",
                "color_range": "tv",
                "color_space": "bt709",
                "color_transfer": "bt709",
                "color_primaries": "bt709",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "1.001"},
    }
    checks = NcnnVideoUpscaleExecutor._validate_output_qc(
        source_probe=source,
        output_probe=output,
        expected_frames=24,
        target_width=1920,
        target_height=1080,
        expected_duration_ms=None,
    )
    assert {item[0] for item in checks} >= {
        "dimensions",
        "frame_count",
        "frame_rate",
        "duration",
        "sample_aspect_ratio",
        "sdr_color",
        "rotation",
        "audio_tracks",
    }

    output["streams"][0]["avg_frame_rate"] = "25/1"
    with pytest.raises(DomainRuleError) as error:
        NcnnVideoUpscaleExecutor._validate_output_qc(
            source_probe=source,
            output_probe=output,
            expected_frames=24,
            target_width=1920,
            target_height=1080,
            expected_duration_ms=None,
        )
    assert error.value.code == "UPSCALE_OUTPUT_QC_FAILED"
