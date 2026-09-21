from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from collections import namedtuple
from pathlib import Path

import pytest

from local_drama.application.media import MediaService
from local_drama.domain.errors import DomainRuleError
from local_drama.model_platform.application.ncnn_video_upscale_execution import NcnnVideoUpscaleExecutor


def _pid_is_alive(pid: int) -> bool:
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_executor_terminates_a_running_child_when_cancel_is_requested(database, workspace) -> None:
    checks = 0

    def cancel() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 1

    executor = NcnnVideoUpscaleExecutor(database, workspace, cancel_check=cancel)
    with pytest.raises(DomainRuleError) as raised:
        executor._run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=10)

    assert raised.value.code == "JOB_CANCELLED"
    assert checks >= 1


def test_executor_terminates_descendants_of_owned_wrapper_on_cancel(
    database,
    workspace,
    tmp_path: Path,
) -> None:
    child_pid_file = tmp_path / "grandchild.pid"
    wrapper = (
        "import pathlib,subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid),encoding='utf-8');"
        "time.sleep(60)"
    )
    executor = NcnnVideoUpscaleExecutor(
        database,
        workspace,
        cancel_check=child_pid_file.is_file,
    )

    with pytest.raises(DomainRuleError) as raised:
        executor._run([sys.executable, "-c", wrapper, str(child_pid_file)], timeout=10)

    assert raised.value.code == "JOB_CANCELLED"
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    for _ in range(40):
        if not _pid_is_alive(child_pid):
            break
        time.sleep(0.05)
    assert not _pid_is_alive(child_pid), "executor cancellation left an owned descendant running"


def test_executor_redacts_absolute_paths_from_process_errors(database, workspace, tmp_path: Path) -> None:
    private_path = (tmp_path / "private-project" / "source.mp4").resolve()
    executor = NcnnVideoUpscaleExecutor(database, workspace)

    with pytest.raises(DomainRuleError) as raised:
        executor._run(
            [
                sys.executable,
                "-c",
                "import sys; print(sys.argv[1], file=sys.stderr); raise SystemExit(7)",
                str(private_path),
            ],
            timeout=10,
        )

    assert raised.value.code == "UPSCALE_PROCESS_FAILED"
    detail = str(raised.value.details["stderr_redacted"])
    assert str(private_path) not in detail
    assert private_path.as_posix() not in detail
    assert "<local-path>" in detail


def test_executor_freezes_audio_copy_transcode_and_no_audio_facts(database, workspace) -> None:
    copy = NcnnVideoUpscaleExecutor._audio_processing(
        {"probe": {"streams": [{"codec_type": "audio", "codec_name": "aac"}, {"codec_type": "audio", "codec_name": "ac3"}]}},
        {"audio_policy": "COPY_IF_COMPATIBLE_ELSE_AAC", "aac_bitrate_kbps": 192},
    )
    transcode = NcnnVideoUpscaleExecutor._audio_processing(
        {"probe": {"streams": [{"codec_type": "audio", "codec_name": "pcm_s16le"}]}},
        {"audio_policy": "COPY_IF_COMPATIBLE_ELSE_AAC", "aac_bitrate_kbps": 256},
    )
    silent = NcnnVideoUpscaleExecutor._audio_processing(
        {"probe": {"streams": [{"codec_type": "video", "codec_name": "h264"}]}},
        {"audio_policy": "COPY_IF_COMPATIBLE_ELSE_AAC", "aac_bitrate_kbps": 192},
    )

    assert copy == {
        "track_count": 2,
        "source_codecs": ["aac", "ac3"],
        "mode": "COPY",
        "aac_bitrate_kbps": None,
        "preserves_all_tracks": True,
        "source_is_final_render_audio": True,
    }
    assert transcode["mode"] == "AAC_TRANSCODE" and transcode["aac_bitrate_kbps"] == 256
    assert silent["mode"] == "NONE" and silent["track_count"] == 0
    with pytest.raises(DomainRuleError) as strict:
        NcnnVideoUpscaleExecutor._audio_processing(
            {"probe": {"streams": [{"codec_type": "audio", "codec_name": "pcm_s16le"}]}},
            {"audio_policy": "COPY_STRICT", "aac_bitrate_kbps": 192},
        )
    assert strict.value.code == "UPSCALE_AUDIO_COPY_UNSUPPORTED"


