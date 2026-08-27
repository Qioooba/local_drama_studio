"""One-job-at-a-time local media worker backed by the persistent queue."""

from __future__ import annotations

import errno
import json
import subprocess
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
from time import monotonic
from typing import Any

from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.background_operations import BackgroundOperationService
from local_drama.application.configuration import ConfigurationService
from local_drama.application.dialogue import DialogueService
from local_drama.application.episode_front_half_actions import EpisodeFrontHalfActionService
from local_drama.application.episode_worker_actions import EpisodeWorkerActionService
from local_drama.application.generation import GenerationService
from local_drama.application.jobs import JobService
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.media import MediaService
from local_drama.application.timeline import TimelineService
from local_drama.application.worker_dispatch import WorkerExecution, WorkerJobDispatcher
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan
from local_drama.domain.policies import VariantInput
from local_drama.infrastructure.database.sqlite import Database
from local_drama.platform import create_platform_services
from local_drama.platform.contracts import TtsRuntime, TtsRuntimeError
from local_drama.infrastructure.filesystem.atomic import replace_path


# Declarative registry: job type -> business handler method name on LocalMediaWorker.
# Kept module-level so handler registration is separated from runner orchestration
# and can be audited/tested independently (design §13.2 "giant worker handler split").
_JOB_HANDLER_REGISTRY: dict[str, str] = {
    "MEDIA_DERIVATIVE": "_run_media_job",
    "MEDIA_THUMBNAIL": "_run_media_job",
    "MEDIA_PROXY": "_run_media_job",
    "TTS_GENERATION": "_run_tts_job",
    "EPISODE_COMPOSE": "_run_compose_job",
    "VIDEO_ENHANCEMENT": "_run_enhancement_job",
    "SEGMENTED_EPISODE_COMPOSE": "_run_segmented_compose_job",
    "DELIVERY_BUILD": "_run_delivery_build_job",
    "EXPERIMENT_CELL": "_run_experiment_cell",
    "LOCAL_LLM_PROBE": "_run_local_llm_probe_job",
}


