"""Real-ESRGAN NCNN/Vulkan video executor for immutable V2 snapshots."""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import shutil
import signal
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Mapping

from local_drama.application.episode_render_approval import latest_episode_render
from local_drama.application.media import MediaService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.atomic import replace_path, write_atomic
from local_drama.infrastructure.filesystem.path_policy import controlled_path
from local_drama.model_platform.application.execution_job_links import WorkerExecutionSnapshot

_HANDLER_CODE = "ncnn.realesrgan.video"
_HANDLER_VERSION = "v1"
_ADAPTER_CODE = "ncnn.realesrgan.video.v1"


def ncnn_video_upscale_handler_identity() -> tuple[str, str]:
    return _HANDLER_CODE, _HANDLER_VERSION


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _redact_local_paths(text: str, argv: list[str], *, limit: int) -> str:
    """Keep bounded process diagnostics without exposing absolute local paths."""
    result = text
    variants: set[str] = set()
    for raw in argv:
        try:
            candidate = Path(raw)
        except (OSError, ValueError):
            continue
        if not candidate.is_absolute():
            continue
        variants.update({raw, str(candidate), candidate.as_posix(), str(candidate).replace("\\", "/")})
    for value in sorted((item for item in variants if item), key=len, reverse=True):
        result = result.replace(value, "<local-path>")
    return result[-limit:]


