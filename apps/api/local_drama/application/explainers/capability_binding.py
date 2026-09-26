"""The explainer production graph's capability gate.

The explainer task skeleton (:data:`~local_drama.application.explainers.production.TASK_SKELETON`)
names its requirements with dotted business keys (``text.generation``,
``audio.tts``, ``video.render`` …).  Those keys are *not* the Model Platform's
canonical capability vocabulary, and nothing bridged the two: the preflight probe
asked the platform to resolve ``text.generation`` and got a failure, which the
production service reported as ``PROBE_FAILED`` for every entry.  The graph could
therefore never become executable on any machine, no matter what was installed.

This module owns that bridge.  Two rules shape it:

* **One canonical name per explainer key.**  Every binding names the canonical
  capability the requirement really is, so the resolution chain, the audit trail
  and the operator-facing "which model provides this" answer are unambiguous.
* **The gate describes what the handler will actually use.**  Text stages run on
  the published offline LLM profile the planner resolves; the speech stages run
  on the configured first-party subprocess runtimes; the deterministic renderer
  needs nothing but the configured FFmpeg pair.  A requirement is reported
  available only when that concrete thing is present — a missing model, runtime
  or tool surfaces as an unavailable entry with the reason, never as a silent
  pass.

Resolution itself is layered, because this build genuinely has two profile
stores: a Model Platform V2 assignment/publication chain and the legacy
``execution_profile_versions`` chain that the drama surfaces still resolve
through.  V2 wins when it can answer (it carries the scope overrides); the
legacy chain is the honest fallback for a capability that only exists there.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

__all__ = [
    "ExplainerCapabilityBinding",
    "EXPLAINER_CAPABILITY_BINDINGS",
    "build_explainer_capability_probe",
    "explainer_capability_report",
]


#: How a requirement is satisfied on this machine.
MODEL_PROFILE = "MODEL_PROFILE"
LOCAL_NARRATION_RUNTIME = "LOCAL_NARRATION_RUNTIME"
LOCAL_ALIGNER_RUNTIME = "LOCAL_ALIGNER_RUNTIME"
LOCAL_FFMPEG = "LOCAL_FFMPEG"


@dataclass(frozen=True, slots=True)
class ExplainerCapabilityBinding:
    """One explainer requirement, its canonical capability and its real source."""

    requirement: str
    canonical: str
    source: str
    note: str = ""


#: The single mapping between the explainer graph and the capability vocabulary.
EXPLAINER_CAPABILITY_BINDINGS: Mapping[str, ExplainerCapabilityBinding] = {
    binding.requirement: binding
    for binding in (
        ExplainerCapabilityBinding(
            "text.generation",
            "LLM_STORY_PARSE",
            MODEL_PROFILE,
            "研究、事实提取、讲稿与分镜都由离线文本规划器撰写，解析的是同一个已发布文本 Profile。",
        ),
        ExplainerCapabilityBinding(
            "audio.tts",
            "TTS",
            LOCAL_NARRATION_RUNTIME,
            "旁白由本机离线 VoxCPM2 子进程运行时合成，音色引用来自栏目版本。",
        ),
        ExplainerCapabilityBinding(
            "audio.forced_alignment",
            "AUDIO_ALIGNMENT",
            LOCAL_ALIGNER_RUNTIME,
            "强制对齐与独立复核由本机离线 Qwen3-ForcedAligner / Qwen3-ASR 运行时完成。",
        ),
        ExplainerCapabilityBinding(
            "image.text_to_image",
            "IMAGE_CONCEPT",
            MODEL_PROFILE,
            "画面节拍的图像候选。",
        ),
        ExplainerCapabilityBinding(
            "image.reference_edit",
            "IMAGE_EDIT",
            MODEL_PROFILE,
            "已有图像的身份/场景参考编辑。",
        ),
        ExplainerCapabilityBinding(
            "video.image_to_video",
            "VIDEO_I2V",
            MODEL_PROFILE,
            "可选的运动镜头；缺失时按已授权回退到静帧动效或信息卡。",
        ),
        ExplainerCapabilityBinding(
            "vision.qa",
            "QC_VISUAL",
            MODEL_PROFILE,
            "视觉质检；缺失时该层报告 LAYER_NOT_RUN，不会伪造通过。",
        ),
        ExplainerCapabilityBinding(
            "video.render",
            "POST_PROCESS",
            LOCAL_FFMPEG,
            "确定性 FFmpeg 渲染与探测；这是本机工具链，不是模型 Profile。",
        ),
    )
}


def _file_present(value: Any) -> bool:
    if value in (None, ""):
        return False
    try:
        return Path(str(value)).is_file() or Path(str(value)).is_dir()
    except OSError:
        return False


def build_capability_assignment_service(database: Any) -> Any:
    """Port-style factory for the model-platform capability resolver.

    Constructing the concrete service inside a resolution helper is reported as new
    cross-service debt, so the construction lives in a ``build_*`` scope.  The
    import stays function-local: this module must stay importable when the model
    platform is not installed.
    """

    from local_drama.model_platform.application.capability_resolution import CapabilityAssignmentService

    return CapabilityAssignmentService(database)


def _v2_resolution(database: Any, binding: ExplainerCapabilityBinding, project_id: str) -> dict[str, Any]:
    """Resolve through the Model Platform V2 assignment chain."""

    from local_drama.model_platform.application.capability_resolution import CapabilityScopeContext

    service = build_capability_assignment_service(database)
    resolution = service.resolve(binding.canonical, CapabilityScopeContext(project_id=project_id))
    if resolution.blocked_reason or not resolution.execution_profile_version_id:
        return {
            "available": False,
            "reason": resolution.blocked_reason or "NO_PUBLISHED_PROFILE_VERSION",
        }
    return {
        "available": True,
        "profile_version_id": str(resolution.execution_profile_version_id),
        "resolution": resolution.resolution_reason,
        "chain": [dict(item) for item in resolution.assignment_chain],
    }


def _legacy_profile_resolution(database: Any, binding: ExplainerCapabilityBinding) -> dict[str, Any]:
    """Resolve through the legacy published ``execution_profile_versions`` chain.

    The drama surfaces still select their runtime through this table, so a
    capability that is published *only* here is genuinely usable on this machine.
    """

    with database.connect() as connection:
        row = connection.execute(
            """SELECT id, version_no FROM execution_profile_versions
            WHERE capability=? AND status='PUBLISHED'
            ORDER BY version_no DESC, updated_at DESC LIMIT 1""",
            (binding.canonical,),
        ).fetchone()
    if row is None:
        return {"available": False, "reason": "NO_PUBLISHED_PROFILE_VERSION"}
    return {
        "available": True,
        "profile_version_id": str(row["id"]),
        "resolution": "LEGACY_PUBLISHED_PROFILE",
    }


def _local_ai_resolution(settings: Any, binding: ExplainerCapabilityBinding) -> dict[str, Any]:
    """Speech runtimes: the narration and alignment subprocess interpreters."""

    if not _file_present(getattr(settings, "local_ai_python", None)):
        return {"available": False, "reason": "LOCAL_AI_PYTHON_NOT_CONFIGURED"}
    if not _file_present(getattr(settings, "local_ai_adapter", None)):
        return {"available": False, "reason": "LOCAL_AI_ADAPTER_NOT_CONFIGURED"}
    model_root = getattr(settings, "local_ai_model_root", None)
    if not _file_present(model_root):
        return {"available": False, "reason": "LOCAL_AI_MODEL_ROOT_NOT_CONFIGURED"}
    required = {
        LOCAL_NARRATION_RUNTIME: ("PyTorch/VoxCPM2", "voxcpm2"),
        LOCAL_ALIGNER_RUNTIME: ("PyTorch/Qwen3-ForcedAligner-0.6B-hf", "qwen3_forced_aligner"),
    }[binding.source]
    relative, label = required
    if not (Path(str(model_root)) / relative).is_dir():
        return {"available": False, "reason": f"MODEL_MISSING:{label}"}
    return {
        "available": True,
        "profile_version_id": None,
        "resolution": "FIRST_PARTY_LOCAL_RUNTIME",
        "runtime": str(settings.local_ai_python),
        "model_root": str(model_root),
    }


def _ffmpeg_resolution(settings: Any) -> dict[str, Any]:
    """The deterministic renderer: a real FFmpeg/FFprobe pair on this machine."""

    ffmpeg_path = getattr(settings, "ffmpeg_path", None)
    ffprobe_path = getattr(settings, "ffprobe_path", None)
    if not _file_present(ffmpeg_path) or not _file_present(ffprobe_path):
        return {
            "available": False,
            "reason": "FFMPEG_TOOLCHAIN_UNAVAILABLE",
            "detail": {"ffmpeg": bool(ffmpeg_path), "ffprobe": bool(ffprobe_path)},
        }
    return {
        "available": True,
        "profile_version_id": None,
        "resolution": "LOCAL_TOOLCHAIN",
        "ffmpeg": str(ffmpeg_path),
    }


def _resolve_one(
    database: Any, settings: Any, binding: ExplainerCapabilityBinding, *, project_id: str
) -> dict[str, Any]:
    if binding.source == MODEL_PROFILE:
        first = _v2_resolution(database, binding, project_id)
        if first.get("available"):
            return {**first, "source": binding.source}
        second = _legacy_profile_resolution(database, binding)
        if second.get("available"):
            return {**second, "source": binding.source, "v2_reason": first.get("reason")}
        return {
            "available": False,
            "reason": first.get("reason") or second.get("reason") or "NO_PUBLISHED_PROFILE_VERSION",
            "source": binding.source,
        }
    if binding.source in {LOCAL_NARRATION_RUNTIME, LOCAL_ALIGNER_RUNTIME}:
        return {**_local_ai_resolution(settings, binding), "source": binding.source}
    return {**_ffmpeg_resolution(settings), "source": binding.source}


def build_explainer_capability_probe(
    database: Any, settings: Any
) -> Callable[..., dict[str, Any]]:
    """The capability probe the explainer preflight and scheduler share.

    It is built once per request/process; the returned callable keeps the
    ``probe(capability, *, project_id=...)`` shape
    :meth:`ExplainerProductionService.capability_snapshot` expects.
    """

    bindings = EXPLAINER_CAPABILITY_BINDINGS

    def probe(capability: str, *, project_id: str) -> dict[str, Any]:
        binding = bindings.get(str(capability))
        if binding is None:
            return {
                "available": False,
                "reason": "UNKNOWN_EXPLAINER_CAPABILITY",
                "canonical_capability": None,
                "execution_class": "LOCAL",
            }
        resolved = _resolve_one(database, settings, binding, project_id=project_id)
        return {
            "available": bool(resolved.get("available")),
            "reason": resolved.get("reason"),
            "canonical_capability": binding.canonical,
            "requirement": binding.requirement,
            "resolution": resolved.get("resolution"),
            "source": resolved.get("source"),
            "profile_version_id": resolved.get("profile_version_id"),
            "execution_class": "LOCAL",
            "note": binding.note,
        }

    return probe


def explainer_capability_report(database: Any, settings: Any, *, project_id: str) -> dict[str, Any]:
    """Human-readable readiness statement for diagnostics surfaces."""

    probe = build_explainer_capability_probe(database, settings)
    return {
        "schema_version": "localdrama.explainer-capability-report.v1",
        "project_id": project_id,
        "capabilities": [probe(key, project_id=project_id) for key in EXPLAINER_CAPABILITY_BINDINGS],
    }
