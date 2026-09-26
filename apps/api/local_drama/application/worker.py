"""One-job-at-a-time local media worker backed by the persistent queue."""

from __future__ import annotations

import errno
import sqlite3
import subprocess
import threading
import traceback
from collections import deque
from pathlib import Path
from queue import Empty, Queue
from time import monotonic
from typing import Any, Callable, Mapping

from local_drama.application.adaptation_analysis_execution import AdaptationAnalysisExecutionService
from local_drama.application.automation_workflows import AutomationWorkflowService
from local_drama.application.background_operations import BackgroundOperationService
from local_drama.application.configuration import ConfigurationService
from local_drama.application.dialogue import DialogueService
from local_drama.application.episode_front_half_actions import EpisodeFrontHalfActionService
from local_drama.application.episode_worker_actions import EpisodeWorkerActionService
from local_drama.application.explainers.motion_generation import ExplainerMotionGenerationRuntime
from local_drama.application.explainers.picture_generation import ExplainerPictureGenerationRuntime
from local_drama.application.explainers.production_pipeline import build_explainer_pipeline_handlers
from local_drama.application.explainers.runtime_adapters import (
    ExplainerRepositoryRead,
    ExplainerRepositoryTransaction,
    LocalAiNarrationTtsRuntime,
    LocalAsrAdapter,
    LocalForcedAlignerAdapter,
    MediaServiceNarrationPort,
    QualityPolicyEvaluator,
    build_media_qc_handlers,
    build_planner_factory,
)
from local_drama.application.explainers.visual_generation_completion import (
    build_explainer_visual_completion_service,
)
from local_drama.application.explainers.visual_qc import build_visual_qc_provider
from local_drama.application.explainers.workflow_bridge import (
    is_explainer_workflow_item,
    run_explainer_workflow_step,
)
from local_drama.application.generation import GenerationService
from local_drama.application.job_resources import GpuRuntime, gpu_runtime_for_job
from local_drama.application.jobs import JobService
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.media import MediaService
from local_drama.application.production_choices import ProductionChoiceService
from local_drama.application.production_identity_inputs import (
    ProductionIdentityGenerationCompletionService,
)
from local_drama.application.timeline import TimelineService
from local_drama.application.video_upscale.plans import VideoUpscalePlanService
from local_drama.application.worker_dispatch import WorkerExecution, WorkerHandler, WorkerJobDispatcher
from local_drama.application.worker_handlers.adaptation_analysis import run_adaptation_analysis_job
from local_drama.application.worker_handlers.automation_task import advance_automation_run, run_automation_task
from local_drama.application.worker_handlers.delivery_build import run_delivery_build_job
from local_drama.application.worker_handlers.episode_compose import run_episode_compose_job
from local_drama.application.worker_handlers.experiment_cell import run_experiment_cell
from local_drama.application.worker_handlers.explainer_task import (
    RepositoryExplainerStepStore,
    run_explainer_task,
)
from local_drama.application.worker_handlers.lipsync_job import run_lipsync_job
from local_drama.application.worker_handlers.local_llm_probe import run_local_llm_probe_job
from local_drama.application.worker_handlers.media_derivative import run_media_job
from local_drama.application.worker_handlers.narration_align import run_narration_align_job
from local_drama.application.worker_handlers.narration_tts import run_narration_tts_job
from local_drama.application.worker_handlers.script_breakdown import run_script_breakdown_job
from local_drama.application.worker_handlers.segmented_compose import run_segmented_compose_job
from local_drama.application.worker_handlers.story_pipeline_apply import run_story_pipeline_apply_job
from local_drama.application.worker_handlers.story_pipeline_draft import run_story_pipeline_draft_job
from local_drama.application.worker_handlers.tts_job import run_tts_job
from local_drama.application.worker_handlers.video_enhancement import run_video_enhancement_job
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.adaptation_plan_repository import SqliteAdaptationPlanRepository
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.atomic import write_atomic
from local_drama.infrastructure.local_ai_subprocess import LocalAiSubprocessRuntime
from local_drama.infrastructure.service_composition import (
    build_asset_image_completion,
    build_pipeline_orchestrator,
    build_shot_keyframe_completion,
)
from local_drama.logging_setup import get_logger
from local_drama.model_platform.application.comfy_capability_smoke_execution import ComfyCapabilitySmokeWorker
from local_drama.model_platform.application.execution_job_links import ExecutionJobLinkService, WorkerExecutionSnapshot
from local_drama.model_platform.application.production_execution_registry import production_worker_execution_handlers
from local_drama.model_platform.application.worker_execution_handlers import WorkerExecutionHandlerRegistry
from local_drama.platform import create_platform_services
from local_drama.platform.contracts import TtsRuntime


def _worker_error_detail(error: DomainRuleError) -> str:
    """Persist a bounded, already-redacted diagnostic for operator recovery."""

    details = error.details if isinstance(error.details, dict) else {}
    detail = error.message
    stderr = details.get("stderr_redacted")
    if isinstance(stderr, str) and stderr.strip():
        detail = f"{detail}（stderr：{stderr.strip()}）"
    returncode = details.get("returncode")
    if isinstance(returncode, int):
        detail = f"{detail}；returncode={returncode}"
    path_suffix = details.get("path_suffix")
    path_exists = details.get("path_exists")
    path_size_bytes = details.get("path_size_bytes")
    if path_suffix or path_exists is not None or path_size_bytes is not None:
        detail = (
            f"{detail}；输入文件 suffix={path_suffix or '-'}"
            f", exists={str(bool(path_exists)).lower()}, size_bytes={path_size_bytes}"
        )
    return detail


