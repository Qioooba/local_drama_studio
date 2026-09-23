"""Production runtime adapters for the explainer narration and policy stages.

The explainer worker handlers are written against narrow ports so their business
flow can be tested without a GPU.  This module supplies the *real* adapters:

* :class:`LocalAiNarrationTtsRuntime` -> the offline VoxCPM2 subprocess runtime
  that already backs the legacy dialogue TTS flow.
* :class:`MediaServiceNarrationPort` -> real media registration plus FFprobe
  verification through :class:`~local_drama.application.media.MediaService`.
* :class:`LocalForcedAlignerAdapter` / :class:`LocalAsrAdapter` -> the offline
  Qwen3-ForcedAligner and Qwen3-ASR subprocess tasks.  Neither fabricates a
  timestamp: when the runtime is not installed the adapter raises
  ``CAPABILITY_UNAVAILABLE`` instead of returning an empty success.
* :class:`QualityPolicyEvaluator` -> the dedicated machine policy path.  It is the
  only object that may write a ``POLICY_ACCEPTED`` decision.
* :class:`ExplainerScheduleExecutor` -> the resident schedule tick.  It claims a
  due trigger point with a lease and a fencing token and hands it to the existing
  automation workflow runner; it never produces anything itself.
* :func:`build_stage_handlers` -> the first-party business handlers the generic
  ``EXPLAINER_TASK`` job dispatches to.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from local_drama.application.explainers.media_qc import build_media_qc_readers
from local_drama.application.explainers.quality import ExplainerQualityService
from local_drama.application.explainers.schedules import ExplainerScheduleService
from local_drama.application.explainers.text_planner import (
    ExplainerStagePlanner,
    LocalTextPlanner,
    build_stage_handlers,
)
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.media import MediaService
from local_drama.application.worker_handlers.explainer_qc import (
    build_qc_handlers,
    build_qc_readers,
    qc_handler_availability,
    qc_layer_status,
)
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import ExplainerContractError, ExplainerErrorCode
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.local_ai_subprocess import LocalAiSubprocessRuntime

#: The documented "use the local model's own voice" reference.  It is not a path.
MODEL_DEFAULT_VOICE_SENTINEL = "LOCAL_MODEL_DEFAULT"

#: Explainer stage codes and whether this build has a first-party handler for them.#: Every stage the production graph plans is now wired; a stage that cannot run
#: still fails loudly instead of silently skipping, and the reason is stated here.
STAGE_HANDLER_AVAILABILITY: dict[str, str] = {
    "RESEARCH_ACQUIRE": "WIRED_LOCAL_TEXT_PLANNER",
    "FACT_EXTRACT": "WIRED_LOCAL_TEXT_PLANNER",
    "NARRATION_WRITE": "WIRED_LOCAL_TEXT_PLANNER",
    "EXPLAINER_STORYBOARD": "WIRED_LOCAL_TEXT_PLANNER",
    "EXPLAINER_POLICY_EVALUATE": "WIRED",
    # The QC stages are wired, but each layer only runs when its real measurement
    # port is present; a missing port is recorded as LAYER_NOT_RUN, never a pass.
    "EXPLAINER_VISUAL_QC": "WIRED_LAYERED_QC",
    "COMPOSITION_QC": "WIRED_LAYERED_QC",
}


class LocalAiNarrationTtsRuntime:
    """``NarrationTtsRuntimePort`` over the offline VoxCPM2 subprocess runtime.

    ``voice_ref`` uses the same ``voxcpm2:<参考音频>[|<参考文本>]`` convention the
    legacy dialogue handler uses, where the reference may be ``media:<id>`` so a
    cloned voice is pinned to an integrity-checked project media version.
    """

    def __init__(self, runtime: LocalAiSubprocessRuntime, *, media: MediaService | None = None) -> None:
        self.runtime = runtime
        self.media = media

    def synthesize(
        self,
        *,
        text: str,
        voice_ref: str,
        model_ref: str,
        output: Path,
        speech_rate: float,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        del model_ref  # the runtime is selected by settings, not by a model string
        if not text.strip():
            raise DomainRuleError("NARRATION_TEXT_EMPTY", "旁白朗读文本不能为空")
        reference = str(voice_ref or "").removeprefix("voxcpm2:").strip()
        if not reference:
            raise DomainRuleError(
                "NARRATION_VOICE_REF_INVALID",
                "旁白音色必须使用 voxcpm2:<参考音频>[|<参考文本>] 形式",
                {"voice_ref": voice_ref},
            )
        prompt_ref, _, prompt_text = reference.partition("|")
        prompt_path: Path | None = None
        if prompt_ref.startswith("media:"):
            if self.media is None:
                raise DomainRuleError(
                    ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value,
                    "旁白音色引用项目媒体，但未注入媒体访问端口",
                    {"voice_ref": voice_ref},
                )
            _meta, prompt_path = self.media.content_path(prompt_ref.removeprefix("media:").strip())
        elif prompt_ref.strip() and prompt_ref.strip() != MODEL_DEFAULT_VOICE_SENTINEL:
            prompt_path = Path(prompt_ref.strip())
        # ``MODEL_DEFAULT_VOICE_SENTINEL`` is the documented "no clone reference"
        # voice: the local model narrates with its own voice, so no prompt audio is
        # sent.  Treating the sentinel as a filesystem path made the runtime look for
        # a file literally named after it and fail every narration take.
        result = self.runtime.synthesize(
            text,
            output,
            prompt_audio=prompt_path,
            prompt_text=prompt_text.strip() or None,
            speed=speech_rate,
        )
        return {
            "runtime": "voxcpm2-subprocess",
            "network_used": bool(result.payload.get("network_used", False)),
            "elapsed_seconds": result.payload.get("elapsed_seconds"),
            "speed_applied_natively": True,
            "voice_mode": "MODEL_DEFAULT" if prompt_path is None else "CLONED_REFERENCE",
            "command": list(result.command),
        }


class MediaServiceNarrationPort:
    """``NarrationMediaPort`` (TTS) and ``NarrationAlignMediaPort`` over MediaService."""

    def __init__(self, media: MediaService) -> None:
        self.media = media

    def register_narration_audio(
        self,
        *,
        project_id: str,
        video_id: str,
        source_path: Path,
        label: str,
        take_no: int,
    ) -> dict[str, Any]:
        """Register a real narration take as an immutable project media version.

        The owner is the explainer video (never an episode), and the purpose keeps
        narration audio distinguishable from drama dialogue audio.
        """

        result = self.media.import_file(
            project_id,
            source_path,
            purpose="EXPLAINER_NARRATION",
            owner_type="EXPLAINER_VIDEO",
            owner_id=video_id,
            media_kind="AUDIO",
            stage="NARRATION",
            actor="local-user",
            schedule_derivatives=False,
        )
        return {
            **result,
            "label": label,
            "take_no": int(take_no),
            "owner_type": "EXPLAINER_VIDEO",
            "owner_id": video_id,
            "purpose": "EXPLAINER_NARRATION",
        }

    def probe_audio(self, path: Path) -> dict[str, Any]:
        return self.media.probe_output(path, "AUDIO")

    def content_path(self, media_version_id: str) -> tuple[dict[str, Any], Path]:
        return self.media.content_path(media_version_id)


class LocalForcedAlignerAdapter:
    """``NarrationAlignerPort`` over the offline Qwen3-ForcedAligner subprocess task."""

    def __init__(self, runtime: LocalAiSubprocessRuntime | None) -> None:
        self.runtime = runtime

    def _require_runtime(self) -> LocalAiSubprocessRuntime:
        if self.runtime is None:
            raise DomainRuleError(
                ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value,
                "本机未配置离线强制对齐运行时，无法在已知文稿上做对齐",
                {"required_setting": "local_ai_python / local_ai_adapter / local_ai_model_root"},
            )
        return self.runtime

    def align(
        self,
        *,
        media_path: Path,
        media_sha256: str,
        spoken_text: str,
        display_text: str,
        locale: str,
        sample_offset: int,
    ) -> Mapping[str, Any]:
        del display_text  # the aligner aligns the spoken form; display mapping is handler-side
        runtime = self._require_runtime()
        language = "Chinese" if locale.lower().startswith("zh") else "English"
        execution = runtime.align(media_path, spoken_text, language=language)
        payload = execution.payload
        timestamps = payload.get("timestamps")
        if not isinstance(timestamps, list):
            raise DomainRuleError(
                "NARRATION_ALIGNMENT_OUTPUT_INVALID",
                "对齐运行时没有返回时间戳列表",
                {"runtime_status": payload.get("status")},
            )
        # Timestamps are converted to integer milliseconds.  A timestamp the
        # runtime did not return is simply absent from the list; the handler then
        # reports it as unaligned rather than receiving a fabricated value.
        word_timings: list[dict[str, Any]] = []
        for item in timestamps:
            if not isinstance(item, Mapping):
                continue
            text = str(item.get("text") or "")
            start = item.get("start_time")
            end = item.get("end_time")
            if not text or start is None or end is None:
                continue
            start_ms = int(round(float(start) * 1000))
            end_ms = int(round(float(end) * 1000))
            if end_ms < start_ms:
                raise DomainRuleError(
                    "NARRATION_ALIGNMENT_OUTPUT_INVALID",
                    "对齐运行时返回了结束早于开始的词级时间戳",
                    {"token": text, "start_ms": start_ms, "end_ms": end_ms},
                )
            word_timings.append({"text": text, "start_ms": start_ms, "end_ms": end_ms})
        return {
            "word_timings": word_timings,
            "alignment_status": "ALIGNED" if word_timings else "PARTIAL",
            "detector_version": "qwen3-forced-aligner-0.6b-hf",
            "media_sha256": media_sha256,
            "sample_offset": int(sample_offset),
            "network_used": bool(payload.get("network_used", False)),
            "model": payload.get("model"),
        }


class LocalAsrAdapter:
    """``NarrationAsrPort`` over the offline Qwen3-ASR subprocess task.

    The transcript is review evidence only.  Nothing here writes back to the
    authoritative script.
    """

    def __init__(self, runtime: LocalAiSubprocessRuntime | None) -> None:
        self.runtime = runtime

    def transcribe(self, *, media_path: Path, locale: str, timeout_seconds: int) -> Mapping[str, Any]:
        if self.runtime is None:
            raise DomainRuleError(
                ExplainerErrorCode.CAPABILITY_UNAVAILABLE.value,
                "本机未配置离线 ASR 运行时，无法做独立漏读/错读复核",
                {"required_setting": "local_ai_python / local_ai_adapter / local_ai_model_root"},
            )
        del timeout_seconds  # the subprocess runtime owns its own bounded timeout
        execution = self.runtime.transcribe(media_path)
        payload = execution.payload
        transcription = str(payload.get("transcription") or "").strip()
        return {
            "transcription": transcription,
            "language": payload.get("language") or locale,
            "detector_version": "qwen3-asr-1.7b-hf",
            "network_used": bool(payload.get("network_used", False)),
            "model": payload.get("model"),
            "rewrites_script": False,
        }


class ExplainerRepositoryTransaction:
    """Context manager yielding an :class:`ExplainerRepository` in one short transaction.

    A worker must never hold a SQLite transaction across a GPU wait, so every
    projection write opens its own transaction and commits immediately.
    """

    def __init__(self, database: Any) -> None:
        self._database = database
        self._context: Any = None

    def __enter__(self) -> ExplainerRepository:
        self._context = self._database.transaction()
        connection = self._context.__enter__()
        return ExplainerRepository(connection)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        return self._context.__exit__(exc_type, exc, traceback)


class ExplainerRepositoryRead:
    """Context manager yielding a repository on a plain, non-transactional connection.

    The explainer text stages call the local LLM between their reads and their
    writes.  Running that call inside :class:`ExplainerRepositoryTransaction` held
    the SQLite write lock for the whole inference, which blocked the job's own
    lease heartbeat: it failed with ``database is locked``, the lease expired, and
    the stage was re-queued while the model was still thinking.  A plain
    connection takes no write lock, so a long local inference cannot starve the
    queue it belongs to.
    """

    def __init__(self, database: Any) -> None:
        self._database = database
        self._connection: Any = None

    def __enter__(self) -> ExplainerRepository:
        self._connection = self._database.connect()
        return ExplainerRepository(self._connection)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        return None


class QualityPolicyEvaluator:
    """The dedicated machine policy path (design §12.2, §14).

    ``evaluate`` is the only route that creates a ``POLICY_ACCEPTED``
    ``explainer_decisions`` row, and it always records the rule version,
    thresholds, evidence and limitations.  It refuses to write a human decision.

    The quality service is reached through the repository-explicit static policy
    entry point, so this adapter holds no cross-service construction dependency
    (``scripts/audit_architecture_debt.py``).
    """

    def __init__(self, database: Any) -> None:
        self.database = database

    def evaluate(self, *, project_id: str, video_id: str, semantic_inputs: Mapping[str, Any]) -> Mapping[str, Any]:
        edition_id = str(semantic_inputs.get("edition_id") or "").strip() or None
        render_id = str(semantic_inputs.get("render_id") or "").strip()
        subject_kind = str(semantic_inputs.get("subject_kind") or ("COMPOSITION_RENDER" if render_id else "EDITION"))
        subject_revision_id = str(
            semantic_inputs.get("subject_revision_id") or render_id or edition_id or video_id
        )
        with self.database.connect() as connection:
            repository = ExplainerRepository(connection)
            subject_hash = ""
            if subject_kind == "COMPOSITION_RENDER":
                render = repository.find("composition_renders", subject_revision_id)
                if render is None:
                    raise DomainRuleError(
                        ExplainerErrorCode.NOT_FOUND.value,
                        "找不到该渲染版本，无法做机器政策判定",
                        {"render_id": subject_revision_id},
                    )
                subject_hash = str(render.get("sha256") or "")
            report = repository.latest_qc_report(
                subject_kind=subject_kind,
                subject_revision_id=subject_revision_id,
                subject_hash=subject_hash or None,
            )
            if report is None:
                # No report means the machine cannot accept: it must ask for the
                # detection work instead of silently passing.
                return {
                    "accepted": False,
                    "decision_kind": "POLICY_ACCEPTED",
                    "actor_type": "MACHINE",
                    "policy_processor": "EXPLAINER_POLICY_EVALUATE",
                    "rule_version": "explainer_standard_v1",
                    "workflow_effect": "REQUEST_HUMAN",
                    "blockers": [
                        {
                            "issue_kind": "QC_REPORT_MISSING",
                            "reason": "NO_QC_REPORT_FOR_SUBJECT",
                            "next_step": "先运行技术/语义/字幕检查并生成报告，再评估机器政策。",
                        }
                    ],
                    "subject_kind": subject_kind,
                    "subject_revision_id": subject_revision_id,
                    "subject_hash": subject_hash or None,
                }
            decision = ExplainerQualityService.evaluate_policy_for_repo(
                repository,
                project_id=project_id,
                video_id=video_id,
                edition_id=edition_id,
                subject_kind=subject_kind,
                subject_revision_id=subject_revision_id,
                subject_hash=subject_hash or str(report.get("subject_hash") or ""),
                report_id=str(report["id"]),
            )
        record = decision.get("decision") if isinstance(decision, Mapping) else None
        accepted = bool((record or {}).get("decision_kind") == "POLICY_ACCEPTED" and decision.get("accepted"))
        return {
            "accepted": accepted,
            "decision_kind": "POLICY_ACCEPTED",
            "actor_type": "MACHINE",
            "policy_processor": "EXPLAINER_POLICY_EVALUATE",
            "rule_version": str((record or {}).get("rule_version") or "explainer_standard_v1"),
            "workflow_effect": str(decision.get("workflow_effect") or "REQUEST_HUMAN"),
            "blockers": list((record or {}).get("blockers") or decision.get("blockers") or []),
            "decision_id": (record or {}).get("id"),
            "subject_kind": subject_kind,
            "subject_revision_id": subject_revision_id,
            "subject_hash": subject_hash or None,
            "human_approval_written": False,
        }


def build_explainer_task_handlers(
    *,
    planner_factory: Callable[[], "ExplainerStagePlanner"] | None = None,
    repo_factory: Callable[[], Any] | None = None,
    read_repo_factory: Callable[[], Any] | None = None,
    visual_provider: Any | None = None,
    technical_reader: Callable[..., Any] | None = None,
    sampling_reader: Callable[..., Any] | None = None,
) -> dict[str, Callable[[dict[str, Any], Mapping[str, Any]], Any]]:
    """Business handlers for the explainer stages that have a first-party path.

    All four text stages are backed by the first-party local text planner, which
    runs on the existing offline LLM capability.  The two QC stages are backed by
    :mod:`local_drama.application.worker_handlers.explainer_qc`, which reads the
    frozen revision artefacts and the injected measurement ports; the subtitle and
    fact readers are always bound, while the technical and sampling readers stay
    optional because they need a real decoder and real extracted frames.

    When either factory is missing the mapping is empty on purpose: requesting a
    stage then fails loudly with ``EXPLAINER_TASK_HANDLER_MISSING`` and
    :data:`STAGE_HANDLER_AVAILABILITY` states which stage needs what, rather than
    the pipeline pretending to have produced a script or an inspection.
    """

    if planner_factory is None or repo_factory is None:
        return {}
    handlers: dict[str, Callable[[dict[str, Any], Mapping[str, Any]], Any]] = dict(
        build_stage_handlers(
            planner_factory=planner_factory,
            repo_factory=repo_factory,
            read_repo_factory=read_repo_factory,
        )
    )
    handlers.update(
        build_qc_handlers(
            quality_factory=lambda repo: ExplainerQualityService(repo, visual_provider=visual_provider),
            repo_factory=repo_factory,
            technical_reader=technical_reader,
            sampling_reader=sampling_reader,
            **build_qc_readers(),
        )
    )
    return handlers


def build_media_qc_handlers(
    *,
    repo_factory: Callable[[], Any],
    planner_factory: Callable[[], "ExplainerStagePlanner"] | None,
    settings: Any,
    visual_provider: Any | None = None,
    media_content_path: Callable[[str], Path] | None = None,
    extra_handlers: Mapping[str, Callable[[dict[str, Any], Mapping[str, Any]], Any]] | None = None,
    read_repo_factory: Callable[[], Any] | None = None,
) -> dict[str, Callable[[dict[str, Any], Mapping[str, Any]], Any]]:
    """Explainer handlers with the real decoder-backed technical/sampling readers.

    This is the production binder: it resolves ``ffmpeg``/``ffprobe`` from settings
    and binds the frame sampler to the instance work root.  Both tools are optional
    at runtime, and a machine without them keeps the corresponding layer at
    ``LAYER_NOT_RUN`` instead of reporting a pass nobody measured.

    ``extra_handlers`` carries the production stages owned by
    :mod:`local_drama.application.explainers.production_pipeline` (identity assets,
    narration, pictures, subtitles, render and export).  They are merged here so one
    dispatcher answers every planned step of the graph.
    """

    readers = build_media_qc_readers(
        ffmpeg_path=settings.ffmpeg_path,
        ffprobe_path=settings.ffprobe_path,
        frame_root=settings.explainer_frames_root,
        content_path=media_content_path or _unavailable_content_path,
    )
    handlers = build_explainer_task_handlers(
        planner_factory=planner_factory,
        repo_factory=repo_factory,
        read_repo_factory=read_repo_factory,
        visual_provider=visual_provider,
        technical_reader=readers["technical_reader"],
        sampling_reader=readers["sampling_reader"],
    )
    if extra_handlers:
        handlers.update(dict(extra_handlers))
    return handlers


def _unavailable_content_path(media_version_id: str) -> Path:  # pragma: no cover - explicit failure path
    raise DomainRuleError(
        "MEDIA_FILE_MISSING",
        "未注入媒体内容解析端口，无法定位质检文件",
        {"media_version_id": media_version_id},
    )


def stage_handler_report() -> dict[str, str]:
    """Machine-readable statement of which explainer stages can actually run."""

    return dict(STAGE_HANDLER_AVAILABILITY)


def resolve_aligner_language(locale: str) -> str:
    return "Chinese" if locale.lower().startswith("zh") else "English"


def summarise_runtime_readiness(settings: Any) -> dict[str, Any]:
    """Report which offline runtimes the narration stages can actually use."""

    configured = settings.local_ai_python is not None and settings.local_ai_adapter is not None
    return {
        "narration_tts": "READY" if configured else "BLOCKED_MISSING_LOCAL_AI_RUNTIME",
        "narration_align": "READY" if configured else "BLOCKED_MISSING_LOCAL_AI_RUNTIME",
        "narration_asr": "READY" if configured else "BLOCKED_MISSING_LOCAL_AI_RUNTIME",
        "local_ai_python": str(settings.local_ai_python) if settings.local_ai_python else None,
        "local_ai_adapter": str(settings.local_ai_adapter) if settings.local_ai_adapter else None,
        "local_ai_model_root": str(settings.local_ai_model_root) if settings.local_ai_model_root else None,
        "qc_stage_handlers": qc_handler_availability(),
        "qc_layers": qc_layer_status(),
    }


def required_models_present(model_root: Path | None) -> dict[str, bool]:
    """Physical presence of the narration models.  Presence is not smoke success."""

    if model_root is None:
        return {"voxcpm2": False, "qwen3_asr": False, "qwen3_forced_aligner": False}
    return {
        "voxcpm2": (model_root / "PyTorch" / "VoxCPM2").is_dir(),
        "qwen3_asr": (model_root / "PyTorch" / "Qwen3-ASR-1.7B-hf").is_dir(),
        "qwen3_forced_aligner": (model_root / "PyTorch" / "Qwen3-ForcedAligner-0.6B-hf").is_dir(),
    }


def build_planner_factory(database: Any, settings: Any) -> Callable[[], LocalTextPlanner]:
    """Create the planner factory over the configured offline LLM capability.

    The client is resolved per stage through ``LocalLLMService``, which enforces
    the loopback / controlled-private endpoint rules and the configured provider,
    so the planner never opens a public-network connection of its own.  A missing
    or unpublished text profile surfaces as ``CAPABILITY_UNAVAILABLE`` from the
    planner, which is exactly the preflight/runtime blocker the design wants.
    """

    def factory() -> LocalTextPlanner:
        def client_factory() -> Any:
            service = LocalLLMService(database, settings)
            with database.connect() as connection:
                row = connection.execute(
                    """SELECT epv.id FROM execution_profile_versions epv
                    JOIN execution_profiles ep ON ep.id=epv.execution_profile_id
                    WHERE epv.capability='LLM_STORY_PARSE' AND epv.status='PUBLISHED'
                    ORDER BY epv.updated_at DESC, epv.version_no DESC, epv.id ASC LIMIT 1"""
                ).fetchone()
            selected = str(row["id"]) if row else None
            return service.client(profile_version_id=selected) if selected else service.client()

        return LocalTextPlanner(client_factory=client_factory)

    return factory


def sequence_or_empty(value: Any) -> Sequence[Any]:
    if isinstance(value, (list, tuple)):
        return value
    return ()


class ExplainerScheduleExecutor:
    """Resident schedule tick: claim a due trigger point, then hand it off.

    This is the piece that makes 定时生产 real.  It runs inside the local Runtime
    Host / Worker loop (never in the browser, never in an external reminder
    service) and does exactly three things per tick:

    1. refresh each active schedule with a short database lease;
    2. claim at most ``max_concurrent_runs`` due trigger points per schedule using
       the database lease + fencing token;
    3. hand a claimed point to the existing
       :meth:`~local_drama.application.automation_workflows.AutomationWorkflowService.start_run`
       with ``schedule-occurrence:<id>`` as the idempotency key.

    It deliberately contains **no executor**: production still runs through the
    existing workflow/Job facilities, and a schedule that has no project yet
    creates one through the ordinary explainer creation path rather than a private
    code path.
    """

    def __init__(
        self,
        database: Any,
        settings: Any,
        *,
        project_service_factory: Callable[[], Any] | None = None,
        workflow_service_factory: Callable[[], Any] | None = None,
        production_factory: Callable[[], Any] | None = None,
        lease_seconds: int = 900,
    ) -> None:
        self.database = database
        self.settings = settings
        self._project_service_factory = project_service_factory
        self._workflow_service_factory = workflow_service_factory
        self._production_factory = production_factory
        # A long preflight may outlive the first lease, so the lease is refreshed
        # (heartbeat) rather than extended: a crash must still let another worker
        # take the trigger point back after the lease expires.
        self.lease_seconds = max(60, min(int(lease_seconds), 3600))

    # ------------------------------------------------------------------ helpers
    # These are port-wiring helpers, not business logic: they exist so a caller can
    # inject a narrow double in tests while production binds the real service.  The
    # ``build_*`` name marks them as composition roots for the architecture guard.
    def build_project_service(self) -> Any:
        if self._project_service_factory is not None:
            return self._project_service_factory()
        from local_drama.application.projects import ProjectService

        return ProjectService(self.database, self.settings.projects_root)

    def build_workflow_service(self) -> Any:
        if self._workflow_service_factory is not None:
            return self._workflow_service_factory()
        from local_drama.application.automation_workflows import AutomationWorkflowService

        return AutomationWorkflowService(self.database)

    def build_production(self) -> Any:
        if self._production_factory is not None:
            return self._production_factory()
        from local_drama.application.explainers.production import ExplainerProductionService

        return ExplainerProductionService(
            self.database,
            capability_probe=build_capability_probe(self.database, self.settings),
            workflow_service=self.build_workflow_service(),
        )

    def build_schedule_service(self, connection: Any) -> "ExplainerScheduleService":
        """The schedule service for one short autocommit connection."""

        return ExplainerScheduleService(ExplainerRepository(connection))

    # ------------------------------------------------------------------ tick
    def tick(self, *, now_utc: Any = None, owner: str | None = None, limit: int = 50) -> dict[str, Any]:
        owner_value = str(owner or f"explainer-scheduler-{secrets.token_hex(4)}")
        # Every schedule write uses its own short autocommit connection: a claim
        # must be durable before the (possibly slow) preflight runs, and no SQLite
        # transaction may be held across model work.
        connection = self.database.connect()
        try:
            service = self.build_schedule_service(connection)
            materialised = self._materialise_due(service, now_utc=now_utc)
            tick = service.tick(now_utc=now_utc, limit=limit, owner=owner_value)
            started: list[dict[str, Any]] = []
            for result in tick.get("results") or []:
                if str(result.get("disposition")) != "CLAIMED":
                    continue
                started.append(self._hand_off(service, result, origin=owner_value, now_utc=now_utc))
        finally:
            connection.close()
        return {
            "owner": owner_value,
            "materialised": materialised,
            "considered": tick.get("considered"),
            "claimed": tick.get("claimed_count"),
            "deferred": tick.get("deferred_count"),
            "started": started,
            "dispositions": [
                {"occurrence_id": item.get("occurrence_id"), "disposition": item.get("disposition")}
                for item in tick.get("results") or []
            ],
            "browser_timer_used": False,
            "external_scheduler_used": False,
        }

    def _materialise_due(self, service: ExplainerScheduleService, *, now_utc: Any) -> list[dict[str, Any]]:
        """Extend the occurrence horizon for every active schedule."""

        schedules = service.list_schedules(status="ACTIVE")
        results: list[dict[str, Any]] = []
        for schedule in schedules:
            try:
                results.append(
                    service.materialise_occurrences(
                        schedule_id=str(schedule["id"]), now_utc=now_utc
                    )
                )
            except ExplainerContractError as error:
                results.append({"schedule_id": str(schedule["id"]), "error": error.code})
        return results

    # ------------------------------------------------------------------ handoff
    def _hand_off(
        self,
        service: ExplainerScheduleService,
        result: Mapping[str, Any],
        *,
        origin: str,
        now_utc: Any = None,
    ) -> dict[str, Any]:
        occurrence_id = str(result.get("occurrence_id"))
        claim = result.get("claim") or {}
        lease_token = str(claim.get("lease_token") or "")
        fencing_token = int(claim.get("fencing_token") or 0)

        def heartbeat() -> None:
            service.heartbeat_occurrence(
                occurrence_id=occurrence_id,
                lease_token=lease_token,
                fencing_token=fencing_token,
                lease_seconds=self.lease_seconds,
                now_utc=now_utc,
            )

        try:
            project_id = self._resolve_project(service, occurrence_id)
        except ExplainerContractError as error:
            service.release_occurrence(
                occurrence_id=occurrence_id,
                lease_token=lease_token,
                fencing_token=fencing_token,
                status="SKIPPED_WITH_REASON",
                skip_reason=error.message,
                error_code=error.code,
                now_utc=now_utc,
            )
            return {"occurrence_id": occurrence_id, "started": False, "reason": error.code}
        # The project exists; keep the lease alive across the preflight, which is
        # the long part of a scheduled production.
        heartbeat()
        try:
            plan = self.build_production().preflight(
                project_id=project_id, outputs=self._schedule_outputs(service, occurrence_id)
            )
        except (ExplainerContractError, DomainRuleError) as error:
            code = getattr(error, "code", "PREFLIGHT_FAILED")
            service.release_occurrence(
                occurrence_id=occurrence_id,
                lease_token=lease_token,
                fencing_token=fencing_token,
                status="FAILED",
                error_code=code,
                error_detail_redacted=getattr(error, "message", str(error))[:400],
                now_utc=now_utc,
            )
            return {"occurrence_id": occurrence_id, "started": False, "reason": code}
        if not plan["executable"]:
            codes = sorted({item["code"] for item in plan["blockers"]})
            service.release_occurrence(
                occurrence_id=occurrence_id,
                lease_token=lease_token,
                fencing_token=fencing_token,
                status="SKIPPED_WITH_REASON",
                skip_reason="预检未通过：" + "、".join(codes),
                error_code=ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value,
                now_utc=now_utc,
            )
            return {"occurrence_id": occurrence_id, "started": False, "reason": "PREFLIGHT_BLOCKED", "blockers": codes}
        heartbeat()
        workflow = self.build_production().ensure_workflow(
            project_id=project_id, video_id=plan["video_id"], actor=f"schedule:{origin}"
        )
        handoff = service.build_workflow_handoff(
            occurrence_id=occurrence_id,
            lease_token=lease_token,
            fencing_token=fencing_token,
            workflow_id=str(workflow["id"]),
            plan_hash=str(workflow["plan_hash"]),
            actor=f"schedule:{origin}",
            now_utc=now_utc,
        )
        run = self.build_workflow_service().start_run(
            handoff["workflow_id"],
            plan_hash=handoff["plan_hash"],
            idempotency_key=handoff["idempotency_key"],
            actor=handoff["actor"],
        )
        # The trigger point stays CLAIMED/RUNNING with its lease for as long as the
        # production is active: that is what makes the per-schedule concurrency
        # limit real, and the concurrency gate counts it accordingly.  Settling it
        # to COMPLETED is not this layer's job — the production it handed off owns
        # the terminal fact.
        return {
            "occurrence_id": occurrence_id,
            "started": True,
            "project_id": project_id,
            "workflow_run_id": run.get("id"),
            "idempotency_key": handoff["idempotency_key"],
            "plan_hash": handoff["plan_hash"],
            "occurrence_state": "CLAIMED_AND_HANDED_OFF",
        }

    def _resolve_project(self, service: ExplainerScheduleService, occurrence_id: str) -> str:
        """Find the explainer project this trigger point produces into.

        A schedule bound to a project uses it.  A channel-level schedule creates a
        new explainer project through the ordinary creation path, so scheduled
        production is not a private bypass of the normal project/video contract.
        """

        occurrence = service.repo.get("schedule_occurrences", occurrence_id)
        project_id = str(occurrence.get("project_id") or "").strip()
        schedule = service.repo.get("explainer_schedules", str(occurrence["schedule_id"]))
        if not project_id:
            project_id = str(schedule.get("project_id") or "").strip()
        if project_id:
            return project_id
        return self._create_scheduled_project(schedule, occurrence)

    def _create_scheduled_project(self, schedule: Mapping[str, Any], occurrence: Mapping[str, Any]) -> str:
        topic_scope = str(schedule.get("topic_scope") or "").strip()
        if not topic_scope:
            raise ExplainerContractError(
                ExplainerErrorCode.SOURCE_EVIDENCE_MISSING.value,
                "该栏目计划任务没有绑定项目，也没有配置题材范围，无法确定要生产什么",
                {"schedule_id": str(schedule["id"])},
            )
        durations = schedule.get("durations_json") or {}
        target_seconds = int(durations.get("target_seconds") or 300)
        title = f"{topic_scope.splitlines()[0][:60]}（{str(occurrence.get('scheduled_for'))[:10]}）"
        outputs = schedule.get("outputs_json") or []
        if not outputs:
            outputs = [
                {
                    "edition_key": "zh-clean-169",
                    "voice_locale": "zh-CN",
                    "subtitle_locales": [],
                    "subtitle_mode": "NONE",
                    "aspect_ratio": "16:9",
                    "fps": {"num": 25, "den": 1},
                    "duration_policy": "NATURAL_NARRATION",
                    "allow_soft_subtitle_fallback": False,
                }
            ]
        project_service = self.build_project_service()
        code = f"sched_{str(occurrence['id']).replace('-', '')[:16]}"
        # A channel-level schedule creates the project *and* its video through the
        # same composite command the API uses.  The previous code passed the channel
        # profile bindings to ``ProjectService.create_project`` — which has no such
        # parameter, so it raised ``TypeError: unexpected keyword argument
        # 'channel_profile_id'`` before writing anything — while ``create_video``,
        # which does accept them, got neither.  The two writes were also separate
        # transactions, so a failure in the second left an orphan project.
        from local_drama.application.explainers.commands import (
            ExplainerCreateCommand,
            build_explainer_creation_service,
        )

        first_output = dict(outputs[0])
        fps = first_output.get("fps") or {}
        content_kind = str(
            (schedule.get("durations_json") or {}).get("content_kind") or "FACTUAL_EXPLAINER"
        )
        allowlist = tuple(str(item) for item in (schedule.get("source_allowlist_json") or []))
        # The schedule's own topic scope and source allowlist are the creation
        # input facts: a channel-level occurrence has no imported source document
        # yet, so the scope is frozen into the input projection at creation time and
        # the research mode mirrors whether the schedule declared allowed domains.
        command = ExplainerCreateCommand(
            title=title,
            topic=topic_scope,
            content_kind=content_kind,
            project_code=code,
            input_kind="REFERENCE_LINKS" if allowlist else "TOPIC",
            reference_urls=allowlist,
            duration_mode="TARGET",
            target_seconds=target_seconds,
            tolerance_percent=5.0,
            source_locale=str(first_output.get("voice_locale") or "zh-CN"),
            automation_mode=str(schedule.get("automation_mode") or "AUTO_WITH_EXCEPTIONS"),
            inference_mode="LOCAL_ONLY",
            research_mode="WEB_RESEARCH" if allowlist else "OFFLINE_IMPORT",
            allowed_domains=allowlist,
            channel_profile_id=str(schedule["channel_profile_id"]),
            channel_profile_version_id=str(schedule["channel_profile_version_id"]),
            aspect_ratio=str(first_output.get("aspect_ratio") or "16:9"),
            width=None,
            height=None,
            subtitle_mode=str(first_output.get("subtitle_mode") or "NONE"),
            fps_num=int(fps.get("num") or 25),
            fps_den=int(fps.get("den") or 1),
            outputs=tuple(dict(item) for item in outputs),
            actor=f"schedule:{schedule['id']}",
        )
        # ``occurrence_id`` is the creation command's idempotency identity, so a
        # retried occurrence replays the same workspace instead of creating a second
        # one, and the profile binding is stored on the video row rather than being
        # silently dropped.
        service = build_explainer_creation_service(
            self.database, self.settings, project_service=project_service
        )
        created = service.create_workspace(command, idempotency_key=f"schedule-occurrence:{occurrence['id']}")
        project_id = str(created["project"]["id"])
        with self.database.transaction() as connection:
            ExplainerRepository(connection).update(
                "schedule_occurrences", str(occurrence["id"]), {"project_id": project_id}
            )
        return project_id

    @staticmethod
    def default_schedule_outputs(
        service: ExplainerScheduleService, occurrence_id: str
    ) -> list[dict[str, Any]]:
        """The output editions a schedule produces, falling back to one clean master.

        A schedule that declares no outputs still has to produce something
        renderable, so it falls back to a single subtitle-free 16:9 master in the
        channel's language rather than submitting an empty output list that can
        never pass preflight.
        """

        schedule = service.schedule_for_occurrence(occurrence_id)
        outputs = schedule.get("outputs_json") or []
        if outputs:
            return [dict(item) for item in outputs]
        durations = schedule.get("durations_json") or {}
        return [
            {
                "edition_key": "schedule-clean-169",
                "voice_locale": str(durations.get("voice_locale") or "zh-CN"),
                "subtitle_locales": [],
                "subtitle_mode": "NONE",
                "aspect_ratio": str(durations.get("aspect_ratio") or "16:9"),
                "fps": {"num": int(durations.get("fps_num") or 25), "den": int(durations.get("fps_den") or 1)},
                "duration_policy": "NATURAL_NARRATION",
                "allow_soft_subtitle_fallback": False,
            }
        ]

    def _schedule_outputs(self, service: ExplainerScheduleService, occurrence_id: str) -> list[dict[str, Any]]:
        return self.default_schedule_outputs(service, occurrence_id)


def build_capability_probe(database: Any, settings: Any | None = None) -> Callable[..., dict[str, Any]]:
    """Resolve an explainer requirement through its canonical capability binding.

    Kept as the scheduler-facing factory name; the single implementation lives in
    :mod:`local_drama.application.explainers.capability_binding` so the API layer
    and the scheduled-production path judge readiness identically.  ``settings``
    supplies the first-party local runtimes and the FFmpeg toolchain; without it
    the probe fails closed instead of guessing.
    """

    from local_drama.application.explainers.capability_binding import (
        build_explainer_capability_probe,
    )

    if settings is None:
        def unavailable(capability: str, *, project_id: str) -> dict[str, Any]:
            del project_id
            return {
                "available": False,
                "reason": "CAPABILITY_PROBE_SETTINGS_MISSING",
                "requirement": capability,
                "execution_class": "LOCAL",
            }

        return unavailable

    return build_explainer_capability_probe(database, settings)