def test_mux_preserves_all_audio_languages_default_disposition_and_tail(database, workspace, tmp_path: Path) -> None:
    source = tmp_path / "双音轨 source.mp4"
    segment = tmp_path / "upscaled-segment.mp4"
    concat = tmp_path / "segments.txt"
    output = tmp_path / "muxed.mp4"
    subprocess.run(
        [
            workspace.ffmpeg_path,
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=4:duration=1.5",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.5",
            "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=1.5",
            "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-metadata:s:a:0", "language=chi", "-metadata:s:a:1", "language=eng",
            "-disposition:a:0", "default", "-disposition:a:1", "0",
            "-y", str(source),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [workspace.ffmpeg_path, "-i", str(source), "-map", "0:v:0", "-c:v", "copy", "-an", "-y", str(segment)],
        check=True,
        capture_output=True,
    )
    concat.write_text(f"file '{segment.as_posix()}'\n", encoding="utf-8")
    source_probe = {"probe": MediaService(database, workspace).probe_output(source, "VIDEO")}
    executor = NcnnVideoUpscaleExecutor(database, workspace)
    facts = executor._mux(
        concat,
        source,
        output,
        {"audio_policy": "COPY_IF_COMPATIBLE_ELSE_AAC", "aac_bitrate_kbps": 192},
        source_probe,
    )
    output_probe = MediaService(database, workspace).probe_output(output, "VIDEO")
    audio = [stream for stream in output_probe["streams"] if stream["codec_type"] == "audio"]

    assert facts["mode"] == "COPY" and facts["track_count"] == 2
    assert [str((stream.get("tags") or {}).get("language")) for stream in audio] == ["chi", "eng"]
    assert [int((stream.get("disposition") or {}).get("default") or 0) for stream in audio] == [1, 0]
    assert all(int(stream.get("channels") or 0) == 1 for stream in audio)
    assert float(output_probe["format"]["duration"]) >= 1.45

    silent_concat = tmp_path / "silent-segments.txt"
    silent_output = tmp_path / "silent-muxed.mp4"
    silent_concat.write_text(f"file '{segment.as_posix()}'\n", encoding="utf-8")
    silent_probe = {"probe": MediaService(database, workspace).probe_output(segment, "VIDEO")}
    silent_facts = executor._mux(
        silent_concat,
        segment,
        silent_output,
        {"audio_policy": "COPY_IF_COMPATIBLE_ELSE_AAC", "aac_bitrate_kbps": 192},
        silent_probe,
    )
    silent_output_probe = MediaService(database, workspace).probe_output(silent_output, "VIDEO")
    assert silent_facts["mode"] == "NONE"
    assert not [stream for stream in silent_output_probe["streams"] if stream["codec_type"] == "audio"]

    pcm_source = tmp_path / "pcm-source.mkv"
    transcoded_output = tmp_path / "transcoded-audio.mp4"
    subprocess.run(
        [
            workspace.ffmpeg_path,
            "-i", str(segment),
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=1.5",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "pcm_s16le", "-y", str(pcm_source),
        ],
        check=True,
        capture_output=True,
    )
    pcm_probe = {"probe": MediaService(database, workspace).probe_output(pcm_source, "VIDEO")}
    transcode_facts = executor._mux(
        concat,
        pcm_source,
        transcoded_output,
        {"audio_policy": "COPY_IF_COMPATIBLE_ELSE_AAC", "aac_bitrate_kbps": 256},
        pcm_probe,
    )
    transcoded_probe = MediaService(database, workspace).probe_output(transcoded_output, "VIDEO")
    transcoded_audio = [stream for stream in transcoded_probe["streams"] if stream["codec_type"] == "audio"]
    assert transcode_facts["mode"] == "AAC_TRANSCODE" and transcode_facts["aac_bitrate_kbps"] == 256
    assert len(transcoded_audio) == 1 and transcoded_audio[0]["codec_name"] == "aac"


def test_executor_uses_frozen_tile_fallback_only_for_ncnn_oom(
    database,
    workspace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = NcnnVideoUpscaleExecutor(database, workspace)
    input_frames = tmp_path / "input"
    output_frames = tmp_path / "output"
    model_dir = tmp_path / "model"
    input_frames.mkdir()
    output_frames.mkdir()
    model_dir.mkdir()
    observed_tiles: list[int] = []

    def run(args: list[str], **_kwargs: object) -> None:
        tile = int(args[args.index("-t") + 1])
        observed_tiles.append(tile)
        if tile == 0:
            (output_frames / "partial.png").write_bytes(b"partial")
            raise DomainRuleError(
                "UPSCALE_PROCESS_FAILED",
                "failed",
                {"stderr_redacted": "vk_error_out_of_device_memory"},
            )

    monkeypatch.setattr(executor, "_run", run)
    result = executor._run_ncnn(
        Path(sys.executable),
        [],
        model_dir,
        input_frames,
        output_frames,
        {
            "model_name": "realesr-animevideov3",
            "tile_size": 0,
            "tile_fallback_sizes": [256, 128, 64],
            "gpu_device": 0,
            "load_threads": 1,
            "proc_threads": 1,
            "save_threads": 1,
            "tta": False,
        },
        3,
        allow_tile_fallback=True,
    )

    assert observed_tiles == [0, 256]
    assert result["tile_size"] == 256
    assert result["tile_fallback_applied"] is True
    assert result["tile_attempts"] == [0, 256]
    assert not (output_frames / "partial.png").exists()


def test_executor_does_not_tile_fallback_for_non_oom_failure(
    database,
    workspace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = NcnnVideoUpscaleExecutor(database, workspace)
    input_frames = tmp_path / "input"
    output_frames = tmp_path / "output"
    model_dir = tmp_path / "model"
    input_frames.mkdir()
    output_frames.mkdir()
    model_dir.mkdir()
    calls = 0

    def run(_args: list[str], **_kwargs: object) -> None:
        nonlocal calls
        calls += 1
        raise DomainRuleError(
            "UPSCALE_PROCESS_FAILED",
            "failed",
            {"stderr_redacted": "invalid model file"},
        )

    monkeypatch.setattr(executor, "_run", run)
    with pytest.raises(DomainRuleError) as raised:
        executor._run_ncnn(
            Path(sys.executable),
            [],
            model_dir,
            input_frames,
            output_frames,
            {
                "model_name": "realesr-animevideov3",
                "tile_size": 0,
                "tile_fallback_sizes": [256, 128, 64],
                "gpu_device": 0,
                "load_threads": 1,
                "proc_threads": 1,
                "save_threads": 1,
                "tta": False,
            },
            3,
            allow_tile_fallback=True,
        )

    assert raised.value.code == "UPSCALE_PROCESS_FAILED"
    assert calls == 1


def test_executor_rechecks_runtime_free_space_before_writing_chunks(
    database,
    workspace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disk_usage = namedtuple("disk_usage", "total used free")
    monkeypatch.setattr(
        "local_drama.model_platform.application.ncnn_video_upscale_execution.shutil.disk_usage",
        lambda _path: disk_usage(100 * 1024**3, 99 * 1024**3, 1 * 1024**3),
    )
    executor = NcnnVideoUpscaleExecutor(database, workspace)

    with pytest.raises(OSError) as raised:
        executor._assert_runtime_disk_space(
            {
                "project_root": tmp_path,
                "options": {"disk_budget": {"minimum_free_bytes": 5 * 1024**3}},
            }
        )

    assert raised.value.errno == 28


def test_executor_passes_unicode_and_shell_metacharacter_paths_as_literal_argv(
    database,
    workspace,
    tmp_path: Path,
) -> None:
    exact_output = tmp_path / "中文 空格 $() & 引号'" / "result.txt"
    executor = NcnnVideoUpscaleExecutor(database, workspace)
    executor._run(
        [
            sys.executable,
            "-c",
            "import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True); p.write_text('literal',encoding='utf-8')",
            str(exact_output),
        ],
        timeout=10,
    )

    assert exact_output.read_text(encoding="utf-8") == "literal"