def _terminate_owned_process_tree(process: subprocess.Popen[str], *, grace_seconds: float = 5) -> None:
    """Stop only the process tree created for one executor command.

    Real-ESRGAN and FFmpeg can be launched through a wrapper in packaged
    Windows builds.  Terminating only that wrapper can strand the real GPU
    process.  The PID passed here always comes from the Popen call immediately
    above, so ``taskkill /T`` stays inside the executor-owned boundary.
    POSIX children start in a new session and use the equivalent process group.
    """
    if process.poll() is not None:
        return
    pid = int(process.pid)
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        taskkill = system_root / "System32" / "taskkill.exe"
        try:
            subprocess.run(
                [str(taskkill), "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=max(1, grace_seconds),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        kill_process_group = getattr(os, "killpg", None)
        try:
            if kill_process_group is not None:
                kill_process_group(pid, int(getattr(signal, "SIGTERM", 15)))
        except (OSError, ProcessLookupError):
            pass
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name != "nt":
        kill_process_group = getattr(os, "killpg", None)
        try:
            if kill_process_group is not None:
                kill_process_group(pid, int(getattr(signal, "SIGKILL", 9)))
        except (OSError, ProcessLookupError):
            pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        # The worker can now fail its attempt; the PID is retained in the
        # process object for host-level diagnostics instead of blocking forever.
        pass


def _require_path(value: object, *, base: Path, code: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DomainRuleError(code, "冻结运行时缺少受控本机路径")
    candidate = Path(value.strip())
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve()
    if not resolved.exists() or resolved.is_symlink():
        raise DomainRuleError(code, "冻结运行时文件不存在或不可使用", {"path_suffix": resolved.suffix})
    return resolved


class NcnnVideoUpscaleExecutor:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        cancel_check: Callable[[], bool] | None = None,
        report_progress: Callable[[dict[str, Any]], bool] | None = None,
        attempt_context: Callable[[], tuple[str, str, str] | None] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.cancel_check = cancel_check or (lambda: False)
        self.report_progress = report_progress or (lambda progress: False)
        self.attempt_context = attempt_context or (lambda: None)

    def execute(self, snapshot: WorkerExecutionSnapshot, output_root: Path) -> tuple[str, str]:
        self._assert_snapshot(snapshot)
        run_id = snapshot.semantic_inputs.get("upscale_run_id")
        if not isinstance(run_id, str) or not run_id:
            raise DomainRuleError("UPSCALE_RUN_ID_REQUIRED", "冻结超分快照缺少 run_id")
        facts = self._load_run(run_id, snapshot)
        recovered = self._recover_registered_output(facts, output_root)
        if recovered is not None:
            return recovered
        recovered = self._recover_published_output(facts, snapshot, output_root)
        if recovered is not None:
            return recovered
        runtime = snapshot.runtime_configuration
        executable = _require_path(
            runtime.get("executable_path", runtime.get("program_path", runtime.get("executable"))),
            base=self.settings.release_root,
            code="UPSCALE_RUNTIME_ARTIFACT_CHANGED",
        )
        if not executable.is_file():
            raise DomainRuleError("UPSCALE_RUNTIME_ARTIFACT_CHANGED", "NCNN 可执行程序不是文件")
        raw_prefix = runtime.get("argv_prefix", [])
        if (
            not isinstance(raw_prefix, list)
            or len(raw_prefix) > 8
            or any(not isinstance(value, str) or not value or len(value) > 500 for value in raw_prefix)
        ):
            raise DomainRuleError("UPSCALE_RUNTIME_CONFIGURATION_INVALID", "NCNN 运行时 argv_prefix 无效")
        argv_prefix = [str(value) for value in raw_prefix]
        model_locator = snapshot.model_bindings[0].get("native_locator")
        model_path = _require_path(
            model_locator,
            base=self.settings.instance_root,
            code="UPSCALE_MODEL_ARTIFACT_CHANGED",
        )
        model_dir = model_path if model_path.is_dir() else model_path.parent
        if runtime.get("provider") == "NCNN_VULKAN_LOCAL_PROCESS":
            self._verify_frozen_model_files(runtime, model_dir)
        options = facts["options"]
        model_options = options["model"]
        pipeline = options["pipeline"]
        geometry = options["geometry"]
        source_path = facts["source_path"]
        source_sha256, _ = _hash_file(source_path)
        if not hmac.compare_digest(source_sha256, str(facts["source"]["source_sha256"])):
            raise DomainRuleError("UPSCALE_SOURCE_HASH_CHANGED", "运行前源文件内容已变化")
        probe = self._probe_count(source_path)
        source_frame_count = int(probe["frame_count"])
        fps = str(probe["fps"])
        try:
            fps_value = Fraction(fps)
        except (ValueError, ZeroDivisionError) as error:
            raise DomainRuleError("UPSCALE_SOURCE_FPS_INVALID", "无法确定源视频的 CFR 帧率") from error
        if fps_value <= 0:
            raise DomainRuleError("UPSCALE_SOURCE_FPS_INVALID", "无法确定源视频的 CFR 帧率")
        purpose = str(facts.get("purpose") or "FULL")
        sample_start_ms: int | None = None
        sample_duration_ms: int | None = None
        range_start = 0
        range_end = source_frame_count
        if purpose == "PREVIEW":
            sample_start_ms = int(facts.get("sample_start_ms") or 0)
            requested_duration_ms = int(facts.get("sample_duration_ms") or 5000)
            range_start = int(Fraction(sample_start_ms, 1000) * fps_value)
            if range_start >= source_frame_count:
                raise DomainRuleError("UPSCALE_PREVIEW_RANGE_INVALID", "样片起点超出源视频时长")
            requested_frames = max(1, int(Fraction(requested_duration_ms, 1000) * fps_value))
            range_end = min(source_frame_count, range_start + requested_frames)
            sample_duration_ms = max(1, round(1000 * (range_end - range_start) / float(fps_value)))
        frame_count = range_end - range_start
        chunk_frames = int(pipeline["chunk_frames"])
        if frame_count <= 0:
            raise DomainRuleError("UPSCALE_SOURCE_FRAME_COUNT_INVALID", "无法确定源视频的完整帧数")

        run_root = output_root / "ncnn-upscale"
        segments_root = run_root / "segments"
        segments_root.mkdir(parents=True, exist_ok=True)
        segments: list[Path] = []
        chunk_count = (frame_count + chunk_frames - 1) // chunk_frames
        active_model_options = dict(model_options)
        for ordinal in range(chunk_count):
            self._assert_runtime_disk_space(facts)
            start = range_start + ordinal * chunk_frames
            end = min(range_end, start + chunk_frames)
            segment = segments_root / f"chunk-{ordinal:06d}.mp4"
            reused_parameters = self._reuse_chunk(
                run_id,
                ordinal,
                start,
                end,
                segment,
                snapshot.execution_snapshot_id,
            )
            if reused_parameters is not None:
                active_model_options = reused_parameters
                segments.append(segment)
                self._progress(ordinal + 1, chunk_count, end - range_start, frame_count, "REUSING_CHUNK")
                continue
            chunk_root = run_root / f"chunk-{ordinal:06d}"
            input_frames = chunk_root / "input"
            output_frames = chunk_root / "output"
            shutil.rmtree(chunk_root, ignore_errors=True)
            input_frames.mkdir(parents=True)
            output_frames.mkdir(parents=True)
            try:
                self._check_cancel()
                self._extract_frames(source_path, input_frames, start, end)
                expected = end - start
                if len(list(input_frames.glob("*.png"))) != expected:
                    raise DomainRuleError("UPSCALE_DECODE_FRAME_MISMATCH", "分块抽帧数量与计划不一致")
                active_model_options = self._run_ncnn(
                    executable,
                    argv_prefix,
                    model_dir,
                    input_frames,
                    output_frames,
                    active_model_options,
                    int(geometry["native_scale"]),
                    allow_tile_fallback=ordinal == 0,
                )
                if len(list(output_frames.glob("*.png"))) != expected:
                    raise DomainRuleError("UPSCALE_INFERENCE_FRAME_MISMATCH", "NCNN 输出帧数量与输入不一致")
                self._encode_segment(output_frames, segment, fps, expected, geometry, pipeline)
                segment_hash, _ = _hash_file(segment)
                self._commit_chunk(
                    run_id,
                    ordinal,
                    start,
                    end,
                    source_sha256,
                    snapshot.execution_snapshot_id,
                    segment,
                    segment_hash,
                    expected,
                    active_model_options,
                )
                segments.append(segment)
            finally:
                shutil.rmtree(chunk_root, ignore_errors=True)
            self._progress(ordinal + 1, chunk_count, end - range_start, frame_count, "INFERENCE")

        self._check_cancel()
        concat_file = run_root / "segments.txt"
        write_atomic(
            concat_file,
            lambda target: target.write_text(
                "".join(f"file '{segment.as_posix()}'\n" for segment in segments), encoding="utf-8"
            ),
        )
        staged = run_root / "final.partial.mp4"
        audio_processing = self._mux(
            concat_file,
            source_path,
            staged,
            pipeline,
            probe,
            sample_start_ms=sample_start_ms,
            sample_duration_ms=sample_duration_ms,
        )
        final_probe = MediaService(self.database, self.settings).probe_output(staged, "VIDEO")
        final_stream: dict[str, Any] = next(
            (stream for stream in final_probe.get("streams", []) if stream.get("codec_type") == "video"),
            {},
        )
        if (
            int(final_stream.get("width") or 0) != int(geometry["target"]["width"])
            or int(final_stream.get("height") or 0) != int(geometry["target"]["height"])
        ):
            raise DomainRuleError("UPSCALE_OUTPUT_GEOMETRY_MISMATCH", "超分输出尺寸与冻结计划不一致")
        counted = self._probe_count(staged)
        if int(counted["frame_count"]) != frame_count:
            raise DomainRuleError("UPSCALE_OUTPUT_FRAME_MISMATCH", "超分输出总帧数与源视频不一致")
        qc_checks = self._validate_output_qc(
            source_probe=probe["probe"],
            output_probe=counted["probe"],
            expected_frames=frame_count,
            target_width=int(geometry["target"]["width"]),
            target_height=int(geometry["target"]["height"]),
            expected_duration_ms=sample_duration_ms if purpose == "PREVIEW" else None,
        )
        after_sha256, _ = _hash_file(source_path)
        if not hmac.compare_digest(source_sha256, after_sha256):
            raise DomainRuleError("UPSCALE_SOURCE_HASH_CHANGED", "运行期间源文件内容发生变化")

        official = (
            facts["project_root"] / "05_outputs" / "upscale" / "previews" / f"{run_id}.mp4"
            if purpose == "PREVIEW"
            else facts["project_root"] / "05_outputs" / "upscale" / facts["episode_code"] / f"{run_id}.mp4"
        )
        official.parent.mkdir(parents=True, exist_ok=True)
        self._assert_runtime_disk_space(facts)
        self._assert_attempt_fence(run_id)
        official_partial = official.with_name(f".{official.name}.partial-{uuid.uuid4().hex}")
        shutil.copyfile(staged, official_partial)
        replace_path(official_partial, official)
        output_sha256, output_size = _hash_file(official)
        publish_receipt = output_root / "ncnn-upscale-publish.json"
        write_atomic(
            publish_receipt,
            lambda target: target.write_text(
                json.dumps(
                    {
                        "schema_version": "localdrama.ncnn-video-upscale-publish.v1",
                        "run_id": run_id,
                        "execution_snapshot_id": snapshot.execution_snapshot_id,
                        "purpose": purpose,
                        "output_rel_path": official.relative_to(facts["project_root"]).as_posix(),
                        "output_sha256": output_sha256,
                        "output_byte_size": output_size,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            ),
        )
        render: dict[str, str] | None = None
        if purpose == "PREVIEW":
            self._register_preview(
                facts,
                official,
                output_sha256,
                output_size,
                sample_duration_ms=sample_duration_ms or 0,
                frame_count=frame_count,
            )
        else:
            render = self._register_output(
                facts,
                snapshot,
                official,
                output_sha256,
                output_size,
                final_probe,
                probe,
                geometry,
                pipeline,
                active_model_options,
                audio_processing=audio_processing,
                source_frame_count=source_frame_count,
                output_frame_count=int(counted["frame_count"]),
                qc_checks=qc_checks,
            )
        receipt = output_root / "ncnn-upscale-receipt.json"
        write_atomic(
            receipt,
            lambda target: target.write_text(
                json.dumps(
                    {
                        "schema_version": "localdrama.ncnn-video-upscale-receipt.v1",
                        "run_id": run_id,
                        "purpose": purpose,
                        "render_id": render["id"] if render else None,
                        "preview_rel_path": official.relative_to(facts["project_root"]).as_posix() if purpose == "PREVIEW" else None,
                        "sample_start_ms": sample_start_ms,
                        "sample_duration_ms": sample_duration_ms,
                        "source_sha256": source_sha256,
                        "output_sha256": output_sha256,
                        "output_byte_size": output_size,
                        "input_frames": frame_count,
                        "output_frames": frame_count,
                        "chunk_count": chunk_count,
                        "actual_model_options": active_model_options,
                        "audio_processing": audio_processing,
                        "network_used": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            ),
        )
        publish_receipt.unlink(missing_ok=True)
        return (
            "VIDEO_UPSCALE_PREVIEW_RECEIPT" if purpose == "PREVIEW" else "VIDEO_UPSCALE_RECEIPT",
            receipt.relative_to(self.settings.work_root).as_posix(),
        )

    def _verify_frozen_model_files(self, runtime: Mapping[str, Any], model_dir: Path) -> None:
        raw_files = runtime.get("model_files")
        expected_bundle = runtime.get("model_bundle_sha256")
        if not isinstance(raw_files, list) or not raw_files or not isinstance(expected_bundle, str):
            raise DomainRuleError("UPSCALE_MODEL_ARTIFACT_CHANGED", "冻结运行时缺少模型组件哈希。")

        observed: list[dict[str, object]] = []
        for raw in raw_files:
            if not isinstance(raw, dict):
                raise DomainRuleError("UPSCALE_MODEL_ARTIFACT_CHANGED", "冻结模型组件合同无效。")
            name = raw.get("name")
            expected_hash = raw.get("sha256")
            expected_size = raw.get("size_bytes")
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or not isinstance(expected_hash, str)
                or not isinstance(expected_size, int)
            ):
                raise DomainRuleError("UPSCALE_MODEL_ARTIFACT_CHANGED", "冻结模型组件合同无效。")
            path = model_dir / name
            if not path.is_file() or path.is_symlink():
                raise DomainRuleError("UPSCALE_MODEL_ARTIFACT_CHANGED", "冻结模型组件缺失或不可使用。")
            actual_hash, actual_size = _hash_file(path)
            if actual_size != expected_size or not hmac.compare_digest(actual_hash, expected_hash):
                raise DomainRuleError("UPSCALE_MODEL_ARTIFACT_CHANGED", "模型组件内容已变化，请重新验证并发布 Profile。")
            observed.append({"name": name, "sha256": actual_hash, "size_bytes": actual_size})
        if not hmac.compare_digest(_hash(observed), expected_bundle):
            raise DomainRuleError("UPSCALE_MODEL_ARTIFACT_CHANGED", "模型组件集合与已发布 Profile 不一致。")

    def _assert_runtime_disk_space(self, facts: Mapping[str, Any]) -> None:
        """Recheck each distinct output volume before a chunk or final publish."""
        options = facts.get("options")
        budget = options.get("disk_budget") if isinstance(options, Mapping) else None
        planned_minimum = int(budget.get("minimum_free_bytes") or 0) if isinstance(budget, Mapping) else 0
        checked_volumes: set[str] = set()
        for root, is_project in (
            (self.settings.work_root, False),
            (Path(facts["project_root"]), True),
        ):
            resolved = root.resolve()
            volume = (resolved.anchor or str(resolved)).casefold()
            if volume in checked_volumes:
                continue
            checked_volumes.add(volume)
            usage = shutil.disk_usage(resolved)
            minimum = max(5 * 1024**3, round(usage.total * 0.05))
            if is_project:
                minimum = max(minimum, planned_minimum)
            if usage.free < minimum:
                raise OSError(
                    errno.ENOSPC,
                    f"video upscale free space below safety threshold on volume {volume}",
                )

    def _assert_snapshot(self, snapshot: WorkerExecutionSnapshot) -> None:
        if snapshot.capability_code != "UPSCALE_VIDEO" or snapshot.adapter_code != _ADAPTER_CODE:
            raise DomainRuleError("UPSCALE_SNAPSHOT_MISMATCH", "冻结快照不属于 NCNN 视频超分处理器")
        if str(snapshot.network_policy.get("mode") or "").upper() != "LOCAL_ONLY":
            raise DomainRuleError("UPSCALE_NETWORK_POLICY_INVALID", "视频超分处理器只允许 LOCAL_ONLY")
        if len(snapshot.model_bindings) != 1:
            raise DomainRuleError("UPSCALE_MODEL_BINDING_INVALID", "视频超分必须冻结且仅冻结一个模型安装")

    def _load_run(self, run_id: str, snapshot: WorkerExecutionSnapshot) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT run.*,COALESCE(run.episode_id,item.episode_id) AS resolved_episode_id,
                COALESCE(run.source_descriptor_json,item.source_descriptor_json) AS resolved_source_descriptor_json,
                COALESCE(run.effective_options_json,item.effective_options_json) AS resolved_effective_options_json,
                episode.code AS episode_code,project.root_rel,root.timeline_revision_id,
                root.input_snapshot_json AS root_input_snapshot_json
                FROM video_upscale_runs run
                LEFT JOIN video_upscale_batch_items item ON item.id=run.batch_item_id
                JOIN episodes episode ON episode.id=COALESCE(run.episode_id,item.episode_id)
                JOIN seasons season ON season.id=episode.season_id
                JOIN projects project ON project.id=season.project_id
                JOIN episode_render_versions root ON root.id=run.root_render_id
                WHERE run.id=?""",
                (run_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("UPSCALE_RUN_NOT_FOUND", "视频超分运行记录不存在")
        if str(row["execution_snapshot_id"]) != snapshot.execution_snapshot_id:
            raise DomainRuleError("UPSCALE_RUN_SNAPSHOT_MISMATCH", "超分运行与冻结快照不一致")
        project_root = self.settings.resolve_project_root(str(row["root_rel"]))
        source = json.loads(str(row["resolved_source_descriptor_json"] or "{}"))
        if not source:
            raise DomainRuleError("UPSCALE_RUN_INPUTS_MISSING", "超分运行缺少冻结来源描述")
        source_path = controlled_path(
            project_root,
            str(source["rel_path"]),
            must_exist=True,
            require_file=True,
            code="UPSCALE_SOURCE_FILE_MISSING",
        )
        return {
            **dict(row),
            "source": source,
            "episode_id": str(row["resolved_episode_id"]),
            "options": json.loads(str(row["resolved_effective_options_json"] or "{}")),
            "source_path": source_path,
            "project_root": project_root,
        }

    def _recover_registered_output(
        self,
        facts: Mapping[str, Any],
        output_root: Path,
    ) -> tuple[str, str] | None:
        """Finish a Job after its business output committed before Job completion."""
        run_id = str(facts["id"])
        purpose = str(facts.get("purpose") or "FULL")
        with self.database.connect() as connection:
            if purpose == "PREVIEW":
                row = connection.execute(
                    "SELECT output_rel_path AS rel_path,output_sha256 AS sha256 FROM video_upscale_runs WHERE id=?",
                    (run_id,),
                ).fetchone()
                artifact_kind = "VIDEO_UPSCALE_PREVIEW_RECEIPT"
            else:
                row = connection.execute(
                    """SELECT render.rel_path,render.sha256
                       FROM video_upscale_runs run
                       JOIN episode_render_versions render ON render.id=run.output_render_id
                       WHERE run.id=? AND render.upscale_run_id=run.id""",
                    (run_id,),
                ).fetchone()
                artifact_kind = "VIDEO_UPSCALE_RECEIPT"
        if row is None or not row["rel_path"] or not row["sha256"]:
            return None
        path = controlled_path(
            facts["project_root"],
            str(row["rel_path"]),
            must_exist=True,
            require_file=True,
            code="UPSCALE_REGISTERED_OUTPUT_MISSING",
        )
        actual_sha256, output_size = _hash_file(path)
        if not hmac.compare_digest(actual_sha256, str(row["sha256"])):
            raise DomainRuleError(
                "UPSCALE_REGISTERED_OUTPUT_CHANGED",
                "已登记的超分输出哈希不一致，不能自动完成恢复",
            )
        self._assert_attempt_fence(run_id)
        output_root.mkdir(parents=True, exist_ok=True)
        receipt = output_root / "ncnn-upscale-receipt.json"
        write_atomic(
            receipt,
            lambda target: target.write_text(
                json.dumps(
                    {
                        "schema_version": "localdrama.ncnn-video-upscale-receipt.v1",
                        "run_id": run_id,
                        "purpose": purpose,
                        "recovered_existing_output": True,
                        "output_sha256": actual_sha256,
                        "output_byte_size": output_size,
                        "network_used": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            ),
        )
        if self.report_progress({"phase": "RECOVERED_REGISTERED_OUTPUT", "percent": 100}):
            raise DomainRuleError("JOB_CANCELLED", "恢复既有超分输出时任务已取消")
        return artifact_kind, receipt.relative_to(self.settings.work_root).as_posix()

    def _official_output_path(self, facts: Mapping[str, Any]) -> Path:
        run_id = str(facts["id"])
        if str(facts.get("purpose") or "FULL") == "PREVIEW":
            return facts["project_root"] / "05_outputs" / "upscale" / "previews" / f"{run_id}.mp4"
        return (
            facts["project_root"]
            / "05_outputs"
            / "upscale"
            / str(facts["episode_code"])
            / f"{run_id}.mp4"
        )

    def _recover_published_output(
        self,
        facts: Mapping[str, Any],
        snapshot: WorkerExecutionSnapshot,
        output_root: Path,
    ) -> tuple[str, str] | None:
        """Register an atomically published output after a pre-DB crash."""

        publish_receipt = output_root / "ncnn-upscale-publish.json"
        if not publish_receipt.is_file():
            return None
        try:
            frozen = json.loads(publish_receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DomainRuleError("UPSCALE_PUBLISH_RECEIPT_INVALID", "超分发布恢复回执损坏，不能自动登记") from error
        run_id = str(facts["id"])
        purpose = str(facts.get("purpose") or "FULL")
        expected_path = self._official_output_path(facts)
        expected_rel = expected_path.relative_to(facts["project_root"]).as_posix()
        if (
            not isinstance(frozen, dict)
            or frozen.get("schema_version") != "localdrama.ncnn-video-upscale-publish.v1"
            or str(frozen.get("run_id") or "") != run_id
            or str(frozen.get("execution_snapshot_id") or "") != snapshot.execution_snapshot_id
            or str(frozen.get("purpose") or "") != purpose
            or str(frozen.get("output_rel_path") or "") != expected_rel
        ):
            raise DomainRuleError("UPSCALE_PUBLISH_RECEIPT_INVALID", "超分发布恢复回执与冻结运行不一致")
        path = controlled_path(
            facts["project_root"],
            expected_rel,
            must_exist=True,
            require_file=True,
            code="UPSCALE_PUBLISHED_OUTPUT_MISSING",
        )
        output_sha256, output_size = _hash_file(path)
        frozen_size = frozen.get("output_byte_size")
        if (
            not hmac.compare_digest(output_sha256, str(frozen.get("output_sha256") or ""))
            or not isinstance(frozen_size, int)
            or output_size != frozen_size
        ):
            raise DomainRuleError("UPSCALE_PUBLISHED_OUTPUT_CHANGED", "已发布的超分输出与恢复回执不一致")
        self._assert_attempt_fence(run_id)
        source_sha256, _ = _hash_file(facts["source_path"])
        if not hmac.compare_digest(source_sha256, str(facts["source"]["source_sha256"])):
            raise DomainRuleError("UPSCALE_SOURCE_HASH_CHANGED", "恢复登记前源文件内容已变化")
        source_probe = self._probe_count(facts["source_path"])
        try:
            fps_value = Fraction(str(source_probe["fps"]))
        except (ValueError, ZeroDivisionError) as error:
            raise DomainRuleError("UPSCALE_SOURCE_FPS_INVALID", "无法确定源视频的 CFR 帧率") from error
        if fps_value <= 0:
            raise DomainRuleError("UPSCALE_SOURCE_FPS_INVALID", "无法确定源视频的 CFR 帧率")
        source_frame_count = int(source_probe["frame_count"])
        frame_count = source_frame_count
        sample_duration_ms: int | None = None
        if purpose == "PREVIEW":
            sample_start_ms = int(facts.get("sample_start_ms") or 0)
            requested_duration_ms = int(facts.get("sample_duration_ms") or 5000)
            range_start = int(Fraction(sample_start_ms, 1000) * fps_value)
            requested_frames = max(1, int(Fraction(requested_duration_ms, 1000) * fps_value))
            frame_count = min(source_frame_count, range_start + requested_frames) - range_start
            if frame_count <= 0:
                raise DomainRuleError("UPSCALE_PREVIEW_RANGE_INVALID", "样片恢复范围超出源视频时长")
            sample_duration_ms = max(1, round(1000 * frame_count / float(fps_value)))
        final_probe = MediaService(self.database, self.settings).probe_output(path, "VIDEO")
        counted = self._probe_count(path)
        options = facts["options"]
        geometry = options["geometry"]
        qc_checks = self._validate_output_qc(
            source_probe=source_probe["probe"],
            output_probe=counted["probe"],
            expected_frames=frame_count,
            target_width=int(geometry["target"]["width"]),
            target_height=int(geometry["target"]["height"]),
            expected_duration_ms=sample_duration_ms if purpose == "PREVIEW" else None,
        )
        render: dict[str, str] | None = None
        if purpose == "PREVIEW":
            self._register_preview(
                facts,
                path,
                output_sha256,
                output_size,
                sample_duration_ms=sample_duration_ms or 0,
                frame_count=frame_count,
            )
            artifact_kind = "VIDEO_UPSCALE_PREVIEW_RECEIPT"
        else:
            render = self._register_output(
                facts,
                snapshot,
                path,
                output_sha256,
                output_size,
                final_probe,
                source_probe,
                geometry,
                options["pipeline"],
                options["model"],
                audio_processing=self._audio_processing(source_probe, options["pipeline"]),
                source_frame_count=source_frame_count,
                output_frame_count=int(counted["frame_count"]),
                qc_checks=qc_checks,
            )
            artifact_kind = "VIDEO_UPSCALE_RECEIPT"
        receipt = output_root / "ncnn-upscale-receipt.json"
        write_atomic(
            receipt,
            lambda target: target.write_text(
                json.dumps(
                    {
                        "schema_version": "localdrama.ncnn-video-upscale-receipt.v1",
                        "run_id": run_id,
                        "purpose": purpose,
                        "render_id": render["id"] if render else None,
                        "recovered_published_output": True,
                        "output_sha256": output_sha256,
                        "output_byte_size": output_size,
                        "input_frames": frame_count,
                        "output_frames": int(counted["frame_count"]),
                        "network_used": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            ),
        )
        publish_receipt.unlink(missing_ok=True)
        if self.report_progress({"phase": "RECOVERED_PUBLISHED_OUTPUT", "percent": 100}):
            raise DomainRuleError("JOB_CANCELLED", "恢复已发布超分输出时任务已取消")
        return artifact_kind, receipt.relative_to(self.settings.work_root).as_posix()

    def _run(self, args: list[str], *, timeout: float, stall_watch: Path | None = None) -> None:
        creationflags = 0
        start_new_session = False
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            start_new_session = True
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as process_log:
            try:
                process = subprocess.Popen(
                    args,
                    stdout=process_log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                    start_new_session=start_new_session,
                )
            except OSError as error:
                raise DomainRuleError("UPSCALE_PROCESS_START_FAILED", "无法启动本机超分处理进程") from error
            deadline = monotonic() + timeout
            last_count = -1
            stall_deadline = monotonic() + 120
            try:
                while process.poll() is None:
                    try:
                        process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        pass
                    self._check_cancel()
                    if monotonic() >= deadline:
                        raise DomainRuleError("UPSCALE_PROCESS_TIMEOUT", "本机超分处理进程超时")
                    if stall_watch is not None:
                        count = sum(1 for _ in stall_watch.glob("*.png"))
                        if count != last_count:
                            last_count = count
                            stall_deadline = monotonic() + 120
                        elif monotonic() >= stall_deadline:
                            raise DomainRuleError("UPSCALE_PROCESS_STALLED", "NCNN 连续 120 秒没有生成新帧")
                process.wait(timeout=5)
            except BaseException:
                _terminate_owned_process_tree(process)
                raise
            if process.returncode != 0:
                process_log.seek(0)
                detail = _redact_local_paths(process_log.read(), args, limit=1000)
                raise DomainRuleError(
                    "UPSCALE_PROCESS_FAILED",
                    "本机超分处理进程执行失败",
                    {"returncode": int(process.returncode), "stderr_redacted": detail},
                )

    def _extract_frames(self, source: Path, output: Path, start: int, end: int) -> None:
        ffmpeg = self.settings.ffmpeg_path
        if not ffmpeg:
            raise DomainRuleError("FFMPEG_UNAVAILABLE", "本机 FFmpeg 不可用")
        selector = f"select=gte(n\\,{start})*lt(n\\,{end})"
        self._run(
            [
                str(ffmpeg), "-hide_banner", "-v", "error", "-i", str(source), "-vf", selector,
                "-vsync", "0", "-start_number", str(start), "-y", str(output / "frame_%010d.png"),
            ],
            timeout=3600,
        )

    def _run_ncnn(
        self,
        executable: Path,
        argv_prefix: list[str],
        model_dir: Path,
        input_frames: Path,
        output_frames: Path,
        options: Mapping[str, Any],
        native_scale: int,
        *,
        allow_tile_fallback: bool,
    ) -> dict[str, Any]:
        resolved = dict(options)
        initial_tile = int(resolved["tile_size"])
        fallback_tiles = [int(value) for value in resolved.get("tile_fallback_sizes", [])]
        attempts = [initial_tile, *(fallback_tiles if allow_tile_fallback else [])]
        tried: list[int] = []
        for tile_size in attempts:
            if tile_size in tried:
                continue
            tried.append(tile_size)
            args = [
                str(executable),
                *argv_prefix,
                "-i", str(input_frames),
                "-o", str(output_frames),
                "-n", str(resolved["model_name"]),
                "-s", str(native_scale),
                "-t", str(tile_size),
                "-m", str(model_dir),
                "-g", str(int(resolved.get("gpu_device", 0))),
                "-j", f"{int(resolved['load_threads'])}:{int(resolved['proc_threads'])}:{int(resolved['save_threads'])}",
                "-f", "png",
            ]
            if bool(resolved.get("tta")):
                args.append("-x")
            try:
                self._run(args, timeout=12 * 60 * 60, stall_watch=output_frames)
                resolved["tile_size"] = tile_size
                resolved["tile_fallback_applied"] = tile_size != initial_tile
                resolved["tile_attempts"] = tried
                return resolved
            except DomainRuleError as error:
                if error.code != "UPSCALE_PROCESS_FAILED" or not self._is_ncnn_oom(error):
                    raise
                if tile_size == attempts[-1]:
                    raise DomainRuleError(
                        "UPSCALE_GPU_OUT_OF_MEMORY",
                        "NCNN 显存不足且冻结的 tile 回退已耗尽；可恢复后重试或改用低显存预设",
                        {"attempted_tile_sizes": tried},
                    ) from error
                shutil.rmtree(output_frames, ignore_errors=True)
                output_frames.mkdir(parents=True, exist_ok=True)
                if self.report_progress(
                    {
                        "phase": "TILE_FALLBACK",
                        "attempted_tile_sizes": tried,
                        "next_tile_size": attempts[len(tried)],
                    }
                ):
                    raise DomainRuleError("JOB_CANCELLED", "tile 回退前任务已取消") from error
        raise DomainRuleError("UPSCALE_GPU_OUT_OF_MEMORY", "NCNN tile 回退未产生可执行参数")

    @staticmethod
    def _is_ncnn_oom(error: DomainRuleError) -> bool:
        detail = str(error.details.get("stderr_redacted") or "").lower()
        markers = (
            "out of memory",
            "vk_error_out_of_device_memory",
            "vk_error_out_of_host_memory",
            "vkallocatememory failed",
            "vkbuffer memory allocation failed",
            "failed to allocate",
            "allocation failed",
            "insufficient memory",
        )
        return any(marker in detail for marker in markers)

    def _encode_segment(
        self,
        frames: Path,
        segment: Path,
        fps: str,
        expected: int,
        geometry: Mapping[str, Any],
        pipeline: Mapping[str, Any],
    ) -> None:
        ffmpeg = self.settings.ffmpeg_path
        if not ffmpeg:
            raise DomainRuleError("FFMPEG_UNAVAILABLE", "本机 FFmpeg 不可用")
        content = geometry["content_rect"]
        target = geometry["target"]
        if str(target["fit"]) == "CONTAIN":
            video_filter = (
                f"scale={content['width']}:{content['height']}:flags=lanczos:"
                "in_range=pc:out_range=tv:out_color_matrix=bt709,"
                f"pad={target['width']}:{target['height']}:{geometry['padding']['left']}:{geometry['padding']['top']}:black"
            )
        else:
            video_filter = (
                f"scale={content['width']}:{content['height']}:flags=lanczos:"
                "in_range=pc:out_range=tv:out_color_matrix=bt709,"
                f"crop={target['width']}:{target['height']}:{geometry['crop']['left']}:{geometry['crop']['top']}"
            )
        names = sorted(frames.glob("*.png"))
        if not names:
            raise DomainRuleError("UPSCALE_INFERENCE_FRAME_MISMATCH", "NCNN 没有输出帧")
        start_number = int(names[0].stem.split("_")[-1])
        args = [
            str(ffmpeg), "-hide_banner", "-v", "error", "-framerate", fps,
            "-start_number", str(start_number), "-i", str(frames / "frame_%010d.png"),
            "-frames:v", str(expected), "-vf", video_filter,
            "-c:v", str(pipeline["encoder"]), "-preset", str(pipeline["preset"]),
        ]
        if pipeline["encoder"] == "libx264":
            args += [
                "-crf",
                str(int(pipeline["crf"])),
                "-x264-params",
                "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited",
            ]
        else:
            raise DomainRuleError("UPSCALE_NVENC_CONTRACT_REQUIRED", "当前 Profile 未冻结 NVENC CQ 合同")
        args += [
            "-pix_fmt", str(pipeline["pix_fmt"]),
            "-colorspace", "bt709",
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-color_range", "tv",
            "-an", "-y", str(segment),
        ]
        self._run(args, timeout=3600)

    @staticmethod
    def _audio_processing(source_probe: Mapping[str, Any], pipeline: Mapping[str, Any]) -> dict[str, Any]:
        audio_streams = [
            stream for stream in source_probe["probe"].get("streams", []) if stream.get("codec_type") == "audio"
        ]
        codecs = [str(stream.get("codec_name") or "").lower() for stream in audio_streams]
        compatible = all(codec in {"aac", "mp3", "ac3", "eac3", "alac"} for codec in codecs)
        if str(pipeline["audio_policy"]) == "COPY_STRICT" and not compatible:
            raise DomainRuleError("UPSCALE_AUDIO_COPY_UNSUPPORTED", "源音轨不能严格复制到 MP4")
        return {
            "track_count": len(audio_streams),
            "source_codecs": codecs,
            "mode": "NONE" if not audio_streams else ("COPY" if compatible else "AAC_TRANSCODE"),
            "aac_bitrate_kbps": int(pipeline["aac_bitrate_kbps"]) if audio_streams and not compatible else None,
            "preserves_all_tracks": True,
            "source_is_final_render_audio": True,
        }

    def _mux(
        self,
        concat_file: Path,
        source: Path,
        output: Path,
        pipeline: Mapping[str, Any],
        source_probe: Mapping[str, Any],
        *,
        sample_start_ms: int | None = None,
        sample_duration_ms: int | None = None,
    ) -> dict[str, Any]:
        ffmpeg = self.settings.ffmpeg_path
        if not ffmpeg:
            raise DomainRuleError("FFMPEG_UNAVAILABLE", "本机 FFmpeg 不可用")
        audio_processing = self._audio_processing(source_probe, pipeline)
        audio_args = ["-c:a", "copy"] if audio_processing["mode"] in {"NONE", "COPY"} else ["-c:a", "aac", "-b:a", f"{int(pipeline['aac_bitrate_kbps'])}k"]
        source_input = ["-i", str(source)]
        if sample_start_ms is not None and sample_duration_ms is not None:
            source_input = [
                "-ss", f"{sample_start_ms / 1000:.6f}",
                "-t", f"{sample_duration_ms / 1000:.6f}",
                "-i", str(source),
            ]
        self._run(
            [
                str(ffmpeg), "-hide_banner", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_file),
                *source_input, "-map", "0:v:0", "-map", "1:a?", "-map_metadata", "1",
                "-c:v", "copy", *audio_args, "-movflags", "+faststart", "-y", str(output),
            ],
            timeout=3600,
        )
        return audio_processing

    def _register_preview(
        self,
        facts: Mapping[str, Any],
        path: Path,
        output_sha256: str,
        output_size: int,
        *,
        sample_duration_ms: int,
        frame_count: int,
    ) -> None:
        now = _now()
        with self.database.transaction() as connection:
            self._assert_attempt_fence(str(facts["id"]), connection=connection)
            run = connection.execute(
                "SELECT purpose FROM video_upscale_runs WHERE id=?",
                (facts["id"],),
            ).fetchone()
            if run is None or str(run["purpose"]) != "PREVIEW":
                raise DomainRuleError("UPSCALE_PREVIEW_NOT_FOUND", "超分样片运行不存在")
            connection.execute(
                """UPDATE video_upscale_runs
                SET sample_artifact_id=?,output_rel_path=?,output_sha256=?,output_byte_size=?,
                    sample_duration_ms=?,progress_summary_json=?,updated_at=?,revision=revision+1
                WHERE id=?""",
                (
                    str(facts["id"]),
                    path.relative_to(facts["project_root"]).as_posix(),
                    output_sha256,
                    output_size,
                    sample_duration_ms,
                    _json({"phase": "COMPLETED", "percent": 100, "sample_frames": frame_count}),
                    now,
                    facts["id"],
                ),
            )

    def _probe_count(self, path: Path) -> dict[str, Any]:
        ffprobe = self.settings.ffprobe_path
        if not ffprobe:
            raise DomainRuleError("FFPROBE_UNAVAILABLE", "本机 FFprobe 不可用")
        args = [
            str(ffprobe), "-v", "error", "-count_frames", "-show_entries",
            "stream=codec_type,codec_name,width,height,pix_fmt,field_order,avg_frame_rate,r_frame_rate,nb_read_frames,sample_aspect_ratio,color_range,color_space,color_transfer,color_primaries:stream_tags=rotate:stream_side_data=rotation:format=duration",
            "-of", "json", str(path),
        ]
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3600,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DomainRuleError("UPSCALE_SOURCE_PROBE_FAILED", "源视频完整探测失败") from error
        if completed.returncode != 0:
            raise DomainRuleError(
                "UPSCALE_SOURCE_PROBE_FAILED",
                "源视频完整探测失败",
                {"stderr_redacted": _redact_local_paths(completed.stderr, args, limit=500)},
            )
        try:
            probe = json.loads(completed.stdout)
            video = next(stream for stream in probe["streams"] if stream.get("codec_type") == "video")
            frame_count = int(video["nb_read_frames"])
        except (KeyError, TypeError, ValueError, StopIteration, json.JSONDecodeError) as error:
            raise DomainRuleError("UPSCALE_SOURCE_PROBE_FAILED", "源视频完整探测证据无效") from error
        return {"probe": probe, "frame_count": frame_count, "fps": str(video.get("avg_frame_rate") or video.get("r_frame_rate"))}

    @staticmethod
    def _validate_output_qc(
        *,
        source_probe: Mapping[str, Any],
        output_probe: Mapping[str, Any],
        expected_frames: int,
        target_width: int,
        target_height: int,
        expected_duration_ms: int | None,
    ) -> list[tuple[str, str, dict[str, Any]]]:
        source_streams = list(source_probe.get("streams") or [])
        output_streams = list(output_probe.get("streams") or [])
        source_video: dict[str, Any] = next((stream for stream in source_streams if stream.get("codec_type") == "video"), {})
        output_video: dict[str, Any] = next((stream for stream in output_streams if stream.get("codec_type") == "video"), {})
        failures: list[dict[str, Any]] = []
        checks: list[tuple[str, str, dict[str, Any]]] = []

        geometry = {"width": int(output_video.get("width") or 0), "height": int(output_video.get("height") or 0)}
        if geometry != {"width": target_width, "height": target_height}:
            failures.append({"item_id": "dimensions", "expected": {"width": target_width, "height": target_height}, "actual": geometry})
        checks.append(("dimensions", "PASS", geometry))

        output_frames = int(output_video.get("nb_read_frames") or 0)
        if output_frames != expected_frames:
            failures.append({"item_id": "frame_count", "expected": expected_frames, "actual": output_frames})
        checks.append(("frame_count", "PASS", {"expected": expected_frames, "output": output_frames}))

        source_fps_raw = str(source_video.get("avg_frame_rate") or source_video.get("r_frame_rate") or "")
        output_fps_raw = str(output_video.get("avg_frame_rate") or output_video.get("r_frame_rate") or "")
        try:
            source_fps = Fraction(source_fps_raw)
            output_fps = Fraction(output_fps_raw)
        except (ValueError, ZeroDivisionError) as error:
            raise DomainRuleError("UPSCALE_OUTPUT_FPS_INVALID", "超分输出缺少有效帧率证据") from error
        if source_fps != output_fps:
            failures.append({"item_id": "frame_rate", "expected": source_fps_raw, "actual": output_fps_raw})
        checks.append(("frame_rate", "PASS", {"source": source_fps_raw, "output": output_fps_raw}))

        output_duration_ms = round(float((output_probe.get("format") or {}).get("duration") or 0) * 1000)
        if expected_duration_ms is None:
            expected_duration_ms = round(float((source_probe.get("format") or {}).get("duration") or 0) * 1000)
        duration_tolerance_ms = max(100, round(2000 / float(source_fps)))
        if expected_duration_ms <= 0 or abs(output_duration_ms - expected_duration_ms) > duration_tolerance_ms:
            failures.append({"item_id": "duration", "expected_ms": expected_duration_ms, "actual_ms": output_duration_ms, "tolerance_ms": duration_tolerance_ms})
        checks.append(("duration", "PASS", {"expected_ms": expected_duration_ms, "output_ms": output_duration_ms, "tolerance_ms": duration_tolerance_ms}))

        pix_fmt = str(output_video.get("pix_fmt") or "")
        if pix_fmt != "yuv420p":
            failures.append({"item_id": "pixel_format", "expected": "yuv420p", "actual": pix_fmt})
        checks.append(("pixel_format", "PASS", {"value": pix_fmt}))

        field_order = str(output_video.get("field_order") or "unknown").lower()
        if field_order != "progressive":
            failures.append({"item_id": "scan_type", "expected": "progressive", "actual": field_order})
        checks.append(("scan_type", "PASS", {"value": field_order}))

        sample_aspect_ratio = str(output_video.get("sample_aspect_ratio") or "")
        if sample_aspect_ratio != "1:1":
            failures.append({"item_id": "sample_aspect_ratio", "expected": "1:1", "actual": sample_aspect_ratio})
        checks.append(("sample_aspect_ratio", "PASS", {"value": sample_aspect_ratio}))

        color_actual = {
            "color_space": str(output_video.get("color_space") or "").lower(),
            "color_range": str(output_video.get("color_range") or "").lower(),
            "color_primaries": str(output_video.get("color_primaries") or "").lower(),
            "color_transfer": str(output_video.get("color_transfer") or "").lower(),
        }
        color_expected = {
            "color_space": "bt709",
            "color_range": "tv",
            "color_primaries": "bt709",
            "color_transfer": "bt709",
        }
        if color_actual != color_expected:
            failures.append({"item_id": "sdr_color", "expected": color_expected, "actual": color_actual})
        checks.append(("sdr_color", "PASS", color_actual))

        rotations: list[object] = []
        tags = output_video.get("tags")
        if isinstance(tags, dict) and tags.get("rotate") is not None:
            rotations.append(tags["rotate"])
        for side in output_video.get("side_data_list") or []:
            if isinstance(side, dict) and side.get("rotation") is not None:
                rotations.append(side["rotation"])
        try:
            rotated = any(int(float(str(value))) % 360 != 0 for value in rotations)
        except (TypeError, ValueError):
            rotated = True
        if rotated:
            failures.append({"item_id": "rotation", "expected": 0, "actual": rotations})
        checks.append(("rotation", "PASS", {"values": rotations or [0]}))

        source_audio_count = sum(stream.get("codec_type") == "audio" for stream in source_streams)
        output_audio_count = sum(stream.get("codec_type") == "audio" for stream in output_streams)
        if source_audio_count != output_audio_count:
            failures.append({"item_id": "audio_tracks", "expected": source_audio_count, "actual": output_audio_count})
        checks.append(("audio_tracks", "PASS", {"source": source_audio_count, "output": output_audio_count}))

        if failures:
            raise DomainRuleError("UPSCALE_OUTPUT_QC_FAILED", "超分输出机器 QC 未通过", {"failures": failures})
        return checks

    def _reuse_chunk(
        self,
        run_id: str,
        ordinal: int,
        start: int,
        end: int,
        segment: Path,
        snapshot_hash: str,
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT output_sha256,frame_count,snapshot_hash,state,actual_parameters_json FROM video_upscale_chunks
                WHERE run_id=? AND ordinal=?""",
                (run_id, ordinal),
            ).fetchone()
        if row is None or str(row["state"]) != "SUCCEEDED" or str(row["snapshot_hash"]) != snapshot_hash:
            return None
        if int(row["frame_count"]) != end - start or not segment.is_file():
            return None
        actual, _ = _hash_file(segment)
        if not hmac.compare_digest(actual, str(row["output_sha256"])):
            return None
        try:
            parameters = json.loads(str(row["actual_parameters_json"] or "{}"))
        except json.JSONDecodeError:
            return None
        return parameters if isinstance(parameters, dict) and parameters else None

    def _commit_chunk(
        self,
        run_id: str,
        ordinal: int,
        start: int,
        end: int,
        source_hash: str,
        snapshot_hash: str,
        segment: Path,
        segment_hash: str,
        frame_count: int,
        actual_parameters: Mapping[str, Any],
    ) -> None:
        now = _now()
        with self.database.transaction() as connection:
            attempt = self._assert_attempt_fence(run_id, connection=connection)
            connection.execute(
                """INSERT INTO video_upscale_chunks
                (id,run_id,ordinal,start_frame,end_frame_exclusive,source_manifest_hash,snapshot_hash,state,
                 output_rel,output_sha256,frame_count,attempt_id,fencing_token,actual_parameters_json,
                 created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,'SUCCEEDED',?,?,?,?,?,?,?,?,'worker',1,'video-upscale-chunk.v1')
                ON CONFLICT(run_id,ordinal) DO UPDATE SET
                start_frame=excluded.start_frame,end_frame_exclusive=excluded.end_frame_exclusive,
                source_manifest_hash=excluded.source_manifest_hash,snapshot_hash=excluded.snapshot_hash,
                state='SUCCEEDED',output_rel=excluded.output_rel,output_sha256=excluded.output_sha256,
                frame_count=excluded.frame_count,attempt_id=excluded.attempt_id,fencing_token=excluded.fencing_token,
                actual_parameters_json=excluded.actual_parameters_json,updated_at=excluded.updated_at,revision=video_upscale_chunks.revision+1""",
                (
                    str(uuid.uuid4()), run_id, ordinal, start, end, source_hash, snapshot_hash,
                    segment.relative_to(self.settings.work_root).as_posix(), segment_hash, frame_count,
                    attempt[0] if attempt else None, attempt[1] if attempt else None,
                    _json(dict(actual_parameters)), now, now,
                ),
            )

    def _register_output(
        self,
        facts: Mapping[str, Any],
        snapshot: WorkerExecutionSnapshot,
        path: Path,
        output_sha256: str,
        output_size: int,
        final_probe: Mapping[str, Any],
        source_probe: Mapping[str, Any],
        geometry: Mapping[str, Any],
        pipeline: Mapping[str, Any],
        model: Mapping[str, Any],
        *,
        audio_processing: Mapping[str, Any],
        source_frame_count: int,
        output_frame_count: int,
        qc_checks: list[tuple[str, str, dict[str, Any]]],
    ) -> dict[str, str]:
        run_id = str(facts["id"])
        with self.database.transaction() as connection:
            self._assert_attempt_fence(run_id, connection=connection)
            existing = connection.execute(
                "SELECT id FROM episode_render_versions WHERE upscale_run_id=?", (run_id,)
            ).fetchone()
            if existing is not None:
                connection.execute(
                    "UPDATE video_upscale_runs SET output_render_id=?,updated_at=? WHERE id=?",
                    (existing["id"], _now(), run_id),
                )
                return {"id": str(existing["id"])}
            latest = latest_episode_render(connection, str(facts["episode_id"]))
            if latest is None or str(latest["id"]) != str(facts["root_render_id"]):
                raise DomainRuleError("UPSCALE_SOURCE_STALE", "源合成成片已更新，结果不能登记为当前候选")
            run = connection.execute(
                "SELECT fingerprint FROM video_upscale_runs WHERE id=? AND execution_snapshot_id=?",
                (run_id, snapshot.execution_snapshot_id),
            ).fetchone()
            if run is None:
                raise DomainRuleError("UPSCALE_RUN_SNAPSHOT_MISMATCH", "运行记录与执行快照已变化")
            render_id = str(uuid.uuid4())
            now = _now()
            input_snapshot = {
                "schema_version": "localdrama.episode-super-resolution-input.v1",
                "source_descriptor": facts["source"],
                "root_compose_render_id": str(facts["root_render_id"]),
                "execution_snapshot_id": snapshot.execution_snapshot_id,
                "execution_runtime_fingerprint": snapshot.runtime_fingerprint,
                "geometry": geometry,
                "pipeline_options": pipeline,
                "model_options": model,
                "audio_processing": dict(audio_processing),
                "source_probe": source_probe["probe"],
                "output_byte_size": output_size,
                "network_used": False,
                "applied_effects": dict(facts["source"].get("applied_effects") or {}),
            }
            try:
                root_input_snapshot = json.loads(str(facts.get("root_input_snapshot_json") or "{}"))
            except json.JSONDecodeError:
                root_input_snapshot = {}
            if isinstance(root_input_snapshot, dict):
                input_snapshot["input_snapshot"] = root_input_snapshot
                input_snapshot["subtitle_revision"] = root_input_snapshot.get("subtitle_revision")
                input_snapshot["subtitle_revision_id"] = root_input_snapshot.get("subtitle_revision_id")
                input_snapshot["subtitle_burned_in"] = bool(
                    input_snapshot["applied_effects"].get(
                        "subtitle_burned",
                        root_input_snapshot.get("subtitle_burned_in", False),
                    )
                )
                input_snapshot["source_audio_policy"] = root_input_snapshot.get("source_audio_policy")
            connection.execute(
                """INSERT INTO episode_render_versions
                (id,episode_id,timeline_revision_id,rel_path,sha256,probe_json,integrity_status,duration_ms,mime_type,
                 input_snapshot_json,ffmpeg_command_json,execution_log_text,render_kind,parent_render_version_id,
                 upscale_run_id,derivation_fingerprint,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,'VERIFIED',?,'video/mp4',?,?,'','SUPER_RESOLUTION',?,?,?,?,?,?,1,'video-upscale-render.v1')""",
                (
                    render_id,
                    facts["episode_id"],
                    facts["timeline_revision_id"],
                    path.relative_to(facts["project_root"]).as_posix(),
                    output_sha256,
                    _json(dict(final_probe)),
                    int(final_probe.get("duration_ms") or 0),
                    _json(input_snapshot),
                    _json({"executor": _HANDLER_CODE, "handler_version": _HANDLER_VERSION}),
                    facts["source_render_id"],
                    run_id,
                    str(run["fingerprint"]),
                    now,
                    now,
                    "worker",
                ),
            )
            qc_run_id = str(uuid.uuid4())
            checks = [
                ("decode", "PASS", {"probe_status": final_probe.get("probe_status")}),
                *qc_checks,
                ("integrity", "PASS", {"sha256": output_sha256, "byte_size": output_size}),
            ]
            connection.execute(
                """INSERT INTO machine_check_runs
                (id,subject_type,subject_id,policy_version,status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,'EPISODE_RENDER_VERSION',?,'video-upscale-output.v1','PASS',?,?,?,1,'video-upscale-qc.v1')""",
                (qc_run_id, render_id, now, now, "worker"),
            )
            for item_id, result, details in checks:
                connection.execute(
                    """INSERT INTO machine_check_results (id,run_id,item_id,result,details_json)
                    VALUES (?,?,?,?,?)""",
                    (str(uuid.uuid4()), qc_run_id, item_id, result, _json(details)),
                )
            connection.execute(
                """UPDATE video_upscale_runs SET output_render_id=?,progress_summary_json=?,updated_at=?,revision=revision+1
                WHERE id=?""",
                (render_id, _json({"phase": "COMPLETED", "percent": 100}), now, run_id),
            )
            connection.execute(
                "UPDATE video_upscale_runs SET qc_run_id=? WHERE id=?",
                (qc_run_id, run_id),
            )
        return {"id": render_id}

    def _assert_attempt_fence(
        self,
        run_id: str,
        *,
        connection: Any | None = None,
    ) -> tuple[str, str] | None:
        """Reject writes from a superseded, expired, paused, or cancelled attempt."""
        context = self.attempt_context()
        if context is None:
            return None
        attempt_id, lease_token, worker_id = context

        def check(active_connection: Any) -> tuple[str, str]:
            row = active_connection.execute(
                """SELECT attempt.id,attempt.lease_token,attempt.worker_id,attempt.state,
                          attempt.lease_expires_at,job.state AS job_state
                   FROM video_upscale_runs run
                   JOIN jobs job ON job.id=run.job_id
                   JOIN job_attempts attempt ON attempt.job_id=job.id
                   WHERE run.id=? AND attempt.id=?""",
                (run_id, attempt_id),
            ).fetchone()
            valid = bool(
                row is not None
                and str(row["worker_id"] or "") == worker_id
                and hmac.compare_digest(str(row["lease_token"] or ""), lease_token)
                and str(row["state"]) in {"CLAIMED", "RUNNING"}
                and str(row["job_state"]) in {"CLAIMED", "RUNNING"}
                and row["lease_expires_at"]
                and datetime.fromisoformat(str(row["lease_expires_at"])) > datetime.now(UTC)
            )
            if not valid:
                raise DomainRuleError(
                    "UPSCALE_ATTEMPT_FENCED",
                    "当前超分 attempt 已失去写入权限；保留已验证分块并等待恢复",
                )
            return attempt_id, lease_token

        if connection is not None:
            return check(connection)
        with self.database.connect() as active_connection:
            return check(active_connection)

    def _check_cancel(self) -> None:
        if not self.cancel_check():
            return
        raise DomainRuleError("JOB_CANCELLED", "视频超分任务已取消，正式结果不会登记")

    def _progress(self, completed_chunks: int, chunk_count: int, completed_frames: int, frame_count: int, phase: str) -> None:
        percent = 10 + round(80 * completed_frames / max(1, frame_count))
        if self.report_progress(
            {
                "phase": phase,
                "percent": percent,
                "completed_chunks": completed_chunks,
                "total_chunks": chunk_count,
                "completed_frames": completed_frames,
                "total_frames": frame_count,
            }
        ):
            raise DomainRuleError("JOB_CANCELLED", "视频超分任务已取消，正式结果不会登记")


def make_ncnn_video_upscale_handler(
    database: Database,
    settings: Settings,
    *,
    cancel_check: Callable[[], bool] | None = None,
    report_progress: Callable[[dict[str, Any]], bool] | None = None,
    attempt_context: Callable[[], tuple[str, str, str] | None] | None = None,
) -> Callable[[WorkerExecutionSnapshot, Path], tuple[str, str]]:
    executor = NcnnVideoUpscaleExecutor(
        database,
        settings,
        cancel_check=cancel_check,
        report_progress=report_progress,
        attempt_context=attempt_context,
    )
    return executor.execute