def llama_cpp_activation_context(snapshot: WorkerExecutionSnapshot) -> dict[str, object]:
    """Translate a frozen llama.cpp V2 snapshot into lease activation facts."""

    if snapshot.adapter_code != "llama.chat.v1" or len(snapshot.model_bindings) != 1:
        raise DomainRuleError(
            "MP_LLAMA_TEXT_MODEL_BINDING_INVALID",
            "托管 llama.cpp V2 Job 必须在租约前冻结一个 GGUF 模型绑定。",
        )
    locator = snapshot.model_bindings[0].get("native_locator")
    if not isinstance(locator, str) or not locator.strip():
        raise DomainRuleError(
            "MP_LLAMA_TEXT_MODEL_BINDING_INVALID",
            "托管 llama.cpp V2 Job 的 GGUF 模型定位符无效。",
        )
    return {"model_locator": locator.strip()}


def _make_delivery_build_handler(
    worker: LocalMediaWorker,
) -> Callable[[dict[str, Any], Path], tuple[str, str]]:
    """Bind the extracted DELIVERY_BUILD business flow to runner-owned ports."""

    def handler(job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        operations = BackgroundOperationService(worker.database, worker.settings)
        return run_delivery_build_job(
            job,
            output_root,
            work_root=worker.settings.work_root,
            delivery_planner=operations,
            delivery_builder=worker._timeline_service(),
            atomic_writer=worker._atomic_file,
        )

    return handler


def _make_video_upscale_preflight_handler(
    worker: LocalMediaWorker,
    plan_factory: Callable[[Any, Settings], VideoUpscalePlanService] = VideoUpscalePlanService,
) -> Callable[[dict[str, Any], Path], tuple[str, str]]:
    """Run source integrity/probe/geometry checks without contacting a GPU."""

    def handler(job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        snapshot = job.get("input_snapshot") or {}
        return plan_factory(worker.database, worker.settings).run_preflight(
            str(snapshot["plan_id"]), output_root
        )

    return handler


def _make_local_llm_probe_handler(
    worker: LocalMediaWorker,
) -> Callable[[dict[str, Any], Path], tuple[str, str]]:
    """Bind the extracted LOCAL_LLM_PROBE flow to runner-owned ports."""

    def handler(job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        return run_local_llm_probe_job(
            job,
            output_root,
            work_root=worker.settings.work_root,
            local_llm=LocalLLMService(worker.database, worker.settings),
            atomic_writer=worker._atomic_file,
            cancel_check=worker._cancel_requested,
            report_progress=worker._report_progress,
        )

    return handler


def _make_adaptation_analysis_handler(
    worker: LocalMediaWorker,
) -> Callable[[dict[str, Any], Path], tuple[str, str]]:
    def handler(job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        def on_progress(progress: dict[str, Any]) -> None:
            worker._report_progress(progress)

        return run_adaptation_analysis_job(
            job,
            output_root,
            work_root=worker.settings.work_root,
            analysis=AdaptationAnalysisExecutionService(
                SqliteAdaptationPlanRepository(worker.database, worker.settings),
                worker.settings,
                LocalLLMService(worker.database, worker.settings),
                database=worker.database,
            ),
            atomic_writer=worker._atomic_file,
            on_progress=on_progress,
        )

    return handler


def _make_timeline_job_handler(
    worker: LocalMediaWorker,
    run_job: Callable[..., tuple[str, str]],
) -> Callable[[dict[str, Any], Path], tuple[str, str]]:
    """Bind an extracted timeline-family handler to runner-owned ports."""

    def handler(job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        return run_job(
            job,
            output_root,
            work_root=worker.settings.work_root,
            timeline_factory=worker._timeline_service,
            atomic_writer=worker._atomic_file,
        )

    return handler


def _make_media_job_handler(
    worker: LocalMediaWorker,
) -> Callable[[dict[str, Any], Path], tuple[str, str]]:
    """Bind the extracted MEDIA_* flows to runner-owned ports."""

    def handler(job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        return run_media_job(
            job,
            output_root,
            work_root=worker.settings.work_root,
            cache_root=worker.settings.cache_root,
            media_ops=worker.media,
            run_ffmpeg=worker._ffmpeg,
            set_expected_duration=worker._set_expected_duration_ms,
            atomic_writer=worker._atomic_file,
        )

    return handler


def _automation_item_payload(job: dict[str, Any], database: Any) -> dict[str, Any]:
    """The batch item payload of one ``AUTOMATION_WORKFLOW_TASK`` job.

    The generic dispatcher needs the item's payload to decide whether the task is a
    drama action or an explainer step, so it is read here once, before either
    branch runs.  A read failure is not fatal: the drama branch then reports the
    payload problem itself with its own error code.
    """

    import json

    snapshot = job.get("input_snapshot") or {}
    run_id = str(snapshot.get("automation_run_id") or "")
    task_id = str(snapshot.get("automation_task_id") or "")
    if not run_id or not task_id:
        return {}
    with database.connect() as connection:
        row = connection.execute(
            "SELECT item_json FROM automation_workflow_run_tasks WHERE id=? AND run_id=?",
            (task_id, run_id),
        ).fetchone()
    if row is None:
        return {}
    try:
        item = json.loads(str(row["item_json"] or "{}"))
    except (TypeError, ValueError):
        return {}
    payload = item.get("payload") if isinstance(item, dict) else None
    return dict(payload) if isinstance(payload, Mapping) else {}


def _is_database_lock_error(error: BaseException) -> bool:
    """Whether a failure is the local database's transient single-writer contention."""

    if isinstance(error, sqlite3.OperationalError):
        return "locked" in str(error).lower()
    # The job service may wrap the driver error; the message is then the only signal,
    # and a wrapped "database is locked" is still contention rather than a lost lease.
    text = f"{error} {getattr(error, 'message', '')}".lower()
    return "database is locked" in text


def _make_explainer_task_provider(
    worker: LocalMediaWorker,
) -> Callable[[dict[str, Any], Path], tuple[str, str, dict[str, Any], int]]:
    """Bind the explainer stage family to the runner-owned ports.

    The explainer graph is one automation workflow run plus one ``EXPLAINER_TASK``
    job per stage.  The step projection is written through short transactions so a
    GPU wait never holds a SQLite lock, the machine policy decision has exactly
    one writer (the dedicated policy evaluator), and the owning workflow run is
    advanced by the dispatcher only for a report-carrying family, i.e. after the
    report artifact and the projection are both durable.
    """

    def handler(job: dict[str, Any], output_root: Path) -> tuple[str, str, dict[str, Any], int]:
        return run_explainer_task(
            job,
            output_root,
            work_root=worker.settings.work_root,
            atomic_writer=worker._atomic_file,
            handlers=build_media_qc_handlers(
                planner_factory=build_planner_factory(worker.database, worker.settings),
                repo_factory=lambda: ExplainerRepositoryTransaction(worker.database),
                read_repo_factory=lambda: ExplainerRepositoryRead(worker.database),
                settings=worker.settings,
                visual_provider=build_visual_qc_provider(
                    worker.database,
                    worker.settings,
                    frame_root=worker.settings.explainer_frames_root,
                ),
                media_content_path=lambda media_version_id: worker.media.content_path(media_version_id)[1],
                extra_handlers=build_explainer_pipeline_handlers(
                    repo_factory=lambda: ExplainerRepositoryTransaction(worker.database),
                    repo_factory_read=lambda: ExplainerRepositoryRead(worker.database),
                    database=worker.database,
                    settings=worker.settings,
                    media_service=worker.media,
                    atomic_writer=worker._atomic_file,
                    narration_runtime=(
                        None
                        if worker.voxcpm_runtime is None
                        else LocalAiNarrationTtsRuntime(worker.voxcpm_runtime, media=worker.media)
                    ),
                    media_ops=MediaServiceNarrationPort(worker.media),
                    aligner=LocalForcedAlignerAdapter(worker.voxcpm_runtime),
                    asr=LocalAsrAdapter(worker.voxcpm_runtime),
                    work_root=worker.settings.work_root,
                    picture_runtime=ExplainerPictureGenerationRuntime(
                        worker.database,
                        worker.settings,
                        gpu_coordinator=worker.gpu_coordinator,
                    ),
                    motion_runtime=ExplainerMotionGenerationRuntime(
                        worker.database,
                        worker.settings,
                        gpu_coordinator=worker.gpu_coordinator,
                    ),
                ),
            ),
            step_store=RepositoryExplainerStepStore(
                repo_factory=lambda: ExplainerRepositoryTransaction(worker.database),
            ),
            cancel_check=worker._cancel_requested,
            policy_evaluator=QualityPolicyEvaluator(worker.database),
        )
    return handler


def _make_narration_tts_handler(
    worker: LocalMediaWorker,
) -> Callable[[dict[str, Any], Path], tuple[str, str]]:
    """Bind the explainer NARRATION_TTS flow to the offline speech runtime.

    The explainer narration flow is deliberately separate from the legacy
    ``TTS_GENERATION`` dialogue flow: it reads narration revisions instead of
    dialogue text revisions and never relies on the legacy ``LOCAL_TEST_ONLY``
    snapshot gate.  Both share the same runtime and media ports.
    """

    def handler(job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        if worker.voxcpm_runtime is None:
            raise DomainRuleError(
                "NARRATION_RUNTIME_UNAVAILABLE",
                "本机未配置离线 VoxCPM2 子进程运行时，无法生成解说旁白",
            )
        return run_narration_tts_job(
            job,
            output_root,
            work_root=worker.settings.work_root,
            database=worker.database,
            narration_runtime=LocalAiNarrationTtsRuntime(worker.voxcpm_runtime, media=worker.media),
            media_ops=MediaServiceNarrationPort(worker.media),
            atomic_writer=worker._atomic_file,
        )

    return handler


def _make_narration_align_handler(
    worker: LocalMediaWorker,
) -> Callable[[dict[str, Any], Path], tuple[str, str]]:
    """Bind the explainer NARRATION_ALIGN flow to the offline aligner/ASR runtimes."""

    def handler(job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        return run_narration_align_job(
            job,
            output_root,
            work_root=worker.settings.work_root,
            database=worker.database,
            aligner=LocalForcedAlignerAdapter(worker.voxcpm_runtime),
            media_ops=MediaServiceNarrationPort(worker.media),
            atomic_writer=worker._atomic_file,
            asr=LocalAsrAdapter(worker.voxcpm_runtime),
        )

    return handler


def _make_tts_job_handler(
    worker: LocalMediaWorker,
) -> Callable[[dict[str, Any], Path], tuple[str, str]]:
    """Bind the extracted TTS_GENERATION flow to runner-owned ports."""

    def handler(job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        return run_tts_job(
            job,
            output_root,
            work_root=worker.settings.work_root,
            database=worker.database,
            tts_runtime=worker.tts_runtime,
            media_ops=worker.media,
            run_ffmpeg=worker._ffmpeg,
            atomic_writer=worker._atomic_file,
            voxcpm_runtime=worker.voxcpm_runtime,
        )

    return handler


def _make_lipsync_job_handler(
    worker: LocalMediaWorker,
) -> Callable[[dict[str, Any], Path], tuple[str, str]]:
    """Bind the extracted LIPSYNC_GENERATION flow to runner-owned ports."""

    def handler(job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        return run_lipsync_job(
            job,
            output_root,
            work_root=worker.settings.work_root,
            database=worker.database,
            lipsync_runtime=worker.voxcpm_runtime,
            media_ops=worker.media,
            atomic_writer=worker._atomic_file,
        )

    return handler


def _make_experiment_cell_handler(
    worker: LocalMediaWorker,
) -> Callable[[dict[str, Any], Path], tuple[str, str]]:
    """Bind the extracted EXPERIMENT_CELL flow to runner-owned ports."""

    def handler(job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        return run_experiment_cell(
            job,
            output_root,
            work_root=worker.settings.work_root,
            database=worker.database,
            generation_ops=GenerationService(worker.database, worker.settings),
            atomic_writer=worker._atomic_file,
        )

    return handler


def _make_story_pipeline_draft_handler(
    worker: LocalMediaWorker,
) -> Callable[[dict[str, Any], Path], tuple[str, str]]:
    """Bind the persistent story-draft workflow to queue-owned lifecycle ports."""

    def handler(job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        return run_story_pipeline_draft_job(
            job,
            output_root,
            work_root=worker.settings.work_root,
            pipeline=build_pipeline_orchestrator(worker.database, worker.settings),
            atomic_writer=worker._atomic_file,
            cancel_check=worker._cancel_requested,
            report_progress=worker._report_progress,
        )

    return handler


def _make_story_pipeline_apply_handler(
    worker: LocalMediaWorker,
) -> Callable[[dict[str, Any], Path], tuple[str, str]]:
    """Invoke the normal application command from a queue-owned continuation."""

    def handler(job: dict[str, Any], output_root: Path) -> tuple[str, str]:
        return run_story_pipeline_apply_job(
            job,
            output_root,
            work_root=worker.settings.work_root,
            pipeline=build_pipeline_orchestrator(worker.database, worker.settings),
            atomic_writer=worker._atomic_file,
        )

    return handler


# Declarative registry: job type -> business handler factory.
# Every queue-dispatched job family's business flow lives under
# application/worker_handlers; the runner binds each flow to its ports through
# _EXTRACTED_HANDLER_PROVIDERS at dispatch time (design §13.2).
#
# Most families return ``(kind, relative_path)``.  A report-carrying family
# returns ``(kind, relative_path, report, produced_bytes)`` so the dispatcher can
# advance its owning automation workflow run (see ``_dispatcher``).
_EXTRACTED_HANDLER_RESULT = tuple[str, str] | tuple[str, str, dict[str, Any], int]
_EXTRACTED_HANDLER_PROVIDERS: dict[
    str, Callable[[LocalMediaWorker], Callable[..., _EXTRACTED_HANDLER_RESULT]]
] = {
    "VIDEO_UPSCALE_PREFLIGHT": _make_video_upscale_preflight_handler,
    "DELIVERY_BUILD": _make_delivery_build_handler,
    "EPISODE_COMPOSE": lambda worker: _make_timeline_job_handler(worker, run_episode_compose_job),
    "SEGMENTED_EPISODE_COMPOSE": lambda worker: _make_timeline_job_handler(worker, run_segmented_compose_job),
    "VIDEO_ENHANCEMENT": lambda worker: _make_timeline_job_handler(worker, run_video_enhancement_job),
    "LOCAL_LLM_PROBE": _make_local_llm_probe_handler,
    "ADAPTATION_ANALYSIS_LOCAL_LLM": _make_adaptation_analysis_handler,
    "MEDIA_DERIVATIVE": _make_media_job_handler,
    "MEDIA_THUMBNAIL": _make_media_job_handler,
    "MEDIA_PROXY": _make_media_job_handler,
    "TTS_GENERATION": _make_tts_job_handler,
    "NARRATION_TTS": _make_narration_tts_handler,
    "NARRATION_ALIGN": _make_narration_align_handler,
    # The explainer graph runs several business stages through one job family.
    # This provider returns the 4-tuple (report-carrying) handler shape; the
    # dispatcher adapts it exactly like the other extracted families.
    "EXPLAINER_TASK": _make_explainer_task_provider,
    # A stage job created from an explainer step carries the stage code as its job
    # type, so every text/QC stage code is registered explicitly here.  Aliasing
    # them onto the same provider keeps one dispatcher and one report shape while
    # guaranteeing the job is actually claimed by a worker instead of sitting in
    # the queue with no registered handler.
    **{
        stage_code: _make_explainer_task_provider
        for stage_code in (
            "RESEARCH_ACQUIRE",
            "FACT_EXTRACT",
            "NARRATION_WRITE",
            "EXPLAINER_STORYBOARD",
            "EXPLAINER_VISUAL_QC",
            "COMPOSITION_QC",
            "EXPLAINER_POLICY_EVALUATE",
        )
    },
    "LIPSYNC_GENERATION": _make_lipsync_job_handler,
    "EXPERIMENT_CELL": _make_experiment_cell_handler,
    "STORY_PIPELINE_DRAFT": _make_story_pipeline_draft_handler,
    "STORY_PIPELINE_APPLY": _make_story_pipeline_apply_handler,
}


class LocalMediaWorker:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        tts_runtime: TtsRuntime | None = None,
        *,
        gpu_coordinator: Any | None = None,
        model_execution_handlers: WorkerExecutionHandlerRegistry | None = None,
        comfy_smoke_worker_factory: Callable[[], ComfyCapabilitySmokeWorker] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.jobs = JobService(database, settings)
        self.media = MediaService(database, settings, ffmpeg_runner=self._ffmpeg)
        self.tts_runtime = tts_runtime or create_platform_services(settings).tts_runtime
        self.voxcpm_runtime = (
            LocalAiSubprocessRuntime(settings) if settings.local_ai_python is not None else None
        )
        self.gpu_coordinator = gpu_coordinator
        self.model_execution_handlers = model_execution_handlers or production_worker_execution_handlers(
            settings,
            database,
            cancel_check=self._cancel_requested,
            report_progress=self._report_progress,
            attempt_context=self._current_attempt_context,
        )
        self.comfy_smoke_worker_factory = comfy_smoke_worker_factory or (lambda: ComfyCapabilitySmokeWorker(database, settings))
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

    def _run_model_platform_execution(self, job: dict[str, Any], output_root: Path) -> WorkerExecution:
        """Execute only the snapshot linked to the claimed V2 Job."""
        snapshot = ExecutionJobLinkService(self.database).load_for_worker(str(job["id"]))
        from local_drama.model_platform.application.project_knowledge_indexing import ProjectKnowledgeIndexCompletionService
        from local_drama.model_platform.application.quick_create_v2_runs import QuickCreateV2RunService

        completion = ProjectKnowledgeIndexCompletionService(self.database, self.settings)
        quick_create = QuickCreateV2RunService(self.database)
        try:
            if self._report_progress({"phase": "MODEL_EXECUTION", "percent": 25}, force=True):
                raise DomainRuleError("JOB_CANCELLED", "V2 模型执行已取消，不会调用 adapter。")
            kind, relative_path = self.model_execution_handlers.execute(snapshot, output_root)
        except DomainRuleError as error:
            completion.record_failure_if_project_knowledge_batch(snapshot, error.code)
            raise

        def after_artifacts_registered(artifacts: tuple[dict[str, Any], ...]) -> None:
            completion.record_if_project_knowledge_batch(snapshot, artifacts)
            quick_create.record_artifacts_for_job(str(job["id"]), artifacts)

        return WorkerExecution(
            kind,
            relative_path,
            after_artifacts_registered=after_artifacts_registered,
        )

    def _run_comfy_capability_smoke(self, job: dict[str, Any], output_root: Path) -> WorkerExecution:
        return self.comfy_smoke_worker_factory().execute(job, output_root)

    def _set_expected_duration_ms(self, duration_ms: int | None) -> None:
        """Own the FFmpeg progress state that handlers feed before encoding."""
        self._ffmpeg_expected_duration_ms = duration_ms

    def _run_with_lease_heartbeat(
        self,
        handler: Callable[[dict[str, Any], Path], Any],
        job: dict[str, Any],
        output_root: Path,
        *,
        attempt_id: str,
        token: str,
        worker_id: str,
        lease_seconds: int = 240,
        interval_seconds: float = 20.0,
    ) -> Any:
        """Run one long explainer stage while a dedicated thread renews its lease.

        The explainer text stages call the local LLM, and a 27B local model can take
        minutes for one long script.  Progress reporting is what renews a lease, and
        a stage handler has nothing truthful to report while it waits — so the
        previous behaviour let the 60-second lease expire mid-call, the reconciler
        re-queued the job, and the same stage ran twice.  This runs the blocking work
        on its own thread and renews the lease from the runner's own thread until it
        settles.
        """

        outcome: dict[str, Any] = {}
        finished = threading.Event()

        def work() -> None:
            try:
                outcome["value"] = handler(job, output_root)
            except BaseException as error:  # re-raised on the runner thread
                outcome["error"] = error
            finally:
                finished.set()

        thread = threading.Thread(
            target=work, name=f"explainer-stage-{str(job.get('id'))[:8]}", daemon=True
        )
        thread.start()
        get_logger("worker").info(
            "explainer_lease_heartbeat_started job_id=%s attempt_id=%s lease_seconds=%s interval=%s",
            str(job.get("id")),
            attempt_id,
            lease_seconds,
            interval_seconds,
        )
        # A transient ``database is locked`` on this renewal must not end it.  The
        # local database has one writer: another job of the same worker (a media
        # derivative import, a render) can hold a write transaction for longer than
        # the connection's busy timeout, and the renewal used to give up on the first
        # such error — the 1962 film's COMPOSITION_QC was orphaned three times in a
        # row with WORKER_LEASE_EXPIRED because of exactly that.  Lock contention is
        # retried with a short backoff; every other error (a lost lease, a cancelled
        # attempt) still stops the thread immediately.
        lock_retry_seconds = 3.0
        max_lock_retries = 12
        lock_retries = 0
        next_heartbeat = monotonic() + float(interval_seconds)
        try:
            while not finished.wait(max(0.25, next_heartbeat - monotonic())):
                try:
                    heartbeat = self.jobs.heartbeat(
                        attempt_id,
                        token,
                        worker_id,
                        progress={**self._active_progress, "phase": "EXPLAINER_STAGE"},
                        lease_seconds=int(lease_seconds),
                    )
                except BaseException as error:
                    get_logger("worker").warning(
                        "explainer_lease_heartbeat_failed job_id=%s attempt_id=%s error=%s",
                        str(job.get("id")),
                        attempt_id,
                        f"{type(error).__name__}: {getattr(error, 'code', '')} {getattr(error, 'message', error)}",
                    )
                    if not _is_database_lock_error(error) or lock_retries >= max_lock_retries:
                        break
                    lock_retries += 1
                    next_heartbeat = monotonic() + lock_retry_seconds
                    continue
                lock_retries = 0
                next_heartbeat = monotonic() + float(interval_seconds)
                if heartbeat.get("cancel_requested"):
                    # The handler observes cancellation through its own port; the
                    # runner only stops renewing so a cancelled job can be reclaimed.
                    break
        finally:
            thread.join()
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value")

    def _lease_heartbeat_wrapper(
        self,
        handler: WorkerHandler,
        *,
        attempt_id: str,
        token: str,
        worker_id: str,
    ) -> WorkerHandler:
        """Wrap one long explainer job family so it cannot lose its lease."""

        def execute(job: dict[str, Any], output_root: Path) -> WorkerExecution:
            return self._run_with_lease_heartbeat(
                handler,
                job,
                output_root,
                attempt_id=attempt_id,
                token=token,
                worker_id=worker_id,
            )

        return execute

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

    def _current_attempt_context(self) -> tuple[str, str, str] | None:
        return self._active_job_context

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
        write_atomic(path, writer)

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
            result = self._execution(
                run_script_breakdown_job(
                    job,
                    output_root,
                    work_root=self.settings.work_root,
                    local_llm=LocalLLMService(self.database, self.settings),
                    atomic_writer=self._atomic_file,
                    on_progress=heartbeat_progress,
                )
            )
            if heartbeat_errors:
                raise DomainRuleError("JOB_HEARTBEAT_FAILED", "AI 拆解 Job lease 续期失败")
            return result
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2.0)

    def _explainer_handlers(self) -> dict[str, Any]:
        """Every explainer business stage this worker can execute, merged once.

        Used by the explainer workflow dispatch so one workflow item executes
        exactly one planned step through the same handler set a standalone stage
        job would use.
        """

        return build_media_qc_handlers(
            planner_factory=build_planner_factory(self.database, self.settings),
            repo_factory=lambda: ExplainerRepositoryTransaction(self.database),
            read_repo_factory=lambda: ExplainerRepositoryRead(self.database),
            settings=self.settings,
            visual_provider=build_visual_qc_provider(
                self.database,
                self.settings,
                frame_root=self.settings.explainer_frames_root,
            ),
            media_content_path=lambda media_version_id: self.media.content_path(media_version_id)[1],
            extra_handlers=build_explainer_pipeline_handlers(
                repo_factory=lambda: ExplainerRepositoryTransaction(self.database),
                repo_factory_read=lambda: ExplainerRepositoryRead(self.database),
                database=self.database,
                settings=self.settings,
                media_service=self.media,
                atomic_writer=self._atomic_file,
                narration_runtime=(
                    None
                    if self.voxcpm_runtime is None
                    else LocalAiNarrationTtsRuntime(self.voxcpm_runtime, media=self.media)
                ),
                media_ops=MediaServiceNarrationPort(self.media),
                aligner=LocalForcedAlignerAdapter(self.voxcpm_runtime),
                asr=LocalAsrAdapter(self.voxcpm_runtime),
                work_root=self.settings.work_root,
                picture_runtime=ExplainerPictureGenerationRuntime(
                    self.database,
                    self.settings,
                    gpu_coordinator=self.gpu_coordinator,
                ),
                motion_runtime=ExplainerMotionGenerationRuntime(
                    self.database,
                    self.settings,
                    gpu_coordinator=self.gpu_coordinator,
                ),
            ),
        )

    def _dispatcher(
        self,
        *,
        attempt_id: str,
        token: str,
        worker_id: str,
    ) -> WorkerJobDispatcher:
        simple_handlers = {
            job_type: provider(self)
            for job_type, provider in _EXTRACTED_HANDLER_PROVIDERS.items()
        }

        def adapt(handler: Callable[..., _EXTRACTED_HANDLER_RESULT]) -> WorkerHandler:
            def execute(job: dict[str, Any], root: Path) -> WorkerExecution:
                result = handler(job, root)
                if len(result) == 2:
                    return self._execution(result)
                # Report-carrying family: the owning workflow run advances only
                # after the handler registered its artifact and report.
                kind, relative, report, produced_bytes = result  # type: ignore[misc]
                return self._execution_with_report(
                    job,
                    kind,
                    relative,
                    report,
                    produced_bytes,
                    worker_id=worker_id,
                )

            return execute

        handlers: dict[str, WorkerHandler] = {
            job_type: adapt(handler) for job_type, handler in simple_handlers.items()
        }
        # Every explainer business stage is a long local stage (LLM, TTS, render) and
        # must keep its lease alive while it works.
        explainer_job_types = {
            "EXPLAINER_TASK",
            "NARRATION_TTS",
            "NARRATION_ALIGN",
            *(
                stage_code
                for stage_code, provider in _EXTRACTED_HANDLER_PROVIDERS.items()
                if provider is _make_explainer_task_provider
            ),
        }
        for job_type in sorted(explainer_job_types):
            if job_type in handlers:
                handlers[job_type] = self._lease_heartbeat_wrapper(
                    handlers[job_type],
                    attempt_id=attempt_id,
                    token=token,
                    worker_id=worker_id,
                )
        handlers["CPU_TEST"] = lambda job, root: self._run_cpu_test(job, root, worker_id)
        handlers["MODEL_PLATFORM_EXECUTION"] = self._run_model_platform_execution
        handlers["MODEL_PLATFORM_COMFY_SMOKE"] = self._run_comfy_capability_smoke
        handlers["SCRIPT_BREAKDOWN_LOCAL_LLM"] = lambda job, root: self._run_script_breakdown_with_heartbeat(
            job, root, attempt_id=attempt_id, token=token, worker_id=worker_id,
        )

        def automation(job: dict[str, Any], root: Path) -> WorkerExecution:
            if is_explainer_workflow_item(_automation_item_payload(job, self.database)):
                # The explainer graph rides the same declarative workflow but its
                # batch items carry a planned step code instead of an episode
                # action, so they dispatch to the explainer task family and the
                # report-carrying path that advances the owning run.
                def run_step() -> tuple[str, str, dict[str, Any], int]:
                    return run_explainer_workflow_step(
                        job,
                        root,
                        database=self.database,
                        work_root=self.settings.work_root,
                        worker_id=worker_id,
                        handlers=self._explainer_handlers(),
                        step_store=RepositoryExplainerStepStore(
                            repo_factory=lambda: ExplainerRepositoryTransaction(self.database),
                        ),
                        atomic_writer=self._atomic_file,
                        policy_evaluator=QualityPolicyEvaluator(self.database),
                        cancel_check=self._cancel_requested,
                    )

                kind, relative, report, produced_bytes = self._run_with_lease_heartbeat(
                    lambda _job, _root: run_step(),
                    job,
                    root,
                    attempt_id=attempt_id,
                    token=token,
                    worker_id=worker_id,
                )
                return self._execution_with_report(
                    job, kind, relative, report, produced_bytes, worker_id=worker_id
                )
            kind, relative, report, produced_bytes = run_automation_task(
                job,
                root,
                worker_id=worker_id,
                work_root=self.settings.work_root,
                database=self.database,
                front_half_actions_factory=lambda: EpisodeFrontHalfActionService(self.database, self.settings),
                episode_worker_actions_factory=lambda: EpisodeWorkerActionService(self.database, self.settings),
                dialogue_factory=lambda: DialogueService(self.database, self.settings, jobs=self.jobs, media=self.media),
                configuration_factory=lambda: ConfigurationService(self.database),
                timeline_factory=self._timeline_service,
                atomic_writer=self._atomic_file,
                production_choice_factory=lambda: ProductionChoiceService(self.database, self.settings),
            )
            return WorkerExecution(kind, relative, report, produced_bytes)

        handlers["AUTOMATION_WORKFLOW_TASK"] = automation
        return WorkerJobDispatcher(handlers)

    def _execution_with_report(
        self,
        job: dict[str, Any],
        kind: str,
        relative: str,
        report: dict[str, Any],
        produced_bytes: int,
        *,
        worker_id: str,
    ) -> WorkerExecution:
        """Wrap a report-carrying explainer handler result and advance its run.

        Only the explainer graph goes through here.  The drama automation family
        advances its own run inside ``run_automation_task`` (it has to, because
        the next task must wait on the real child Jobs), so calling this for it
        would step the same run twice.
        """

        del worker_id
        advance_error = advance_automation_run(
            job,
            report,
            produced_bytes,
            workflow_steps=AutomationWorkflowService(self.database),
        )
        if advance_error:
            report = {**report, "workflow_advance_error": advance_error}
        return WorkerExecution(kind, relative, report, produced_bytes)

    def _execution(self, result: Any) -> WorkerExecution:
        return WorkerExecution(result[0], result[1])

    def _gpu_activation_context(
        self,
        job: dict[str, Any],
        runtime: GpuRuntime | None,
    ) -> dict[str, object] | None:
        """Resolve immutable runtime activation facts before entering a lease."""

        if runtime is not GpuRuntime.LLAMA_CPP or str(job.get("type") or "") != "MODEL_PLATFORM_EXECUTION":
            return None
        snapshot = ExecutionJobLinkService(self.database).load_for_worker(str(job["id"]))
        return llama_cpp_activation_context(snapshot)

    def _wait_for_gpu(self) -> None:
        if self._report_progress({"phase": "WAITING_FOR_GPU"}, force=True):
            raise DomainRuleError("JOB_CANCELLED", "等待显卡期间已取消，不会启动模型")

    def run_once(
        self,
        worker_id: str,
        channels: list[str] | None = None,
        *,
        worker_session_id: str | None = None,
        job_types: list[str] | None = None,
    ) -> dict[str, Any] | None:
        claim = self.jobs.claim(
            worker_id,
            channels or ["CPU"],
            worker_session_id=worker_session_id,
            job_types=job_types,
        )
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
        self._set_expected_duration_ms(None)
        try:
            # An explainer stage runs for minutes and its first lease renewal must
            # land before the claim's lease runs out.  The claim used to hand every
            # job a 60 s lease while the renewal thread asked for 240 s, so a single
            # failed renewal (measured: ``database is locked`` while another job
            # held a write transaction) orphaned the whole stage.
            initial_lease_seconds = (
                120 if job["type"] in {"SCRIPT_BREAKDOWN_LOCAL_LLM", "EXPLAINER_TASK"} else 60
            )
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
            dispatcher = self._dispatcher(attempt_id=attempt_id, token=token, worker_id=worker_id)
            runtime = gpu_runtime_for_job(job)
            activation_context = self._gpu_activation_context(job, runtime)
            if runtime is not None and self.gpu_coordinator is not None:
                with self.gpu_coordinator.session(
                    runtime,
                    owner_kind="JOB_ATTEMPT",
                    owner_ref=attempt_id,
                    retain_if_same_runtime_waiting=True,
                    activation_context=activation_context,
                    on_wait=self._wait_for_gpu,
                ):
                    if self._report_progress({"phase": "EXECUTING"}, force=True):
                        raise DomainRuleError("JOB_CANCELLED", "已取消，不会启动模型任务")
                    execution = dispatcher.execute(job, output_root)
            else:
                execution = dispatcher.execute(job, output_root)
            business_progress_complete = int(self._active_progress.get("percent") or 0) >= 100
            if not business_progress_complete:
                if self._report_progress({"phase": "VERIFYING_OUTPUT", "percent": 92}, force=True):
                    raise DomainRuleError("JOB_CANCELLED", "后台任务已取消，本次输出不会登记")
                if self._report_progress({"phase": "FINALIZING", "percent": 97}, force=True):
                    raise DomainRuleError("JOB_CANCELLED", "后台任务已取消，本次输出不会提交为成功")
            artifacts = [self.jobs.register_artifact(attempt_id, execution.kind, execution.relative_path)]
            for kind, relative_path in execution.additional_artifacts:
                artifacts.append(self.jobs.register_artifact(attempt_id, kind, relative_path))
            if execution.after_artifacts_registered is not None:
                execution.after_artifacts_registered(tuple(artifacts))
            result = self.jobs.complete(attempt_id, token, worker_id, success=True)
            asset_completion = build_asset_image_completion(self.database, self.settings)
            try:
                asset_completion.finalize_job(str(job["id"]), artifacts)
            except DomainRuleError as error:
                asset_completion.record_finalization_failure(str(job["id"]), error)
            keyframe_completion = build_shot_keyframe_completion(self.database, self.settings)
            try:
                keyframe_completion.finalize_job(str(job["id"]), artifacts)
            except DomainRuleError as error:
                keyframe_completion.record_failure(str(job["id"]), error)
            identity_completion = ProductionIdentityGenerationCompletionService(
                self.database, self.settings
            )
            try:
                identity_completion.finalize_job(str(job["id"]), artifacts)
            except DomainRuleError as error:
                identity_completion.record_failure(str(job["id"]), error)
            # Explainer candidate projection.  A miss is the normal answer for every
            # job that is not an explainer candidate, so it returns None quietly.
            explainer_completion = build_explainer_visual_completion_service(self.database, self.settings)
            try:
                explainer_completion.finalize_job(str(job["id"]), artifacts)
            except DomainRuleError as error:
                explainer_completion.record_failure(str(job["id"]), error)
            advance_error: str | None = None
            if execution.report is not None:
                advance_error = advance_automation_run(
                    job,
                    execution.report,
                    execution.produced_bytes,
                    workflow_steps=AutomationWorkflowService(self.database),
                )
            payload: dict[str, Any] = {"job": job, "attempt": attempt, "artifact": artifacts[0], "artifacts": artifacts, "result": result}
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
                error_detail_redacted=_worker_error_detail(error),
                retryable=self._retryable_error(error.code),
                needs_attention=error.code in {"DISK_FULL", "UPSCALE_GPU_OUT_OF_MEMORY"},
            )
            return {"job": job, "attempt": attempt, "result": result, "error": error.code}
        except OSError as error:
            disk_full = error.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}
            code = "DISK_FULL" if disk_full else "MEDIA_WORKER_IO_FAILED"
            detail = "本地输出空间不足，worker 已安全终止且未登记产物" if disk_full else "本地媒体 worker 文件操作失败"
            result = self.jobs.complete(
                attempt_id, token, worker_id, success=False,
                error_code=code, error_detail_redacted=detail,
                needs_attention=disk_full,
            )
            return {"job": job, "attempt": attempt, "result": result, "error": code}
        except MemoryError:
            code = "WORKER_OUT_OF_MEMORY"
            result = self.jobs.complete(
                attempt_id, token, worker_id, success=False,
                error_code=code, error_detail_redacted="本地 worker 内存不足，任务已安全终止",
            )
            return {"job": job, "attempt": attempt, "result": result, "error": code}
        except BaseException as error:
            # A handler that raises outside the declared error taxonomy must still
            # settle its own job.  Letting it escape restarts the surrounding worker
            # session and leaves the attempt dangling until its lease expires — the
            # job then looks "running" while nothing at all is executing it.
            code = type(error).__name__
            get_logger("worker").warning(
                "worker_handler_unexpected_error job_id=%s type=%s detail=%s",
                str(job.get("id")),
                code,
                f"{error}\n{traceback.format_exc()[-3000:]}",
            )
            try:
                result = self.jobs.complete(
                    attempt_id,
                    token,
                    worker_id,
                    success=False,
                    error_code=code,
                    error_detail_redacted=f"处理器异常（{code}）：{str(error)[:200]}",
                )
            except BaseException:
                return {"job": job, "attempt": attempt, "error": code}
            return {"job": job, "attempt": attempt, "result": result, "error": code}
        finally:
            self._active_job_context = None

    def run_media_derivative_once(
        self,
        worker_id: str,
        channels: list[str],
        *,
        worker_session_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Prefer one lightweight creator preview before another long decode."""

        return self.run_once(
            worker_id,
            channels,
            worker_session_id=worker_session_id,
            job_types=["MEDIA_DERIVATIVE"],
        )

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