class LocalMediaWorker:
    def __init__(self, database: Database, settings: Settings, tts_runtime: TtsRuntime | None = None) -> None:
        self.database = database
        self.settings = settings
        self.jobs = JobService(database, settings)
        self.media = MediaService(database, settings, ffmpeg_runner=self._ffmpeg)
        self.tts_runtime = tts_runtime or create_platform_services(settings).tts_runtime
        self._active_job_context: tuple[str, str, str] | None = None
        self._last_cancel_check = 0.0
        self._last_progress_heartbeat = 0.0
        self._active_progress: dict[str, Any] = {}
        self._ffmpeg_expected_duration_ms: int | None = None

    @staticmethod
    def _execution(result: tuple[str, str]) -> WorkerExecution:
        return WorkerExecution(kind=result[0], relative_path=result[1])

    def _run_cpu_test(self, job: dict[str, Any], output_root: Path, worker_id: str) -> WorkerExecution:
        output = output_root / "result.txt"
        self._atomic_file(output, lambda target: target.write_text(f"job={job['id']}\nworker={worker_id}\n", encoding="utf-8"))
        return WorkerExecution("TEXT_RESULT", output.relative_to(self.settings.work_root).as_posix())

    def _report_progress(self, progress: dict[str, Any], *, force: bool = False) -> bool:
        """Persist truthful, monotonic progress and return the cancel fact.

        FFmpeg can emit dozens of updates per second. The local job store does
        not need that write rate, so routine updates are coalesced while cancel
        checks can still force an immediate heartbeat/lease renewal.
        """
        if self._active_job_context is None:
            return False
        merged = {**self._active_progress, **progress}
        previous_percent = self._active_progress.get("percent")
        next_percent = merged.get("percent")
        if next_percent is not None:
            try:
                numeric = max(0, min(99, int(round(float(next_percent)))))
                if previous_percent is not None:
                    numeric = max(numeric, int(round(float(previous_percent))))
                merged["percent"] = numeric
            except (TypeError, ValueError):
                merged.pop("percent", None)
        previous_processed = self._active_progress.get("processed_ms")
        next_processed = merged.get("processed_ms")
        if next_processed is not None:
            try:
                processed = max(0, int(next_processed))
                if previous_processed is not None:
                    processed = max(processed, int(previous_processed))
                merged["processed_ms"] = processed
            except (TypeError, ValueError):
                merged.pop("processed_ms", None)
        self._active_progress = merged
        now = monotonic()
        if not force and now - self._last_progress_heartbeat < 0.5:
            return False
        self._last_progress_heartbeat = now
        attempt_id, token, worker_id = self._active_job_context
        heartbeat = self.jobs.heartbeat(
            attempt_id,
            token,
            worker_id,
            progress=dict(self._active_progress),
            lease_seconds=60,
        )
        return bool(heartbeat.get("cancel_requested"))

    def _cancel_requested(self) -> bool:
        if self._active_job_context is None:
            return False
        now = monotonic()
        if now - self._last_cancel_check < 1.0:
            return False
        self._last_cancel_check = now
        return self._report_progress(
            {"detail": "本机媒体处理中，可安全取消"},
            force=True,
        )

    def _timeline_service(self) -> TimelineService:
        timeline = TimelineService(self.database, self.settings)
        timeline.cancel_check = self._cancel_requested
        timeline.progress_callback = self._timeline_progress
        return timeline

    def _timeline_progress(self, progress: dict[str, Any]) -> None:
        step_percent = progress.get("step_percent")
        payload = {**progress, "phase": str(progress.get("phase") or "ENCODING")}
        if step_percent is not None:
            try:
                payload["percent"] = 20 + (float(step_percent) * 0.65)
            except (TypeError, ValueError):
                pass
        if self._report_progress(payload):
            raise DomainRuleError("JOB_CANCELLED", "后台任务已取消，本次 FFmpeg 输出不会登记")

    @staticmethod
    def _ffmpeg_progress_value(line: str) -> tuple[str, int | str] | None:
        key, separator, raw_value = line.strip().partition("=")
        if not separator:
            return None
        if key in {"out_time_us", "out_time_ms"}:
            try:
                # FFmpeg's historical out_time_ms field is also expressed in
                # microseconds; out_time_us is the clearer modern alias.
                return "processed_ms", max(0, int(raw_value) // 1000)
            except ValueError:
                return None
        if key == "progress":
            return "progress", raw_value
        return None

    @staticmethod
    def _retryable_error(code: str) -> bool:
        """Retry only failures that can plausibly change without new input."""
        permanent_suffixes = (
            "_INVALID",
            "_UNSUPPORTED",
            "_NOT_FOUND",
            "_STALE",
            "_MISMATCH",
            "_UNAVAILABLE",
            "_REQUIRED",
            "_NOT_CONFIRMED",
        )
        return code not in {"OUTPUT_INVALID", "JOB_CANCELLED"} and not code.endswith(permanent_suffixes)

    def _ffmpeg(self, args: list[str]) -> None:
        ffmpeg = self.settings.ffmpeg_path
        if not ffmpeg or not Path(ffmpeg).exists():
            raise DomainRuleError("FFMPEG_UNAVAILABLE", "本机 FFmpeg 不可用")
        try:
            process = subprocess.Popen(
                [ffmpeg, "-hide_banner", "-nostats", "-progress", "pipe:1", *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            deadline = monotonic() + 300
            # Keep process doubles and non-standard Popen adapters cancellable
            # even if they do not expose real pipe objects.
            process_stdout = getattr(process, "stdout", None)
            process_stderr = getattr(process, "stderr", None)
            if not hasattr(process_stdout, "readline") or not hasattr(process_stderr, "readline"):
                while True:
                    try:
                        _, stderr = process.communicate(timeout=min(1.0, max(0.1, deadline - monotonic())))
                        break
                    except subprocess.TimeoutExpired:
                        if self._cancel_requested():
                            process.terminate()
                            try:
                                process.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=3)
                            raise DomainRuleError("JOB_CANCELLED", "后台任务已取消，本次媒体输出不会登记") from None
                        if monotonic() >= deadline:
                            process.terminate()
                            process.wait(timeout=3)
                            raise DomainRuleError("MEDIA_WORKER_TIMEOUT", "本地媒体 worker 执行超时") from None
                if process.returncode != 0:
                    raise DomainRuleError(
                        "MEDIA_WORKER_FAILED",
                        "本地媒体 worker 执行失败",
                        {"stderr_redacted": (stderr or "")[-500:]},
                    )
                return

            messages: Queue[tuple[str, str]] = Queue()
            stdout_tail: deque[str] = deque(maxlen=120)
            stderr_tail: deque[str] = deque(maxlen=120)

            def pump(stream: Any, channel: str) -> None:
                for line in iter(stream.readline, ""):
                    messages.put((channel, line))
                messages.put((channel, ""))

            readers = [
                threading.Thread(target=pump, args=(process_stdout, "stdout"), daemon=True),
                threading.Thread(target=pump, args=(process_stderr, "stderr"), daemon=True),
            ]
            for reader in readers:
                reader.start()
            open_streams = 2
            expected_ms = self._ffmpeg_expected_duration_ms
            last_processed_ms = 0
            while True:
                try:
                    channel, line = messages.get(timeout=0.2)
                    if not line:
                        open_streams -= 1
                    elif channel == "stdout":
                        stdout_tail.append(line)
                        parsed = self._ffmpeg_progress_value(line)
                        if parsed and parsed[0] == "processed_ms":
                            last_processed_ms = max(last_processed_ms, int(parsed[1]))
                        if parsed and parsed[0] == "progress":
                            progress: dict[str, Any] = {
                                "phase": "ENCODING",
                                "processed_ms": last_processed_ms,
                            }
                            if expected_ms and expected_ms > 0:
                                step_percent = min(100, round(last_processed_ms * 100 / expected_ms))
                                progress.update(
                                    {
                                        "total_ms": expected_ms,
                                        "step_percent": step_percent,
                                        "percent": 10 + step_percent * 0.8,
                                    }
                                )
                            if self._report_progress(progress):
                                raise DomainRuleError("JOB_CANCELLED", "后台任务已取消，本次媒体输出不会登记")
                    else:
                        stderr_tail.append(line)
                except Empty:
                    pass
                if self._cancel_requested():
                    raise DomainRuleError("JOB_CANCELLED", "后台任务已取消，本次媒体输出不会登记")
                if monotonic() >= deadline:
                    raise DomainRuleError("MEDIA_WORKER_TIMEOUT", "本地媒体 worker 执行超时")
                if process.poll() is not None and open_streams <= 0:
                    break
            process.wait(timeout=3)
            for reader in readers:
                reader.join(timeout=1)
            stderr = "".join(stderr_tail)
        except DomainRuleError:
            process_state = getattr(process, "poll", lambda: getattr(process, "returncode", None))() if "process" in locals() else 0
            if "process" in locals() and process_state is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            raise
        except OSError as error:
            raise DomainRuleError("MEDIA_WORKER_FAILED", "本地媒体 worker 执行失败", {"reason": type(error).__name__}) from error
        if process.returncode != 0:
            raise DomainRuleError("MEDIA_WORKER_FAILED", "本地媒体 worker 执行失败", {"stderr_redacted": stderr[-500:]})

    def _atomic_file(self, path: Path, writer: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f".partial-{path.name}")
        try:
            writer(partial)
            replace_path(partial, path)
        except Exception:
            if partial.exists():
                partial.unlink()
            raise

    def _run_media_job(self, job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        snapshot = job["input_snapshot"]
        media_version_id = str(snapshot.get("media_version_id", ""))
        if not media_version_id:
            raise DomainRuleError("JOB_INPUT_INVALID", "媒体 Job 缺少 media_version_id")
        item, source = self.media.content_path(media_version_id)
        self._ffmpeg_expected_duration_ms = int(item.get("duration_ms") or 0) or None
        if job["type"] == "MEDIA_DERIVATIVE":
            derivative_kind = str(snapshot.get("kind") or "").upper()
            if str(snapshot.get("source_sha256") or "") != str(item["sha256"]):
                raise DomainRuleError("MEDIA_DERIVATIVE_SOURCE_STALE", "媒体派生任务的源版本指纹已变化")
            if derivative_kind == "THUMBNAIL":
                path, mime = self.media.thumbnail(
                    media_version_id,
                    str(snapshot.get("size") or "small"),
                    str(snapshot.get("frame") or "poster"),
                )
            elif derivative_kind == "FILMSTRIP":
                path, mime = self.media.filmstrip(media_version_id)
            elif derivative_kind == "WAVEFORM":
                path, mime = self.media.waveform(media_version_id)
            elif derivative_kind == "PROXY":
                path, mime = self.media.proxy(media_version_id)
            else:
                raise DomainRuleError("MEDIA_DERIVATIVE_KIND_INVALID", "媒体派生任务类型无效", {"kind": derivative_kind})
            cache_root = self.settings.cache_root.resolve()
            resolved = path.resolve()
            if not resolved.is_relative_to(cache_root) or not resolved.is_file() or resolved.is_symlink():
                raise DomainRuleError("MEDIA_DERIVATIVE_OUTPUT_INVALID", "媒体派生输出未安全落入缓存目录")
            report = {
                "schema_version": "localdrama.media-derivative-report.v1",
                "job_id": str(job["id"]),
                "media_version_id": media_version_id,
                "source_sha256": str(item["sha256"]),
                "kind": derivative_kind,
                "cache_rel_path": resolved.relative_to(cache_root).as_posix(),
                "mime_type": mime,
                "byte_size": resolved.stat().st_size,
                "local_only": True,
                "network_contacted": False,
            }
            output = output_root / "media-derivative-report.json"
            self._atomic_file(output, lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"))
            return "MEDIA_DERIVATIVE_REPORT", output.relative_to(self.settings.work_root).as_posix()
        if item["media_kind"] != "VIDEO":
            raise DomainRuleError("MEDIA_WORKER_INPUT_UNSUPPORTED", "当前 worker 只处理视频媒体")
        if job["type"] == "MEDIA_THUMBNAIL":
            output = output_root / "thumbnail.webp"
            self._atomic_file(
                output, lambda target: self._ffmpeg(["-i", str(source), "-frames:v", "1", "-vf", "scale=320:-1", "-c:v", "libwebp", "-y", str(target)])
            )
            return "THUMBNAIL", output.relative_to(self.settings.work_root).as_posix()
        if job["type"] == "MEDIA_PROXY":
            output = output_root / "proxy.mp4"
            self._atomic_file(
                output,
                lambda target: self._ffmpeg(
                    [
                        "-i",
                        str(source),
                        "-vf",
                        "scale=480:-2",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-pix_fmt",
                        "yuv420p",
                        "-an",
                        "-movflags",
                        "+faststart",
                        "-y",
                        str(target),
                    ]
                ),
            )
            probe = self.media._probe(output, "VIDEO")
            if probe.get("probe_status") != "PASS":
                raise DomainRuleError("OUTPUT_INVALID", "proxy 输出无法通过 ffprobe")
            return "PROXY_VIDEO", output.relative_to(self.settings.work_root).as_posix()
        raise DomainRuleError("JOB_TYPE_UNSUPPORTED", "当前 worker 不支持该媒体 Job 类型", {"type": job["type"]})

    def _run_local_llm_probe_job(self, job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        snapshot = job["input_snapshot"]
        credential_source = snapshot.get("credential_source")
        if snapshot.get("secret_persisted") is not False or credential_source not in {"SETTINGS_OR_ENV", "PROVIDER_CONNECTION"}:
            raise DomainRuleError("LOCAL_LLM_PROBE_SNAPSHOT_INVALID", "LLM 测试 Job 的密钥边界无效")
        if self._cancel_requested():
            raise DomainRuleError("JOB_CANCELLED", "LLM 连接测试已取消")
        self._report_progress({"phase": "PROBING_RUNTIME", "percent": 25}, force=True)
        probe = LocalLLMService(self.database, self.settings).client(
            model=str(snapshot.get("model") or ""),
            provider=str(snapshot.get("provider") or ""),
            base_url=str(snapshot.get("base_url") or ""),
            provider_connection_id=str(snapshot.get("provider_connection_id") or "") or None,
        ).probe(load_test=bool(snapshot.get("load_test", True)))
        self._report_progress({"phase": "RECORDING_EVIDENCE", "percent": 85}, force=True)
        report = {
            "schema_version": "localdrama.local-llm-probe-report.v1",
            "job_id": str(job["id"]),
            "probe": probe,
            "secret_persisted": False,
            "credential_source": credential_source,
            "provider_connection_id": snapshot.get("provider_connection_id"),
        }
        output = output_root / "local-llm-probe-report.json"
        self._atomic_file(output, lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"))
        return "LOCAL_LLM_PROBE_REPORT", output.relative_to(self.settings.work_root).as_posix()

    def _run_experiment_cell(self, job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        """Turn one immutable matrix cell into a real child Variant + GPU Job.

        The CPU cell is orchestration, not a fake generation success.  The
        experiment cell is rebound to the child generation Job so cancellation,
        terminal state and Canvas progress continue to follow actual execution.
        """
        snapshot = job["input_snapshot"]
        experiment_id = str(snapshot.get("experiment_id") or "")
        cell_key = str(snapshot.get("cell_key") or "")
        parameters = snapshot.get("parameters")
        if not experiment_id or not cell_key or not isinstance(parameters, dict) or not parameters:
            raise DomainRuleError("EXPERIMENT_CELL_INPUT_INVALID", "实验 cell 缺少冻结计划或参数")

        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT ec.id AS cell_id, ec.job_id AS cell_job_id, ec.status AS cell_status,
                ge.status AS experiment_status, ge.created_at AS experiment_created_at,
                ge.axis_definitions_json, ge.intent_id, gi.project_id
                FROM experiment_cells ec
                JOIN generation_experiments ge ON ge.id=ec.experiment_id
                JOIN generation_intents gi ON gi.id=ge.intent_id
                WHERE ec.experiment_id=? AND ec.cell_key=?""",
                (experiment_id, cell_key),
            ).fetchone()
        if row is None:
            raise DomainRuleError("EXPERIMENT_CELL_NOT_FOUND", "实验 cell 不存在")
        if str(row["experiment_status"]) != "CONFIRMED":
            raise DomainRuleError("EXPERIMENT_NOT_CONFIRMED", "实验已取消或尚未确认，禁止继续派生")
        if str(row["cell_job_id"]) != str(job["id"]):
            # A previous Attempt already dispatched the immutable child.  The
            # parent retry must not create another Variant.
            with self.database.connect() as connection:
                existing_child = connection.execute(
                    "SELECT id, subject_id, state FROM jobs WHERE id=? AND subject_type='GENERATION_VARIANT'",
                    (row["cell_job_id"],),
                ).fetchone()
            if existing_child is None:
                raise DomainRuleError("EXPERIMENT_CELL_LINEAGE_INVALID", "实验 cell 的子任务谱系无效")
            result = {
                "experiment_id": experiment_id,
                "cell_id": str(row["cell_id"]),
                "cell_key": cell_key,
                "variant_id": str(existing_child["subject_id"]),
                "generation_job_id": str(existing_child["id"]),
                "generation_job_state": str(existing_child["state"]),
                "idempotent_replay": True,
            }
        else:
            try:
                axes_plan = json.loads(str(row["axis_definitions_json"] or "{}"))
            except (TypeError, json.JSONDecodeError) as error:
                raise DomainRuleError("EXPERIMENT_PLAN_INVALID", "实验冻结计划无法解析") from error
            base_variant_id = str(snapshot.get("base_variant_id") or axes_plan.get("base_variant_id") or "")
            if not base_variant_id:
                # Compatibility for plans authored before base_variant_id was
                # frozen: resolve the newest Variant that already existed when
                # the experiment itself was created.  This is deterministic and
                # excludes later branches.
                with self.database.connect() as connection:
                    base_row = connection.execute(
                        """SELECT id FROM generation_variants
                        WHERE intent_id=? AND created_at<=?
                        ORDER BY created_at DESC, variant_no DESC, id DESC LIMIT 1""",
                        (row["intent_id"], row["experiment_created_at"]),
                    ).fetchone()
                base_variant_id = str(base_row["id"]) if base_row is not None else ""
            if not base_variant_id:
                raise DomainRuleError(
                    "EXPERIMENT_BASE_VARIANT_REQUIRED",
                    "实验矩阵必须基于一个已提交的 GenerationVariant；请先完成生成预检与提交",
                )

            generation = GenerationService(self.database, self.settings)
            base = generation.get_variant(base_variant_id)
            if str(base["intent_id"]) != str(row["intent_id"]):
                raise DomainRuleError("EXPERIMENT_BASE_VARIANT_MISMATCH", "实验冻结的基础 Variant 不属于当前 Intent")
            parameter_set = json.loads(str(base["parameter_set_json"] or "{}"))
            if not isinstance(parameter_set, dict):
                raise DomainRuleError("EXPERIMENT_BASE_PARAMETERS_INVALID", "基础 Variant 参数快照无效")
            changed_keys: list[str] = []
            for raw_key, value in parameters.items():
                key = str(raw_key).strip()
                if not key:
                    raise DomainRuleError("EXPERIMENT_AXIS_INVALID", "实验轴名称不能为空")
                target_key = "SEED" if key.casefold() == "seed" else next(
                    (existing for existing in parameter_set if str(existing).casefold() == key.casefold()),
                    key,
                )
                if parameter_set.get(target_key) != value:
                    changed_keys.append(target_key)
                parameter_set[target_key] = value

            seed_policy = str(base["seed_policy"])
            explicit_seed = int(base["explicit_seed"]) if base.get("explicit_seed") is not None else None
            if "SEED" in parameter_set:
                if seed_policy != "EXPLICIT":
                    raise DomainRuleError("EXPERIMENT_SEED_POLICY_UNSUPPORTED", "Provider random 基础 Variant 不能运行显式 seed 实验轴")
                try:
                    explicit_seed = int(parameter_set["SEED"])
                except (TypeError, ValueError) as error:
                    raise DomainRuleError("EXPERIMENT_SEED_INVALID", "实验 seed 必须是整数") from error
                parameter_set["SEED"] = explicit_seed
            variant_type = (
                "RESAMPLE_NEW_SEED"
                if set(changed_keys) == {"SEED"} and explicit_seed != base.get("explicit_seed")
                else "PARAMETER_BRANCH"
            )
            bindings = tuple(
                VariantInput(
                    str(item["role"]),
                    str(item["media_version_id"]),
                    int(item["ordinal"]),
                    float(item["weight"]) if item.get("weight") is not None else None,
                )
                for item in base["bindings"]
            )
            plan = VariantPlan(
                variant_type=variant_type,
                parent_variant_id=base_variant_id,
                branch_reason=f"EXPERIMENT_CELL:{experiment_id}:{cell_key}",
                prompt_revision_id=str(base["prompt_revision_id"]) if base.get("prompt_revision_id") else None,
                profile_version_id=str(base["capability_profile_version_id"]),
                parameter_set=parameter_set,
                seed_policy=seed_policy,
                explicit_seed=explicit_seed,
                bindings=bindings,
                provider_random_nonce=str(base["provider_random_nonce"]) if base.get("provider_random_nonce") else None,
            )
            child_key = f"experiment:{experiment_id}:generation:{cell_key}"
            with self.database.connect() as connection:
                existing_child = connection.execute(
                    "SELECT id, subject_id, state FROM jobs WHERE project_id=? AND idempotency_key=?",
                    (row["project_id"], child_key),
                ).fetchone()
            if existing_child is None:
                preflight = generation.preflight_variant(str(row["intent_id"]), plan)
                submitted = generation.submit_confirmed_variant(
                    str(row["intent_id"]), plan, str(preflight["plan_hash"]), child_key
                )
                child = submitted["job"]
                variant_id = str(submitted["variant"]["id"])
                replay = False
            else:
                child = dict(existing_child)
                variant_id = str(existing_child["subject_id"])
                replay = True
            with self.database.transaction() as connection:
                updated = connection.execute(
                    """UPDATE experiment_cells SET variant_id=?, job_id=?, status=?
                    WHERE id=? AND job_id=? AND status NOT IN ('CANCELLED','CANCEL_REQUESTED')""",
                    (variant_id, child["id"], str(child["state"]), row["cell_id"], job["id"]),
                )
                if updated.rowcount != 1:
                    raise DomainRuleError("EXPERIMENT_CELL_STALE", "实验 cell 已被取消或由其他 Worker 派生")
                connection.execute(
                    """INSERT INTO outbox_events (type, project_id, subject_type, subject_id, payload_json)
                    VALUES ('EXPERIMENT_CELL_DISPATCHED', ?, 'EXPERIMENT_CELL', ?, ?)""",
                    (
                        row["project_id"],
                        row["cell_id"],
                        json.dumps(
                            {"experiment_id": experiment_id, "variant_id": variant_id, "job_id": child["id"]},
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
            result = {
                "experiment_id": experiment_id,
                "cell_id": str(row["cell_id"]),
                "cell_key": cell_key,
                "base_variant_id": base_variant_id,
                "variant_id": variant_id,
                "generation_job_id": str(child["id"]),
                "generation_job_state": str(child["state"]),
                "parameters": parameters,
                "idempotent_replay": replay,
            }

        output = output_root / "experiment-cell.json"
        self._atomic_file(
            output,
            lambda target: target.write_text(
                json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            ),
        )
        return "EXPERIMENT_CELL_REPORT", output.relative_to(self.settings.work_root).as_posix()

    def _run_tts_job(self, job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        snapshot = job["input_snapshot"]
        text_revision_id = str(snapshot.get("text_revision_id", ""))
        voice_profile_version_id = str(snapshot.get("voice_profile_version_id", ""))
        with self.database.connect() as connection:
            text_revision = connection.execute(
                """SELECT dtr.*,s.project_id FROM dialogue_text_revisions dtr
                JOIN dialogue_lines dl ON dl.id=dtr.dialogue_line_id JOIN episodes e ON e.id=dl.episode_id
                JOIN seasons s ON s.id=e.season_id WHERE dtr.id=?""",
                (text_revision_id,),
            ).fetchone()
            voice = connection.execute("SELECT * FROM voice_profile_versions WHERE id=?", (voice_profile_version_id,)).fetchone()
            profile = connection.execute(
                "SELECT * FROM execution_profile_versions WHERE id=?",
                (job["execution_profile_version_id"],),
            ).fetchone()
        if (
            text_revision is None
            or voice is None
            or profile is None
            or job["subject_type"] != "DIALOGUE_TEXT_REVISION"
            or str(job["subject_id"]) != text_revision_id
            or str(text_revision["project_id"]) != str(job["project_id"])
            or str(voice["project_id"]) != str(job["project_id"])
            or voice["status"] != "ACTIVE"
            or str(voice["provider_profile_version_id"]) != str(profile["id"])
            or profile["status"] != "PUBLISHED"
            or "TTS" not in str(profile["capability"]).upper()
            or str(snapshot.get("provider_kind")) != "WINDOWS_SAPI_LOCAL"
            or snapshot.get("network_allowed") is not False
            or str(snapshot.get("text_hash")) != str(text_revision["text_hash"])
            or str(snapshot.get("text")) != str(text_revision["text"])
            or str(snapshot.get("voice_ref")) != str(voice["voice_ref"])
        ):
            raise DomainRuleError("TTS_JOB_SNAPSHOT_INVALID", "TTS Job 快照与最新持久化文本、音色或 Published Profile 不匹配")
        voice_ref = str(voice["voice_ref"])
        if not voice_ref.startswith("sapi:") or not voice_ref.removeprefix("sapi:").strip():
            raise DomainRuleError("TTS_VOICE_REF_INVALID", "Windows SAPI Job 必须使用 sapi: 音色引用")
        output = output_root / "speech.wav"
        sapi_output = output_root / "speech.sapi.wav"
        speech_rate = float(snapshot.get("speech_rate", 1.0))
        sapi_rate = max(-10, min(10, round((speech_rate - 1.0) * 10)))
        def synthesize(target: Path) -> None:
            try:
                self.tts_runtime.synthesize(
                    voice=voice_ref.removeprefix("sapi:").strip(),
                    text=str(text_revision["text"]),
                    output=target,
                    rate=sapi_rate,
                    timeout=120,
                )
            except TtsRuntimeError as error:
                raise DomainRuleError("TTS_RUNTIME_FAILED", "本机 SAPI TTS 执行失败", {"reason": type(error).__name__}) from error

        self._atomic_file(sapi_output, synthesize)
        try:
            # SAPI can peak just above the project's -1 dBFS safety ceiling.
            # Freeze deterministic local headroom into the actual Job artifact
            # instead of asking a reviewer to approve a technically failing WAV.
            self._atomic_file(
                output,
                lambda target: self._ffmpeg(
                    [
                        "-i",
                        str(sapi_output),
                        "-filter:a",
                        "volume=-1.5dB",
                        "-c:a",
                        "pcm_s16le",
                        "-y",
                        str(target),
                    ]
                ),
            )
        finally:
            sapi_output.unlink(missing_ok=True)
        probe = self.media._probe(output, "AUDIO")
        try:
            duration_ms = round(float(probe.get("format", {}).get("duration")) * 1000)
        except (TypeError, ValueError):
            duration_ms = None
        if probe.get("probe_status") != "PASS" or duration_ms is None or duration_ms <= 0:
            raise DomainRuleError("TTS_OUTPUT_INVALID", "SAPI 输出未通过本机 FFprobe")
        return "TTS_AUDIO", output.relative_to(self.settings.work_root).as_posix()

    def _run_compose_job(self, job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        snapshot = job["input_snapshot"]
        timeline_revision_id = str(snapshot.get("timeline_revision_id") or "")
        expected = str(snapshot.get("compose_fingerprint") or "")
        if job["subject_type"] != "TIMELINE_REVISION" or str(job["subject_id"]) != timeline_revision_id or not expected:
            raise DomainRuleError("COMPOSE_JOB_SNAPSHOT_INVALID", "Compose Job 缺少不可变 timeline/fingerprint 输入")
        timeline = self._timeline_service()
        current = timeline.preflight_episode_render(timeline_revision_id)
        if str(current["compose_fingerprint"]) != expected:
            raise DomainRuleError(
                "COMPOSE_INPUT_STALE", "Compose 入队后输入已变化；请重新预检并提交",
                {"expected_fingerprint": expected, "current_fingerprint": current["compose_fingerprint"]},
            )
        render = timeline.render_episode(
            timeline_revision_id, force_rerender=bool(snapshot.get("force_rerender")), actor="compose-worker",
        )
        report = {
            "schema_version": "localdrama.episode-compose-report.v1", "job_id": str(job["id"]),
            "timeline_revision_id": timeline_revision_id, "compose_fingerprint": expected,
            "render_version_id": str(render["id"]), "render_sha256": str(render["sha256"]),
            "idempotent_render_replay": bool(render.get("idempotent_replay")),
            "local_only": True, "network_contacted": False,
        }
        output = output_root / "compose-report.json"
        self._atomic_file(output, lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"))
        return "EPISODE_COMPOSE_REPORT", output.relative_to(self.settings.work_root).as_posix()

    def _run_enhancement_job(self, job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        snapshot = job["input_snapshot"]
        media_version_id = str(snapshot.get("input_media_version_id") or "")
        recipe_id = str(snapshot.get("recipe_id") or "")
        plan_hash = str(snapshot.get("plan_hash") or "")
        parameters = snapshot.get("parameters")
        if (
            job["subject_type"] != "MEDIA_VERSION"
            or str(job["subject_id"]) != media_version_id
            or not recipe_id
            or not plan_hash
            or not isinstance(parameters, dict)
        ):
            raise DomainRuleError("ENHANCEMENT_JOB_SNAPSHOT_INVALID", "增强 Job 缺少不可变媒体、配方或计划输入")
        timeline = self._timeline_service()
        current = timeline.plan_enhancement(media_version_id, recipe_id, parameters)
        if str(current["plan_hash"]) != plan_hash:
            raise DomainRuleError("ENHANCEMENT_PLAN_STALE", "增强入队后输入或配方已变化，请重新预检并提交")
        enhancement = timeline.run_enhancement(
            media_version_id,
            recipe_id,
            plan_hash,
            parameters,
            actor="enhancement-worker",
        )
        report = {
            "schema_version": "localdrama.enhancement-job-report.v1",
            "job_id": str(job["id"]),
            "enhancement_run_id": str(enhancement["id"]),
            "output_media_version_id": str(enhancement["output_media_version_id"]),
            "output_sha256": str(enhancement["output_sha256"]),
            "qc_passed": bool(enhancement.get("qc", {}).get("passed")),
            "local_only": True,
            "network_contacted": False,
        }
        output = output_root / "enhancement-report.json"
        self._atomic_file(output, lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"))
        return "VIDEO_ENHANCEMENT_REPORT", output.relative_to(self.settings.work_root).as_posix()

    def _run_segmented_compose_job(self, job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        snapshot = job["input_snapshot"]
        timeline_revision_id = str(snapshot.get("timeline_revision_id") or "")
        segments = snapshot.get("segments")
        expected = str(snapshot.get("compose_fingerprint") or "")
        if (
            job["subject_type"] != "TIMELINE_REVISION"
            or str(job["subject_id"]) != timeline_revision_id
            or not isinstance(segments, list)
            or not expected
        ):
            raise DomainRuleError("SEGMENTED_COMPOSE_JOB_SNAPSHOT_INVALID", "分段合成 Job 缺少不可变时间线或分段输入")
        timeline = self._timeline_service()
        current = timeline.preflight_segmented_episode_render(timeline_revision_id, segments)
        if str(current["compose_fingerprint"]) != expected:
            raise DomainRuleError("COMPOSE_INPUT_STALE", "分段合成入队后输入已变化，请重新预检并提交")
        render = timeline.render_segmented_episode(
            timeline_revision_id,
            segments,
            force_rerender=bool(snapshot.get("force_rerender")),
            actor="segmented-compose-worker",
        )
        report = {
            "schema_version": "localdrama.segmented-compose-report.v1",
            "job_id": str(job["id"]),
            "timeline_revision_id": timeline_revision_id,
            "compose_fingerprint": expected,
            "render_version_id": str(render["id"]),
            "render_sha256": str(render["sha256"]),
            "local_only": True,
            "network_contacted": False,
        }
        output = output_root / "segmented-compose-report.json"
        self._atomic_file(output, lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"))
        return "SEGMENTED_EPISODE_COMPOSE_REPORT", output.relative_to(self.settings.work_root).as_posix()

    def _run_delivery_build_job(self, job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        snapshot = job["input_snapshot"]
        render_id = str(snapshot.get("episode_render_version_id") or "")
        target_version_id = str(snapshot.get("target_version_id") or "")
        expected = str(snapshot.get("delivery_fingerprint") or "")
        if (
            job["subject_type"] != "EPISODE_RENDER_VERSION"
            or str(job["subject_id"]) != render_id
            or not target_version_id
            or not expected
        ):
            raise DomainRuleError("DELIVERY_JOB_SNAPSHOT_INVALID", "交付 Job 缺少不可变渲染或目标版本输入")
        operations = BackgroundOperationService(self.database, self.settings)
        current = operations.delivery_plan(
            render_id,
            target_version_id,
            snapshot.get("brand_kit_id"),
            snapshot.get("watermark_profile_id"),
            snapshot.get("compliance_policy_id"),
        )
        if str(current["fingerprint"]) != expected:
            raise DomainRuleError("DELIVERY_INPUT_STALE", "交付入队后渲染、目标或批准状态已变化，请重新提交")
        delivery = self._timeline_service().build_delivery(
            render_id,
            target_version_id,
            snapshot.get("brand_kit_id"),
            snapshot.get("watermark_profile_id"),
            snapshot.get("compliance_policy_id"),
            actor="delivery-worker",
        )
        report = {
            "schema_version": "localdrama.delivery-build-job-report.v1",
            "job_id": str(job["id"]),
            "delivery_package_id": str(delivery["id"]),
            "manifest_sha256": str(delivery.get("manifest_sha256") or ""),
            "status": str(delivery["status"]),
            "local_only": True,
            "network_contacted": False,
        }
        output = output_root / "delivery-build-report.json"
        self._atomic_file(output, lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"))
        return "DELIVERY_BUILD_REPORT", output.relative_to(self.settings.work_root).as_posix()

    def _run_script_breakdown_job(
        self,
        job: dict[str, Any],
        output_root: Path,
        on_progress: Any,
    ) -> tuple[str, str]:
        snapshot = job["input_snapshot"]
        session_id = str(snapshot.get("import_session_id") or "")
        profile_version_id = str(snapshot.get("profile_version_id") or "")
        if (
            job["subject_type"] != "IMPORT_SESSION"
            or str(job["subject_id"]) != session_id
            or str(job.get("execution_profile_version_id") or "") != profile_version_id
            or snapshot.get("automatic_apply") is not False
            or snapshot.get("requires_human_action") is not True
        ):
            raise DomainRuleError("LOCAL_LLM_JOB_SNAPSHOT_INVALID", "AI 拆解 Job 缺少不可变源/Profile/人工审核安全快照")
        result = LocalLLMService(self.database, self.settings).breakdown(
            session_id,
            profile_version_id,
            job_id=str(job["id"]),
            input_snapshot=snapshot,
            on_progress=on_progress,
        )
        scenes = result.get("draft", {}).get("scenes", [])
        scene_count = len(scenes) if isinstance(scenes, list) else 0
        shot_count = sum(
            len(scene.get("shots", []))
            for scene in scenes
            if isinstance(scene, dict) and isinstance(scene.get("shots", []), list)
        )
        report = {
            "schema_version": "localdrama.script-breakdown-job-report.v1",
            "job_id": str(job["id"]),
            "draft_id": str(result["id"]),
            "draft_status": str(result["status"]),
            "scene_count": scene_count,
            "shot_count": shot_count,
            "idempotent_replay": bool(result.get("idempotent_replay")),
            "automatic_apply": False,
            "requires_human_action": True,
            "local_only": True,
            "remote_provider_contacted": False,
        }
        output = output_root / "script-breakdown-report.json"
        self._atomic_file(
            output,
            lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"),
        )
        on_progress({"phase": "DRAFT_READY", "percent": 100, "draft_id": str(result["id"])})
        return "SCRIPT_BREAKDOWN_REPORT", output.relative_to(self.settings.work_root).as_posix()

    # ------------------------------------------------------------------
    # Declarative automation task executor (AUTOMATION_WORKFLOW_TASK).
    #
    # The workflow engine persists each batch item as a durable Job; the Job
    # input_snapshot intentionally carries no payload, so the executor reloads
    # the item payload from automation_workflow_run_tasks.item_json by task id.
    # Every action writes one JSON report artifact (kind AUTOMATION_TASK_REPORT)
    # and completes the Job successfully; the machine_check.status in the
    # report then drives the workflow run forward through step_run, which is
    # the only place HITL pauses/limits are decided.  Known business outcomes
    # (missing keyframes, missing timeline, missing delivery target, render
    # errors, ...) become report statuses; unexpected input problems raise
    # DomainRuleError so the Job itself fails like every other worker Job.
    # ------------------------------------------------------------------

    @staticmethod
    def _automation_report(status: str, machine_check: dict[str, Any], produced: dict[str, Any], summary: str) -> dict[str, Any]:
        return {"status": status, "machine_check": machine_check, "produced": produced, "summary": summary}

    @staticmethod
    def _automation_failure(code: str, detail: str) -> dict[str, Any]:
        machine_check = {"status": "FAIL", "ok": False, "code": code, "detail": detail}
        return {"status": "FAIL", "machine_check": machine_check, "produced": {}, "summary": detail}

    def _automation_episode(self, episode_id: str) -> Any:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT e.id, e.code, s.project_id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=?",
                (episode_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
        return row

    def _automation_keyframe_check(self, episode_id: str) -> tuple[dict[str, Any], int]:
        with self.database.connect() as connection:
            shots = connection.execute(
                "SELECT id, code FROM shots WHERE episode_id=? ORDER BY CAST(order_key AS REAL), code",
                (episode_id,),
            ).fetchall()
            missing: list[dict[str, str]] = []
            for shot in shots:
                approved = connection.execute(
                    """SELECT 1 FROM media_assets ma JOIN media_versions mv ON mv.id=ma.approved_version_id
                    WHERE ma.owner_type='SHOT' AND ma.owner_id=? AND ma.purpose='KEYFRAME'
                    AND ma.media_kind='IMAGE' AND mv.stage='KEYFRAME' AND mv.integrity_status='VERIFIED'
                    LIMIT 1""",
                    (str(shot["id"]),),
                ).fetchone()
                if approved is None:
                    missing.append({"shot_id": str(shot["id"]), "shot_code": str(shot["code"])})
        if missing:
            machine_check: dict[str, Any] = {"status": "NEEDS_HITL", "ok": False, "checked_shots": len(shots), "missing_shots": missing}
            summary = f"{len(missing)} 个镜头缺少已批准关键帧，等待人工确认"
        else:
            machine_check = {"status": "PASS", "ok": True, "checked_shots": len(shots), "missing_shots": []}
            summary = "全部镜头关键帧已有人工批准"
        report = self._automation_report(str(machine_check["status"]), machine_check, {"checked_shots": len(shots), "missing_shot_count": len(missing)}, summary)
        return report, 0

    def _automation_tts_batch(self, episode_id: str, run_id: str, task_id: str) -> tuple[dict[str, Any], int]:
        dialogue = DialogueService(self.database, self.settings, jobs=self.jobs, media=self.media)
        prefix = f"automation:{run_id}:{task_id}"
        result = dialogue.submit_episode_tts_batch(episode_id, idempotency_key_prefix=prefix, actor="local-user")
        counts = {key: int(result["counts"].get(key, 0)) for key in ("submitted", "skipped", "failed")}
        machine_check = {"status": "PASS", "ok": True, "counts": counts, "job_count": counts["submitted"]}
        produced = {
            "counts": counts,
            "submitted": [
                {"line_id": str(item["line_id"]), "code": str(item["code"]), "job_id": str(item["job_id"])}
                for item in result["submitted"]
            ],
        }
        summary = f"整集 TTS 批量提交完成：提交 {counts['submitted']}、跳过 {counts['skipped']}、失败 {counts['failed']}"
        return self._automation_report("PASS", machine_check, produced, summary), 0

    def _automation_render(self, episode_id: str) -> tuple[dict[str, Any], int]:
        with self.database.connect() as connection:
            timeline = connection.execute(
                "SELECT id, revision_no, status FROM timeline_revisions WHERE episode_id=? ORDER BY revision_no DESC LIMIT 1",
                (episode_id,),
            ).fetchone()
        if timeline is None:
            return self._automation_failure("RENDER_NO_TIMELINE", "该集还没有时间线 revision，无法渲染"), 0
        timeline_service = self._timeline_service()
        try:
            render = timeline_service.render_episode(str(timeline["id"]), actor="local-user")
        except DomainRuleError as error:
            return self._automation_failure(error.code, error.message), 0
        byte_size = int(render.get("byte_size") or 0)
        probe = render.get("probe") or {}
        machine_check = {
            "status": "PASS",
            "ok": True,
            "render_version_id": str(render["id"]),
            "timeline_revision_id": str(render["timeline_revision_id"]),
            "sha256": str(render["sha256"]),
            "byte_size": byte_size,
            "duration_ms": probe.get("duration_ms"),
        }
        report = self._automation_report(
            "PASS",
            machine_check,
            {"render_version_id": str(render["id"]), "rel_path": str(render["rel_path"]), "byte_size": byte_size},
            "整集渲染完成并登记 episode_render_versions",
        )
        return report, byte_size

    def _automation_delivery(self, episode_id: str) -> tuple[dict[str, Any], int]:
        episode = self._automation_episode(episode_id)
        project_id = str(episode["project_id"])
        snapshot = ConfigurationService(self.database).inspect_project_configuration(project_id)
        target_version_id = snapshot.get("selected_delivery_target_version_id")
        if not target_version_id:
            machine_check = {"status": "SKIPPED", "ok": False, "code": "DELIVERY_NO_TARGET", "detail": "项目未选定交付目标版本，交付跳过"}
            return self._automation_report("SKIPPED", machine_check, {}, "交付跳过：项目未选定交付目标版本"), 0
        with self.database.connect() as connection:
            render = connection.execute(
                "SELECT id FROM episode_render_versions WHERE episode_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                (episode_id,),
            ).fetchone()
        if render is None:
            return self._automation_failure("DELIVERY_NO_RENDER", "该集还没有整集渲染版本，无法构建交付包"), 0
        timeline_service = self._timeline_service()
        try:
            package = timeline_service.build_delivery(str(render["id"]), str(target_version_id), actor="local-user")
        except DomainRuleError as error:
            return self._automation_failure(error.code, error.message), 0
        machine_check = {
            "status": "PASS",
            "ok": True,
            "delivery_package_id": str(package["id"]),
            "target_version_id": str(target_version_id),
            "rel_path": str(package.get("rel_path") or ""),
            "manifest_sha256": str(package.get("manifest_sha256") or ""),
            "package_status": str(package.get("status") or ""),
        }
        report = self._automation_report("PASS", machine_check, {"delivery_package_id": str(package["id"]), "rel_path": str(package.get("rel_path") or "")}, "交付包构建完成（机器预检 PASS，人工/平台审签仍为 PENDING）")
        return report, 0

    def _automation_subtitle(self, episode_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """Placeholder-capable SUBTITLE action (not used by the v1 template).

        Cues are derived from the episode dialogue lines in order; timing is an
        estimated speaking-rate projection because the executor never fabricates
        ASR alignment.  The script-authority check in create_subtitle_revision
        still guards the text, so non-verbatim derived text fails closed with
        SUBTITLE_SCRIPT_AUTHORITY_MISMATCH instead of silently writing subtitles.
        """
        dialogue = DialogueService(self.database, self.settings, jobs=self.jobs, media=self.media)
        lines = dialogue.list_lines(episode_id)
        cues: list[dict[str, Any]] = []
        cursor_us = 0
        for line in lines:
            revisions = line.get("text_revisions") or []
            if not revisions:
                continue
            text = str(revisions[-1]["text"]).strip()
            if not text:
                continue
            duration_us = max(1_000_000, len(text) * 200_000)
            cues.append({"start_us": cursor_us, "end_us": cursor_us + duration_us, "text": text})
            cursor_us += duration_us
        if not cues:
            return self._automation_failure("SUBTITLE_NO_DIALOGUE", "该集没有可派生字幕的对白"), 0
        authority = {
            "text_authority": "SCRIPT",
            "source_document_version_id": str(payload.get("source_document_version_id") or ""),
        }
        timeline_service = self._timeline_service()
        try:
            revision = timeline_service.create_subtitle_revision(episode_id, cues, authority=authority, actor="local-user")
        except DomainRuleError as error:
            return self._automation_failure(error.code, error.message), 0
        machine_check = {
            "status": "PASS",
            "ok": True,
            "subtitle_revision_id": str(revision["id"]),
            "cue_count": len(cues),
            "timing_source": "ESTIMATED_SPEAKING_RATE",
        }
        report = self._automation_report("PASS", machine_check, {"subtitle_revision_id": str(revision["id"]), "cue_count": len(cues)}, "字幕 revision 创建完成（文本权威为剧本原文，时间为估算）")
        return report, 0

    def _run_automation_task(self, job: dict[str, Any], output_root: Path, worker_id: str) -> tuple[str, str, dict[str, Any], int]:
        snapshot = job["input_snapshot"]
        run_id = str(snapshot.get("automation_run_id", "") or "")
        task_id = str(snapshot.get("automation_task_id", "") or "")
        if not run_id or not task_id:
            raise DomainRuleError("JOB_INPUT_INVALID", "自动化任务 Job 缺少 automation_run_id/automation_task_id")
        with self.database.connect() as connection:
            task = connection.execute(
                "SELECT item_json FROM automation_workflow_run_tasks WHERE id=? AND run_id=?", (task_id, run_id)
            ).fetchone()
        if task is None:
            raise DomainRuleError("AUTOMATION_TASK_NOT_FOUND", "workflow 任务不存在", {"task_id": task_id})
        try:
            item = json.loads(str(task["item_json"] or "{}"))
        except (TypeError, ValueError) as error:
            raise DomainRuleError("AUTOMATION_TASK_PAYLOAD_INVALID", "workflow 任务 item_json 不是合法 JSON") from error
        payload = item.get("payload", {}) if isinstance(item, dict) else {}
        if not isinstance(payload, dict):
            raise DomainRuleError("AUTOMATION_TASK_PAYLOAD_INVALID", "workflow 任务 payload 必须是对象")
        action = str(payload.get("action", "") or "").strip()
        episode_id = str(payload.get("episode_id", "") or "").strip()
        if not action:
            raise DomainRuleError("AUTOMATION_TASK_PAYLOAD_INVALID", "自动化任务 payload 缺少 action")
        if not episode_id:
            raise DomainRuleError("AUTOMATION_TASK_PAYLOAD_INVALID", "自动化任务 payload 缺少 episode_id")
        managed_front_half = action != "KEYFRAME_CHECK" or bool(payload.get("front_half_managed"))
        if action in EpisodeFrontHalfActionService.ACTIONS and managed_front_half:
            # Front-half Jobs never auto-apply/auto-approve creative facts.
            # The action service emits PASS/SKIPPED for existing authorities
            # or NEEDS_HITL with bounded evidence for the workflow gate.
            report, produced_extra = EpisodeFrontHalfActionService(
                self.database, self.settings,
            ).run(action, episode_id)
        elif action == "KEYFRAME_CHECK":
            # Frozen WHOLE_DRAMA v1 workflows retain their historical
            # approved_version authority.  New Episode Production snapshots
            # set front_half_managed and require an explicit ReviewDecision.
            report, produced_extra = self._automation_keyframe_check(episode_id)
        elif action == "VIDEO_GENERATION":
            mode_policy = payload.get("mode_policy", {})
            target_take_count = int(mode_policy.get("target_take_count", 2)) if isinstance(mode_policy, dict) else 2
            report, produced_extra = EpisodeWorkerActionService(self.database, self.settings).video_generation(
                episode_id, run_id, task_id, target_take_count=target_take_count,
            )
        elif action == "QC":
            report, produced_extra = EpisodeWorkerActionService(self.database, self.settings).qc(episode_id, run_id, task_id)
        elif action == "TTS_BATCH":
            report, produced_extra = self._automation_tts_batch(episode_id, run_id, task_id)
        elif action == "RENDER":
            report, produced_extra = self._automation_render(episode_id)
        elif action == "DELIVERY":
            report, produced_extra = self._automation_delivery(episode_id)
        elif action == "SUBTITLE":
            report, produced_extra = self._automation_subtitle(episode_id, payload)
        else:
            raise DomainRuleError("AUTOMATION_ACTION_UNSUPPORTED", "不支持的自动化任务 action", {"action": action})
        report = {
            "schema_version": "localdrama.automation-task-report.v1",
            "job_id": str(job["id"]),
            "automation_run_id": run_id,
            "automation_task_id": task_id,
            "ordinal": int(snapshot.get("ordinal", 0) or 0),
            "item_key": str(snapshot.get("item_key", "") or ""),
            "action": action,
            "episode_id": episode_id,
            "worker_id": worker_id,
            "created_at": datetime.now(UTC).isoformat(),
            **report,
        }
        path = output_root / "report.json"
        self._atomic_file(path, lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"))
        relative = path.relative_to(self.settings.work_root).as_posix()
        produced_bytes = max(0, int(path.stat().st_size) + produced_extra)
        return "AUTOMATION_TASK_REPORT", relative, report, produced_bytes

    def _advance_automation_run(self, job: dict[str, Any], report: dict[str, Any], produced_bytes: int) -> str | None:
        """Push the workflow run to its next task after this task Job succeeded.

        The machine_check.status from the report is the only input to the run
        state machine: PASS/SKIPPED continue, NEEDS_HITL/FAIL/FAILED/BLOCKED
        pause the run on the declarative conditions.  Returns a non-benign
        error code (attached to the result) instead of raising, because the Job
        is already SUCCEEDED and must not be rolled back by a run-level issue.
        """
        run_id = str(job["input_snapshot"].get("automation_run_id", "") or "")
        if not run_id:
            return None
        machine_check = report.get("machine_check", {})
        status = str(machine_check.get("status", "PASS"))
        produced = report.get("produced", {})
        dependency_job_ids: list[str] = []
        if isinstance(produced, dict):
            for item in produced.get("items", []):
                if not isinstance(item, dict):
                    continue
                candidates = [item, *(item.get("submissions", []) if isinstance(item.get("submissions"), list) else [])]
                for candidate in candidates:
                    if isinstance(candidate, dict) and candidate.get("job_id"):
                        job_id = str(candidate["job_id"])
                        if job_id not in dependency_job_ids:
                            dependency_job_ids.append(job_id)
                for job_id_value in item.get("dependency_job_ids", []) if isinstance(item.get("dependency_job_ids"), list) else []:
                    job_id = str(job_id_value)
                    if job_id and job_id not in dependency_job_ids:
                        dependency_job_ids.append(job_id)
        service = AutomationWorkflowService(self.database)
        try:
            service.step_run(
                run_id,
                machine_context={"status": status, "machine_check": machine_check},
                produced_bytes=produced_bytes,
                expected_completed_job_id=str(job["id"]),
                additional_dependency_job_ids=dependency_job_ids,
                actor="local-user",
            )
        except DomainRuleError as error:
            if error.code in {"AUTOMATION_RUN_NOT_RUNNING", "AUTOMATION_RUN_NOT_FOUND"}:
                return None  # run was paused/ended externally; nothing to advance
            return error.code
        return None

    def _run_script_breakdown_with_heartbeat(
        self,
        job: dict[str, Any],
        output_root: Path,
        *,
        attempt_id: str,
        token: str,
        worker_id: str,
    ) -> WorkerExecution:
        heartbeat_stop = threading.Event()
        heartbeat_errors: list[BaseException] = []
        progress_state: dict[str, Any] = {"phase": "PREPARING", "percent": 5}
        progress_lock = threading.Lock()

        def heartbeat_progress(progress: dict[str, Any]) -> None:
            with progress_lock:
                progress_state.clear()
                progress_state.update(progress)
                self._active_progress = {**self._active_progress, **progress}
            heartbeat = self.jobs.heartbeat(
                attempt_id, token, worker_id, progress=progress, lease_seconds=120,
            )
            if heartbeat.get("cancel_requested"):
                raise DomainRuleError("JOB_CANCELLED", "AI 拆解已请求取消；不会保存模型输出")
            if heartbeat_errors:
                raise DomainRuleError("JOB_HEARTBEAT_FAILED", "AI 拆解 Job lease 续期失败")

        def keep_lease_alive() -> None:
            while not heartbeat_stop.wait(20.0):
                try:
                    with progress_lock:
                        current_progress = dict(progress_state)
                    self.jobs.heartbeat(
                        attempt_id, token, worker_id, progress=current_progress, lease_seconds=120,
                    )
                except BaseException as error:
                    heartbeat_errors.append(error)
                    return

        heartbeat_thread = threading.Thread(
            target=keep_lease_alive,
            name=f"script-breakdown-heartbeat-{str(job['id'])[:8]}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            result = self._execution(self._run_script_breakdown_job(job, output_root, heartbeat_progress))
            if heartbeat_errors:
                raise DomainRuleError("JOB_HEARTBEAT_FAILED", "AI 拆解 Job lease 续期失败")
            return result
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2.0)

    def _dispatcher(
        self,
        *,
        attempt_id: str,
        token: str,
        worker_id: str,
    ) -> WorkerJobDispatcher:
        simple_handlers = {
            job_type: getattr(self, method_name)
            for job_type, method_name in _JOB_HANDLER_REGISTRY.items()
        }
        handlers = {
            job_type: (lambda job, root, handler=handler: self._execution(handler(job, root)))
            for job_type, handler in simple_handlers.items()
        }
        handlers["CPU_TEST"] = lambda job, root: self._run_cpu_test(job, root, worker_id)
        handlers["SCRIPT_BREAKDOWN_LOCAL_LLM"] = lambda job, root: self._run_script_breakdown_with_heartbeat(
            job, root, attempt_id=attempt_id, token=token, worker_id=worker_id,
        )

        def automation(job: dict[str, Any], root: Path) -> WorkerExecution:
            kind, relative, report, produced_bytes = self._run_automation_task(job, root, worker_id)
            return WorkerExecution(kind, relative, report, produced_bytes)

        handlers["AUTOMATION_WORKFLOW_TASK"] = automation
        return WorkerJobDispatcher(handlers)

    def run_once(
        self,
        worker_id: str,
        channels: list[str] | None = None,
        *,
        worker_session_id: str | None = None,
    ) -> dict[str, Any] | None:
        claim = self.jobs.claim(worker_id, channels or ["CPU"], worker_session_id=worker_session_id)
        if claim is None:
            return None
        job = claim["job"]
        attempt = claim["attempt"]
        attempt_id = str(attempt["id"])
        token = str(attempt["lease_token"])
        self._active_job_context = (attempt_id, token, worker_id)
        self._last_cancel_check = 0.0
        self._last_progress_heartbeat = 0.0
        self._active_progress = {"phase": "PREPARING", "percent": 5}
        self._ffmpeg_expected_duration_ms = None
        try:
            initial_lease_seconds = 120 if job["type"] == "SCRIPT_BREAKDOWN_LOCAL_LLM" else 60
            self.jobs.heartbeat(
                attempt_id,
                token,
                worker_id,
                progress=dict(self._active_progress),
                lease_seconds=initial_lease_seconds,
            )
            output_root = self.settings.work_root / "jobs" / str(job["id"])
            if self._report_progress({"phase": "EXECUTING", "percent": 10}, force=True):
                raise DomainRuleError("JOB_CANCELLED", "后台任务已取消，不会开始新的处理步骤")
            execution = self._dispatcher(
                attempt_id=attempt_id, token=token, worker_id=worker_id,
            ).execute(job, output_root)
            business_progress_complete = int(self._active_progress.get("percent") or 0) >= 100
            if not business_progress_complete:
                if self._report_progress({"phase": "VERIFYING_OUTPUT", "percent": 92}, force=True):
                    raise DomainRuleError("JOB_CANCELLED", "后台任务已取消，本次输出不会登记")
                if self._report_progress({"phase": "FINALIZING", "percent": 97}, force=True):
                    raise DomainRuleError("JOB_CANCELLED", "后台任务已取消，本次输出不会提交为成功")
            artifact = self.jobs.register_artifact(attempt_id, execution.kind, execution.relative_path)
            result = self.jobs.complete(attempt_id, token, worker_id, success=True)
            advance_error: str | None = None
            if execution.report is not None:
                advance_error = self._advance_automation_run(job, execution.report, execution.produced_bytes)
            payload: dict[str, Any] = {"job": job, "attempt": attempt, "artifact": artifact, "result": result}
            if advance_error is not None:
                payload["advance_error"] = advance_error
            return payload
        except DomainRuleError as error:
            result = self.jobs.complete(
                attempt_id,
                token,
                worker_id,
                success=False,
                error_code=error.code,
                error_detail_redacted=error.message,
                retryable=self._retryable_error(error.code),
            )
            return {"job": job, "attempt": attempt, "result": result, "error": error.code}
        except OSError as error:
            disk_full = error.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}
            code = "DISK_FULL" if disk_full else "MEDIA_WORKER_IO_FAILED"
            detail = "本地输出空间不足，worker 已安全终止且未登记产物" if disk_full else "本地媒体 worker 文件操作失败"
            result = self.jobs.complete(
                attempt_id, token, worker_id, success=False,
                error_code=code, error_detail_redacted=detail,
            )
            return {"job": job, "attempt": attempt, "result": result, "error": code}
        except MemoryError:
            code = "WORKER_OUT_OF_MEMORY"
            result = self.jobs.complete(
                attempt_id, token, worker_id, success=False,
                error_code=code, error_detail_redacted="本地 worker 内存不足，任务已安全终止",
            )
            return {"job": job, "attempt": attempt, "result": result, "error": code}
        finally:
            self._active_job_context = None

    def run_until_idle(
        self,
        worker_id: str,
        max_jobs: int = 100,
        *,
        worker_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for _ in range(max_jobs):
            result = self.run_once(worker_id, worker_session_id=worker_session_id)
            if result is None:
                break
            results.append(result)
        return results
