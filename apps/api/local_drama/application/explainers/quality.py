"""Explainer quality gates: technical full decode, semantic sampling, deep frames,
subtitle layout and deterministic fact checks (《解说工厂完整设计与开发规范》§18).

Three check layers live here and are **never collapsed**:

``TECHNICAL_FULL_COVERAGE``
    ``technical_full_decode_v1`` — every frame of the final film (and every source
    clip, when the caller declares them) must be fully decoded; black / freeze /
    glitch / PTS / frame-count / illegal-size / audio-gap / peak / subtitle-overflow
    detection run over that decoded scope.  Intentional black and still intervals
    are an allowlist so an illustration is never misreported as a frozen video.

``SEMANTIC_SAMPLING``
    ``visual_semantic_v1`` — a local :class:`VisualQcProvider` really reads sampled
    frames.  Sampled frame ids, windows and ratio are recorded.  A provider that is
    not installed yields ``NOT_RUN`` + an ``UNKNOWN`` ``SEMANTIC_QC_UNCHECKED``
    issue; it is **never** a pass, and ``UNKNOWN`` is a preserved result.

``DEEP_PER_FRAME``
    ``visual_depth_v1`` — a requested full-frame VLM pass over one named shot,
    resumable, with every unprocessed frame reported as unchecked.

What this module deliberately does NOT do:

* it never converts a model score, a similarity value or a policy verdict into a
  proof of factual truth; numbers/names/sources are decided by deterministic
  checks first (``run_fact_check``), and a model confidence is only recorded;
* it never reports a sampled pass as full understanding, and never reports decode
  coverage as semantic coverage (three separate coverage numbers stay separate);
* it never writes ``HUMAN_APPROVED`` — the machine path can only write
  ``POLICY_ACCEPTED``, and human decisions are recorded by
  :meth:`ExplainerQualityService.record_human_decision` from a real operator
  action with the current content hash;
* it never lets a new ``POLICY_RULE_VERSION`` rewrite an old report or make an old
  machine decision reusable under the new rule set;
* it never calls a model itself: the visual provider is injected, and a missing or
  failing provider is recorded as ``UNCHECKED``/``STALE``-safe data, not success;
* it never performs the repair: an issue returns to its responsible node with the
  requirement that a new version is produced and the downstream re-checked;
* it never touches the legacy drama render approval path
  (``local_drama.application.episode_render_approval``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, ClassVar, Mapping, Protocol, Sequence

from local_drama.domain.explainers.contracts import (
    ActorType,
    ClaimStatus,
    DecisionKind,
    ExplainerContractError,
    ExplainerErrorCode,
    IssueStatus,
    QcReportStatus,
    QcSubjectKind,
    Severity,
    StatementType,
    content_hash,
    ensure_policy_decision_allowed,
    is_hard_blocker,
    is_sha256,
    is_soft_issue,
    subtitle_reading_rates,
    summarise_coverage,
    utc_now_iso,
)
from local_drama.domain.explainers.policies import (
    POLICY_PROCESSOR_NAME,
    POLICY_RULE_VERSION,
    CoverageFact,
    IssueFact,
    MachineAcceptance,
    Thresholds,
    evaluate_machine_acceptance,
)
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

#: Detector identifiers recorded on every issue row (design §18.1).
TECHNICAL_DETECTOR = "technical_full_decode_v1"
SEMANTIC_DETECTOR = "visual_semantic_v1"
DEPTH_DETECTOR = "visual_depth_v1"
SUBTITLE_DETECTOR = "subtitle_layout_v1"
FACT_DETECTOR = "fact_evidence_v1"

DETECTOR_VERSIONS: dict[str, str] = {
    TECHNICAL_DETECTOR: "1",
    SEMANTIC_DETECTOR: "1",
    DEPTH_DETECTOR: "1",
    SUBTITLE_DETECTOR: "1",
    FACT_DETECTOR: "1",
}

LAYER_TECHNICAL = "TECHNICAL_FULL_COVERAGE"
LAYER_SEMANTIC = "SEMANTIC_SAMPLING"
LAYER_DEPTH = "DEEP_PER_FRAME"
LAYER_SUBTITLE = "SUBTITLE_LAYOUT"
LAYER_FACT = "FACT_EVIDENCE"

#: Sampling density vocabulary.  ``DENSE`` doubles the interior sample count.
DENSITY_MULTIPLIERS: dict[str, int] = {"STANDARD": 1, "DENSE": 2}

#: Frames sampled around a hard cut and around an entity/text change.
CUT_SAMPLE_RADIUS = 2
CHANGE_SAMPLE_RADIUS = 1

#: A visual finding below this confidence stays a low-confidence/UNKNOWN issue.
LOW_CONFIDENCE_THRESHOLD = 0.5

#: Audio gap bands (design §11.4 keeps these advisory, not blocking).
AUDIO_GAP_WARN_MS = 800
AUDIO_GAP_MAJOR_MS = 2000

#: Subtitle reading rate above ``limit * factor`` is reported as a real defect.
SUBTITLE_READING_RATE_GROSS_FACTOR = 1.5

#: Cue overlap shorter than this is treated as boundary tolerance, not an overlap.
SUBTITLE_OVERLAP_TOLERANCE_MS = 40

#: Above this count a coverage payload stores intervals instead of a frame-id list.
MAX_COVERAGE_FRAME_ID_LIST = 2000

#: The trusted-LAN install has no login system; every human decision records it.
LOCAL_OPERATOR_LIMITATION = (
    "受信任局域网安装没有登录系统：该人工决定是本地操作者对机器执行的动作，"
    "不是经过身份认证的自然人签名，也不构成对操作者身份的法律背书；"
    "该限制随决定一起记录，不得静默省略。"
)

#: Severity assigned to a provider finding that is not classified by type.
UNCLASSIFIED_FINDING_SEVERITY = Severity.MAJOR.value


def _detector_version(detector: str) -> str:
    return DETECTOR_VERSIONS.get(detector, "")


def _error_detail_text(error: BaseException, *, limit: int = 300) -> str:
    """A provider error's message plus its structured details, bounded.

    A capability adapter reports the *reason* in ``error.details`` (the exception
    class of the underlying call, the endpoint it could not reach).  Recording only
    the message left an operator with "the multimodal call failed" and no way to tell
    a stopped model server from an unreadable frame.
    """

    message = str(error)
    details = getattr(error, "details", None)
    if isinstance(details, Mapping) and details:
        try:
            rendered = json.dumps({str(key): item for key, item in details.items()}, ensure_ascii=False)
        except (TypeError, ValueError):
            rendered = str(details)
        message = f"{message} {rendered}"
    return message[:limit]


#: Which coverage layer a detector belongs to; used when a caller omits ``layer``.
DETECTOR_LAYERS: dict[str, str] = {
    TECHNICAL_DETECTOR: LAYER_TECHNICAL,
    SEMANTIC_DETECTOR: LAYER_SEMANTIC,
    DEPTH_DETECTOR: LAYER_DEPTH,
    SUBTITLE_DETECTOR: LAYER_SUBTITLE,
    FACT_DETECTOR: LAYER_FACT,
}


class VisualQcProvider(Protocol):
    """Injected local visual understanding capability (design §18.1, §20).

    ``capability()`` must be honest: ``available=False`` means the layer is
    UNCHECKED.  ``check_frames`` receives only frames the caller selected and must
    return one finding per anomaly it actually observed, with ``unknown_reason``
    set when it could not decide.
    """

    def capability(self) -> dict[str, Any]:  # pragma: no cover - protocol
        ...

    def check_frames(
        self, *, frames: Sequence[dict[str, Any]], questions: Sequence[str]
    ) -> list[dict[str, Any]]:  # pragma: no cover - protocol
        ...


# --------------------------------------------------------------------------- #
# interval helpers (deterministic, half-open ``[start, end)`` frames and ms)
# --------------------------------------------------------------------------- #
Interval = tuple[int, int]

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_CJK_NUMBER_RE = re.compile(r"[零一二三四五六七八九十百千万亿两]+")


def _coerce_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ExplainerContractError("SCHEMA_INVALID", f"{field_name} 必须是整数", {field_name: value})
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float) and float(value).is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("+-").isdigit():
        return int(value.strip())
    raise ExplainerContractError("SCHEMA_INVALID", f"{field_name} 必须是整数", {field_name: value})


def _read_interval(entry: Any, *, field_name: str) -> Interval:
    """Read one half-open frame interval from an int, pair, or mapping."""

    if isinstance(entry, Mapping):
        if "start_frame" in entry or "end_frame_exclusive" in entry:
            return (
                _coerce_int(entry.get("start_frame"), field_name=f"{field_name}.start_frame"),
                _coerce_int(entry.get("end_frame_exclusive"), field_name=f"{field_name}.end_frame_exclusive"),
            )
        if "frame" in entry or "frame_id" in entry:
            frame = _coerce_int(entry.get("frame", entry.get("frame_id")), field_name=f"{field_name}.frame")
            return (frame, frame + 1)
        if "start" in entry or "end" in entry:
            return (
                _coerce_int(entry.get("start"), field_name=f"{field_name}.start"),
                _coerce_int(entry.get("end"), field_name=f"{field_name}.end"),
            )
        pair = entry.get("frame_range")
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            return (
                _coerce_int(pair[0], field_name=f"{field_name}.frame_range[0]"),
                _coerce_int(pair[1], field_name=f"{field_name}.frame_range[1]"),
            )
        raise ExplainerContractError(
            "SCHEMA_INVALID", f"{field_name} 的区间缺少帧范围字段", {field_name: dict(entry)}
        )
    if isinstance(entry, (list, tuple)):
        if len(entry) == 2:
            return (
                _coerce_int(entry[0], field_name=f"{field_name}[0]"),
                _coerce_int(entry[1], field_name=f"{field_name}[1]"),
            )
        raise ExplainerContractError(
            "SCHEMA_INVALID", f"{field_name} 的区间必须是 [start, end) 两个元素", {field_name: list(entry)}
        )
    frame = _coerce_int(entry, field_name=field_name)
    return (frame, frame + 1)


def _merge_intervals(intervals: Sequence[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for start, end in sorted((int(item[0]), int(item[1])) for item in intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _normalise_intervals(
    values: Sequence[Any] | None,
    *,
    field_name: str,
    total_frames: int | None = None,
    notes: list[str] | None = None,
) -> list[Interval]:
    out: list[Interval] = []
    for entry in values or ():
        start, end = _read_interval(entry, field_name=field_name)
        if end <= start:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"{field_name} 的帧区间必须满足 end > start",
                {"start": start, "end": end},
            )
        if start < 0:
            raise ExplainerContractError(
                "SCHEMA_INVALID", f"{field_name} 的帧区间不能为负", {"start": start, "end": end}
            )
        if total_frames is not None:
            if start >= total_frames:
                if notes is not None:
                    notes.append(f"INTERVAL_OUTSIDE_FILM_DROPPED:{field_name}")
                continue
            if end > total_frames:
                if notes is not None:
                    notes.append(f"INTERVAL_CLAMPED_TO_FILM:{field_name}")
                end = total_frames
        out.append((start, end))
    return _merge_intervals(out)


def _subtract_intervals(base: Interval, cutters: Sequence[Interval]) -> list[Interval]:
    """Return the parts of ``base`` not covered by ``cutters``."""

    remaining: list[Interval] = [base]
    for cut_start, cut_end in _merge_intervals(cutters):
        next_remaining: list[Interval] = []
        for start, end in remaining:
            if cut_end <= start or cut_start >= end:
                next_remaining.append((start, end))
                continue
            if start < cut_start:
                next_remaining.append((start, cut_start))
            if cut_end < end:
                next_remaining.append((cut_end, end))
        remaining = next_remaining
        if not remaining:
            break
    return remaining


def _interval_frame_count(intervals: Sequence[Interval]) -> int:
    return sum(end - start for start, end in intervals)


def _intervals_overlap(left: Interval, right: Interval) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _frames_to_ms(frames: int, fps: tuple[int, int] | None) -> int | None:
    if not fps or fps[0] <= 0 or fps[1] <= 0:
        return None
    return int(round(frames * fps[1] * 1000 / fps[0]))


def _ms_to_frames(ms: int, fps: tuple[int, int] | None) -> int | None:
    if not fps or fps[0] <= 0 or fps[1] <= 0:
        return None
    return int(ms * fps[0] // (fps[1] * 1000))


def _coerce_confidence(value: Any) -> tuple[float | None, str | None]:
    if value is None:
        return None, None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None, "CONFIDENCE_NOT_NUMERIC"
    if confidence != confidence:  # NaN
        return None, "CONFIDENCE_NOT_NUMERIC"
    if confidence < 0.0 or confidence > 1.0:
        return None, "CONFIDENCE_OUT_OF_RANGE"
    return round(confidence, 6), None


#: Which node owns a repair for a given issue kind (design §18.4, §13.2).
_RESPONSIBLE_STEP_EXACT: dict[str, str] = {
    "SOURCE_EVIDENCE_MISSING": "RESEARCH_ACQUIRE",
    "LICENSE_SCOPE_UNVERIFIED": "EXPLAINER_EXPORT",
    "IDENTITY_WRONG_CHARACTER": "EXPLAINER_STORYBOARD",
    "SUBTITLE_STALE_TEXT": "NARRATION_ALIGN",
    "SUBTITLE_TEXT_MISMATCH": "NARRATION_ALIGN",
    "NARRATION_MISREAD_KEY_TERM": "NARRATION_TTS",
    "NARRATION_MISREAD_NUMBER": "NARRATION_TTS",
    "NARRATION_SEGMENT_MISSING": "NARRATION_TTS",
    "MISSING_NARRATION": "NARRATION_TTS",
    "SEMANTIC_QC_UNCHECKED": "EXPLAINER_VISUAL_QC",
}

_RESPONSIBLE_STEP_PREFIX: tuple[tuple[str, str], ...] = (
    ("FACT_", "NARRATION_WRITE"),
    ("SOURCE_", "RESEARCH_ACQUIRE"),
    ("LICENSE_", "EXPLAINER_EXPORT"),
    ("NARRATION_", "NARRATION_TTS"),
    ("SUBTITLE_", "COMPOSITION_RENDER"),
    ("IDENTITY_", "EXPLAINER_STORYBOARD"),
    ("VISUAL_", "EXPLAINER_VISUAL_QC"),
    ("SEMANTIC_", "EXPLAINER_VISUAL_QC"),
    ("MUSIC_", "COMPOSITION_RENDER"),
    ("STYLE_", "EXPLAINER_VISUAL_QC"),
    ("COMPOSITION_", "COMPOSITION_RENDER"),
    ("PACING_", "COMPOSITION_RENDER"),
    ("FRAMING_", "COMPOSITION_RENDER"),
    ("MEDIA_", "COMPOSITION_RENDER"),
    ("VIDEO_", "COMPOSITION_RENDER"),
    ("AUDIO_", "COMPOSITION_RENDER"),
    ("PTS_", "COMPOSITION_RENDER"),
    ("DECODE_", "COMPOSITION_RENDER"),
    ("ILLEGAL_", "COMPOSITION_RENDER"),
    ("TECHNICAL_", "COMPOSITION_RENDER"),
)


def responsible_step_for(issue_kind: str) -> str:
    """Map an issue kind to the node that must produce the repaired version."""

    if issue_kind in _RESPONSIBLE_STEP_EXACT:
        return _RESPONSIBLE_STEP_EXACT[issue_kind]
    for prefix, step in _RESPONSIBLE_STEP_PREFIX:
        if issue_kind.startswith(prefix):
            return step
    return "COMPOSITION_QC"


#: Suggested repair vocabulary recorded with every issue.
_SUGGESTED_REPAIR: dict[str, str] = {
    "MEDIA_CORRUPT": "重新渲染受影响的帧区间并生成新的 render 版本，不要复用损坏文件。",
    "DECODE_COVERAGE_INCOMPLETE": "补足全片解码后再判定；未解码帧不得视为通过。",
    "TECHNICAL_COVERAGE_INCOMPLETE": "对全部帧完成技术检测后重新检查。",
    "ILLEGAL_DIMENSIONS": "按 edition 的画幅与分辨率重新输出，修正非法规格。",
    "VIDEO_BLACK_FRAME": "确认是否属于有意的黑场；无意的黑场回到合成节点重新生成该区间。",
    "VIDEO_FROZEN": "确认是否为静帧画面；若非有意静帧，回到画面段节点重新生成动态素材。",
    "VIDEO_GLITCH": "回到合成节点重新渲染该区间，排除解码/拼接故障。",
    "PTS_ANOMALY": "修正时间戳与帧计数后重新封装，禁止直接重命名输出。",
    "AUDIO_GAP_DETECTED": "补齐旁白或调整混音，避免长时间静音断档。",
    "AUDIO_PEAK_EXCEEDED": "回到混音节点做限幅/增益修正后重新渲染。",
    "AUDIO_LOUDNESS_OUT_OF_BAND": "按记录的响度阈值重新混音。",
    "SUBTITLE_OUT_OF_SAFE_AREA": "重排字幕版式使其回到安全区，并生成新的字幕 revision。",
    "SUBTITLE_STALE_TEXT": "基于当前冻结的旁白/讲稿重新生成字幕 revision。",
    "SUBTITLE_CUE_OVERLAP": "修正时间轴重叠，禁止两条同轨字幕同时显示。",
    "SUBTITLE_NEGATIVE_DURATION": "修正 cue 的起止时间，禁止零长度或负长度字幕。",
    "SUBTITLE_BEYOND_FILM_END": "把 cue 收回片长范围，或重新对齐旁白时长。",
    "SUBTITLE_BILINGUAL_OVERFLOW": "重新折行/缩短译文，生成新的双语字幕 revision。",
    "SUBTITLE_READING_RATE_EXCEEDED": "缩短文案或延长显示时间，降低阅读速度。",
    "SEMANTIC_QC_UNCHECKED": "安装并 smoke 本地视觉 QC 能力后重新检查；未检查不等于通过。",
    "SOURCE_EVIDENCE_MISSING": "为该断言补充可定位的来源，或删改该断言后重新预检。",
    "FACT_KEY_CONFLICT": "在资料页解决冲突：补充证据、保留有依据的表述或移除该断言。",
    "FACT_KEY_NUMBER_MISMATCH": "核对数字与引用断言，改为与证据一致的数字。",
    "FACT_KEY_NAME_MISMATCH": "核对关键名称与引用断言/别名表，修正不一致的名称。",
}

_DEFAULT_REPAIR = "回到负责节点生成新的版本，并对下游重新检查。"


def _suggested_repair(issue_kind: str) -> str:
    return _SUGGESTED_REPAIR.get(issue_kind, _DEFAULT_REPAIR)


# --------------------------------------------------------------------------- #
# technical layer input
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TechnicalCheckInput:
    """Declared detections of the technical layer.

    Every interval is half-open ``[start_frame, end_frame_exclusive)`` and may be
    given as an ``int`` (one frame), a ``[start, end)`` pair, or a mapping with
    ``start_frame``/``end_frame_exclusive``.  ``extras`` keeps the fields this
    dataclass does not own — notably ``source_clips`` declaring per-source-clip
    decode coverage, and ``fps_num``/``fps_den`` when the caller already knows them.
    """

    total_frames: int
    decoded_frames: int
    decode_errors: tuple[Any, ...] = ()
    black_frames: tuple[Any, ...] = ()
    frozen_intervals: tuple[Any, ...] = ()
    glitch_intervals: tuple[Any, ...] = ()
    pts_anomalies: tuple[Any, ...] = ()
    illegal_dimensions: tuple[Any, ...] = ()
    audio_gaps_ms: tuple[Any, ...] = ()
    peak_dbtp: float | None = None
    loudness_lufs: float | None = None
    subtitle_overflows: tuple[Any, ...] = ()
    declared_allowed_black: tuple[Any, ...] = ()
    declared_still_intervals: tuple[Any, ...] = ()
    extras: Mapping[str, Any] = field(default_factory=dict)

    _FIELDS: ClassVar[tuple[str, ...]] = (
        "total_frames",
        "decoded_frames",
        "decode_errors",
        "black_frames",
        "frozen_intervals",
        "glitch_intervals",
        "pts_anomalies",
        "illegal_dimensions",
        "audio_gaps_ms",
        "peak_dbtp",
        "loudness_lufs",
        "subtitle_overflows",
        "declared_allowed_black",
        "declared_still_intervals",
    )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> TechnicalCheckInput:
        if not isinstance(payload, Mapping):
            raise ExplainerContractError("SCHEMA_INVALID", "technical 必须是对象", {"type": type(payload).__name__})
        for required in ("total_frames", "decoded_frames"):
            if required not in payload:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", f"technical 缺少必需字段：{required}", {"field": required}
                )
        sequences: dict[str, tuple[Any, ...]] = {}
        for name in cls._FIELDS:
            if name in {"total_frames", "decoded_frames", "peak_dbtp", "loudness_lufs"}:
                continue
            raw = payload.get(name)
            if raw is None:
                sequences[name] = ()
                continue
            if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple, set)):
                raise ExplainerContractError(
                    "SCHEMA_INVALID", f"technical.{name} 必须是数组", {"field": name}
                )
            sequences[name] = tuple(raw)
        peak = payload.get("peak_dbtp")
        loudness = payload.get("loudness_lufs")
        extras = {key: value for key, value in payload.items() if key not in cls._FIELDS}
        return cls(
            total_frames=_coerce_int(payload.get("total_frames"), field_name="technical.total_frames"),
            decoded_frames=_coerce_int(payload.get("decoded_frames"), field_name="technical.decoded_frames"),
            decode_errors=sequences["decode_errors"],
            black_frames=sequences["black_frames"],
            frozen_intervals=sequences["frozen_intervals"],
            glitch_intervals=sequences["glitch_intervals"],
            pts_anomalies=sequences["pts_anomalies"],
            illegal_dimensions=sequences["illegal_dimensions"],
            audio_gaps_ms=sequences["audio_gaps_ms"],
            peak_dbtp=None if peak is None else float(peak),
            loudness_lufs=None if loudness is None else float(loudness),
            subtitle_overflows=sequences["subtitle_overflows"],
            declared_allowed_black=sequences["declared_allowed_black"],
            declared_still_intervals=sequences["declared_still_intervals"],
            extras=extras,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_frames": self.total_frames,
            "decoded_frames": self.decoded_frames,
            "decode_errors": list(self.decode_errors),
            "black_frames": list(self.black_frames),
            "frozen_intervals": list(self.frozen_intervals),
            "glitch_intervals": list(self.glitch_intervals),
            "pts_anomalies": list(self.pts_anomalies),
            "illegal_dimensions": list(self.illegal_dimensions),
            "audio_gaps_ms": list(self.audio_gaps_ms),
            "peak_dbtp": self.peak_dbtp,
            "loudness_lufs": self.loudness_lufs,
            "subtitle_overflows": list(self.subtitle_overflows),
            "declared_allowed_black": list(self.declared_allowed_black),
            "declared_still_intervals": list(self.declared_still_intervals),
            "extras": dict(self.extras),
        }


# --------------------------------------------------------------------------- #
# service
# --------------------------------------------------------------------------- #
class ExplainerQualityService:
    """Quality gates for one explainer video, persisted through the repository."""

    def __init__(
        self,
        repo: ExplainerRepository,
        *,
        visual_provider: VisualQcProvider | None = None,
    ) -> None:
        self.repo = repo
        self.visual_provider = visual_provider

    # ------------------------------------------------------------------ scope
    def _require_scope(
        self, *, project_id: str, video_id: str, edition_id: str | None = None
    ) -> dict[str, Any]:
        return ExplainerQualityService._require_scope_for_repo(
            self.repo, project_id=project_id, video_id=video_id, edition_id=edition_id
        )

    @staticmethod
    def _require_scope_for_repo(
        repo: ExplainerRepository, *, project_id: str, video_id: str, edition_id: str | None = None
    ) -> dict[str, Any]:
        repo.require_explainer_project(project_id)
        video = repo.require_video_for_project(project_id)
        if str(video["id"]) != str(video_id):
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "视频与项目不匹配",
                {"project_id": project_id, "video_id": video_id, "video_project_id": video["project_id"]},
            )
        scope: dict[str, Any] = dict(video)
        if edition_id:
            edition = repo.find("explainer_editions", edition_id)
            if edition is None:
                raise ExplainerContractError(
                    ExplainerErrorCode.NOT_FOUND.value, "edition 不存在", {"edition_id": edition_id}
                )
            if str(edition["video_id"]) != str(video_id):
                raise ExplainerContractError(
                    "INVALID_REQUEST",
                    "edition 不属于该视频",
                    {"edition_id": edition_id, "edition_video_id": edition["video_id"]},
                )
            scope["edition"] = edition
        return scope

    def _render_fps(self, *, subject_kind: str, subject_revision_id: str) -> tuple[int, int] | None:
        render = None
        if subject_kind == QcSubjectKind.COMPOSITION_RENDER.value:
            render = self.repo.find("composition_renders", subject_revision_id)
        if render is None:
            return None
        composition = self.repo.find("composition_revisions", str(render.get("composition_revision_id") or ""))
        if composition is None:
            return None
        num = int(composition.get("fps_num") or 0)
        den = int(composition.get("fps_den") or 0)
        if num <= 0 or den <= 0:
            return None
        return (num, den)

    def _require_hash(self, value: str | None, *, field_name: str) -> str:
        return ExplainerQualityService._require_hash_for_repo(value, field_name=field_name)

    @staticmethod
    def _require_hash_for_repo(value: str | None, *, field_name: str) -> str:
        if not is_sha256(value or ""):
            raise ExplainerContractError(
                "INVALID_REQUEST",
                f"{field_name} 必须是对象内容哈希（sha256）；决定与判定必须绑定确切对象",
                {field_name: value},
            )
        return str(value)

    # ------------------------------------------------------------------ provider
    def _capability(self, provider: VisualQcProvider | None) -> dict[str, Any]:
        if provider is None:
            return {
                "available": False,
                "provider_id": None,
                "model_revision": None,
                "reads_images": False,
                "reads_video": False,
                "reason": "VISUAL_QC_PROVIDER_NOT_CONFIGURED",
            }
        try:
            raw = provider.capability()
        except Exception as exc:  # noqa: BLE001 - an exploding provider is UNCHECKED
            return {
                "available": False,
                "provider_id": getattr(provider, "provider_id", None),
                "model_revision": None,
                "reads_images": False,
                "reads_video": False,
                "reason": f"VISUAL_QC_PROVIDER_CAPABILITY_ERROR:{type(exc).__name__}",
            }
        if not isinstance(raw, Mapping):
            return {
                "available": False,
                "provider_id": None,
                "model_revision": None,
                "reads_images": False,
                "reads_video": False,
                "reason": "VISUAL_QC_PROVIDER_CAPABILITY_INVALID",
            }
        available = bool(raw.get("available"))
        reads_images = bool(raw.get("reads_images"))
        if available and not reads_images:
            return {
                "available": False,
                "provider_id": raw.get("provider_id"),
                "model_revision": raw.get("model_revision"),
                "reads_images": False,
                "reads_video": bool(raw.get("reads_video")),
                "reason": "VISUAL_QC_PROVIDER_DOES_NOT_READ_IMAGES",
            }
        return {
            "available": available,
            "provider_id": raw.get("provider_id"),
            "model_revision": raw.get("model_revision"),
            "reads_images": reads_images,
            "reads_video": bool(raw.get("reads_video")),
            "reason": raw.get("reason"),
        }

    # ------------------------------------------------------------------ report / issue writes
    def _create_report(
        self,
        *,
        project_id: str,
        video_id: str,
        edition_id: str | None,
        subject_kind: str,
        subject_revision_id: str,
        subject_hash: str,
        status: str,
        coverage: Mapping[str, Any],
        unverified_checks: Sequence[str],
        detectors: Sequence[Mapping[str, Any]],
        summary: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._require_hash(subject_hash, field_name="subject_hash")
        deduped = list(dict.fromkeys(str(item) for item in unverified_checks))
        return self.repo.insert(
            "explainer_qc_reports",
            {
                "project_id": project_id,
                "video_id": video_id,
                "edition_id": edition_id,
                "subject_kind": str(subject_kind),
                "subject_revision_id": str(subject_revision_id),
                "subject_hash": str(subject_hash),
                "policy_version": POLICY_RULE_VERSION,
                "status": str(status),
                "coverage_json": dict(coverage),
                "unverified_checks_json": deduped,
                "detectors_json": [dict(item) for item in detectors],
                "summary_json": dict(summary),
            },
        )

    def _insert_issue(
        self,
        *,
        report: Mapping[str, Any],
        issue_kind: str,
        severity: str,
        detector: str,
        layer: str | None = None,
        observed: Any = "",
        expected: Any = "",
        confidence: Any = None,
        unknown_reason: str | None = None,
        start_frame: int | None = None,
        end_frame_exclusive: int | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        locale: str | None = None,
        beat_id: str | None = None,
        narration_segment_id: str | None = None,
        subtitle_cue_id: str | None = None,
        evidence: Mapping[str, Any] | None = None,
        evidence_media_version_id: str | None = None,
        evidence_text_span: str | None = None,
        suggested_repair: str | None = None,
        scope: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if severity not in {item.value for item in Severity}:
            raise ExplainerContractError("SCHEMA_INVALID", f"未知严重级别：{severity}", {"severity": severity})
        if start_frame is not None and start_frame < 0:
            raise ExplainerContractError("SCHEMA_INVALID", "issue 起始帧不能为负", {"start_frame": start_frame})
        if (
            start_frame is not None
            and end_frame_exclusive is not None
            and end_frame_exclusive < start_frame
        ):
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "issue 帧区间必须满足 end >= start",
                {"start_frame": start_frame, "end_frame_exclusive": end_frame_exclusive},
            )
        resolved_confidence, confidence_note = _coerce_confidence(confidence)
        reasons = [item for item in (unknown_reason, confidence_note) if item]
        resolved_layer = layer or DETECTOR_LAYERS.get(detector, "UNSPECIFIED")
        soft = is_soft_issue(str(issue_kind))
        scope_payload = {
            "layer": resolved_layer,
            "subject_kind": report.get("subject_kind"),
            "subject_revision_id": report.get("subject_revision_id"),
            "edition_id": report.get("edition_id"),
            "recheck_required": True,
            "repair_creates_new_version": True,
            "sampling_is_not_full_understanding": resolved_layer in {LAYER_SEMANTIC, LAYER_DEPTH},
            "soft_hint": soft,
            "never_triggers_automatic_redraw": soft,
            "hard_blocker_kind": is_hard_blocker(str(issue_kind)),
        }
        if start_frame is not None and end_frame_exclusive is not None:
            scope_payload["frame_range"] = [int(start_frame), int(end_frame_exclusive)]
        scope_payload.update(dict(scope or {}))
        return self.repo.insert(
            "explainer_qc_issues",
            {
                "report_id": report["id"],
                "video_id": report["video_id"],
                "edition_id": report.get("edition_id"),
                "issue_kind": str(issue_kind),
                "severity": str(severity),
                "detector": str(detector),
                "detector_version": _detector_version(detector),
                "scope_json": scope_payload,
                "locale": locale,
                "start_frame": start_frame,
                "end_frame_exclusive": end_frame_exclusive,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "beat_id": beat_id,
                "narration_segment_id": narration_segment_id,
                "subtitle_cue_id": subtitle_cue_id,
                "observed": _stringify(observed),
                "expected": _stringify(expected),
                "confidence": resolved_confidence,
                "unknown_reason": " / ".join(reasons) if reasons else None,
                "evidence_json": dict(evidence or {}),
                "evidence_media_version_id": evidence_media_version_id,
                "evidence_text_span": evidence_text_span,
                "suggested_repair": suggested_repair or _suggested_repair(str(issue_kind)),
                "responsible_step_code": responsible_step_for(str(issue_kind)),
                "status": IssueStatus.OPEN.value,
            },
        )

    def _link_report(
        self,
        *,
        project_id: str,
        video_id: str,
        edition_id: str | None,
        subject_kind: str,
        subject_revision_id: str,
        subject_hash: str,
        report_id: str,
    ) -> dict[str, Any] | None:
        """Record the QC report as a dependent of the exact revision it judged."""

        try:
            return self.repo.add_dependency(
                video_id=video_id,
                project_id=project_id,
                edition_id=edition_id,
                upstream_kind=str(subject_kind),
                upstream_id=str(subject_revision_id),
                upstream_hash=str(subject_hash),
                downstream_kind="QC_REPORT",
                downstream_id=str(report_id),
            )
        except ExplainerContractError:
            # A dependency edge is bookkeeping; never lose the finished report over it.
            return None

    # ------------------------------------------------------------------ technical layer
    def _composition_items_for_subject(
        self, *, subject_kind: str, subject_revision_id: str
    ) -> list[dict[str, Any]]:
        if subject_kind != QcSubjectKind.COMPOSITION_RENDER.value:
            return []
        render = self.repo.find("composition_renders", subject_revision_id)
        if render is None:
            return []
        version_id = str(render.get("composition_revision_id") or "")
        if not version_id:
            return []
        return self.repo.composition_items(version_id)

    def _derived_still_intervals(self, items: Sequence[Mapping[str, Any]]) -> list[Interval]:
        """Stills/infographics are legitimately static: never a frozen-video defect."""

        intervals: list[Interval] = []
        for item in items:
            if str(item.get("item_kind") or "") not in {"IMAGE_CLIP", "INFOGRAPHIC"}:
                continue
            start = item.get("start_frame")
            end = item.get("end_frame_exclusive")
            if start is None or end is None:
                continue
            intervals.append((int(start), int(end)))
        return _merge_intervals(intervals)

    def _source_clip_coverage(
        self,
        *,
        project_id: str,
        technical: TechnicalCheckInput,
        items: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        declared = technical.extras.get("source_clips")
        if declared is not None:
            if isinstance(declared, (str, bytes)) or not isinstance(declared, (list, tuple)):
                raise ExplainerContractError("SCHEMA_INVALID", "source_clips 必须是数组", {})
            clips: list[dict[str, Any]] = []
            complete = True
            for entry in declared:
                if not isinstance(entry, Mapping):
                    raise ExplainerContractError("SCHEMA_INVALID", "source_clips 的每一项必须是对象", {})
                media_version_id = str(entry.get("media_version_id") or "")
                if media_version_id:
                    record = self.repo.require_same_project_media(
                        project_id=project_id, media_version_id=media_version_id
                    )
                else:
                    record = {}
                total = _coerce_int(entry.get("total_frames", 0), field_name="source_clips.total_frames")
                decoded = _coerce_int(entry.get("decoded_frames", 0), field_name="source_clips.decoded_frames")
                errors = list(entry.get("decode_errors") or [])
                ok = total > 0 and decoded >= total and not errors and not record.get("integrity_status") == "CORRUPT"
                complete = complete and ok
                clips.append(
                    {
                        "media_version_id": media_version_id or None,
                        "rel_path": entry.get("rel_path") or record.get("rel_path"),
                        "total_frames": total,
                        "decoded_frames": decoded,
                        "decode_error_count": len(errors),
                        "integrity_status": record.get("integrity_status"),
                        "decode_complete": ok,
                    }
                )
            return {
                "declared": True,
                "clips": clips,
                "complete": complete,
                "reason": None if complete else "SOURCE_CLIP_DECODE_INCOMPLETE",
            }
        video_items = [
            item
            for item in items
            if str(item.get("item_kind") or "") in {"VIDEO_CLIP", "MOTION_CLIP"}
            or str(item.get("media_kind") or "").upper() == "VIDEO"
        ]
        if not items:
            return {
                "declared": False,
                "clips": [],
                "complete": None,
                "reason": "NO_COMPOSITION_ITEMS_FOUND",
            }
        if not video_items:
            return {
                "declared": False,
                "clips": [],
                "complete": True,
                "reason": "NO_VIDEO_SOURCE_CLIPS",
            }
        clips = []
        complete = True
        for media_version_id in dict.fromkeys(
            str(item.get("media_version_id")) for item in video_items if item.get("media_version_id")
        ):
            record = self.repo.require_same_project_media(project_id=project_id, media_version_id=media_version_id)
            integrity = str(record.get("integrity_status") or "UNKNOWN")
            ok = integrity == "VERIFIED"
            complete = complete and ok
            clips.append(
                {
                    "media_version_id": media_version_id,
                    "integrity_status": integrity,
                    "sha256": record.get("sha256"),
                    "decode_complete": ok,
                    "evidence": "MEDIA_VERSION_INTEGRITY_VERIFIED" if ok else "MEDIA_VERSION_INTEGRITY_NOT_VERIFIED",
                }
            )
        return {
            "declared": False,
            "clips": clips,
            "complete": complete,
            "reason": None if complete else "SOURCE_CLIP_DECODE_NOT_VERIFIED",
        }

    def run_technical_check(
        self,
        *,
        project_id: str,
        video_id: str,
        edition_id: str | None,
        subject_kind: str,
        subject_revision_id: str,
        subject_hash: str,
        technical: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Layer (a): full-decode technical coverage over the whole film.

        The report is ``PASS`` only when the film is fully decoded *and* the
        technical scope covers every frame *and* no blocker was found.  Intentional
        black/still intervals suppress the corresponding false positive and are kept
        as allowlisted evidence instead.
        """

        scope = self._require_scope(project_id=project_id, video_id=video_id, edition_id=edition_id)
        data = TechnicalCheckInput.from_mapping(technical)
        notes: list[str] = []
        total = data.total_frames
        if total < 0:
            raise ExplainerContractError("SCHEMA_INVALID", "总帧数不能为负", {"total_frames": total})
        if data.decoded_frames < 0 or data.decoded_frames > total:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "已解码帧数必须在 0–总帧数之间",
                {"decoded_frames": data.decoded_frames, "total_frames": total},
            )
        fps = self._render_fps(subject_kind=subject_kind, subject_revision_id=subject_revision_id)
        if fps is None and scope.get("edition"):
            edition = scope["edition"]
            num = int(edition.get("fps_num") or 0)
            den = int(edition.get("fps_den") or 0)
            fps = (num, den) if num > 0 and den > 0 else None
        if fps is None:
            notes.append("FPS_UNKNOWN_MS_ONLY_FRAME_TIME_RANGE")

        decode_errors = _normalise_intervals(
            data.decode_errors, field_name="decode_errors", total_frames=total, notes=notes
        )
        black = _normalise_intervals(
            data.black_frames, field_name="black_frames", total_frames=total, notes=notes
        )
        frozen = _normalise_intervals(
            data.frozen_intervals, field_name="frozen_intervals", total_frames=total, notes=notes
        )
        glitches = _normalise_intervals(
            data.glitch_intervals, field_name="glitch_intervals", total_frames=total, notes=notes
        )
        pts = _normalise_intervals(
            data.pts_anomalies, field_name="pts_anomalies", total_frames=total, notes=notes
        )
        overflows = _normalise_intervals(
            data.subtitle_overflows, field_name="subtitle_overflows", total_frames=total, notes=notes
        )
        allowed_black = _normalise_intervals(
            data.declared_allowed_black,
            field_name="declared_allowed_black",
            total_frames=total,
            notes=notes,
        )
        items = self._composition_items_for_subject(
            subject_kind=subject_kind, subject_revision_id=subject_revision_id
        )
        stills = _merge_intervals(
            _normalise_intervals(
                data.declared_still_intervals,
                field_name="declared_still_intervals",
                total_frames=total,
                notes=notes,
            )
            + self._derived_still_intervals(items)
        )
        source_clip_coverage = self._source_clip_coverage(
            project_id=project_id, technical=data, items=items
        )

        report = self._create_report(
            project_id=project_id,
            video_id=video_id,
            edition_id=edition_id,
            subject_kind=subject_kind,
            subject_revision_id=subject_revision_id,
            subject_hash=subject_hash,
            status=QcReportStatus.RUNNING.value,
            coverage={},
            unverified_checks=(),
            detectors=(
                {
                    "detector": TECHNICAL_DETECTOR,
                    "detector_version": _detector_version(TECHNICAL_DETECTOR),
                    "layer": LAYER_TECHNICAL,
                    "scope": "FULL_FILM_ALL_FRAMES",
                    "fps": {"num": fps[0], "den": fps[1]} if fps else None,
                },
            ),
            summary={"created_for": "run_technical_check"},
        )

        issues: list[dict[str, Any]] = []
        allowlisted_evidence: list[dict[str, Any]] = []

        def add(**kwargs: Any) -> None:
            issues.append(self._insert_issue(report=report, detector=TECHNICAL_DETECTOR, **kwargs))

        # ------ decode coverage / corruption
        if total == 0:
            add(
                issue_kind="MEDIA_CORRUPT",
                severity=Severity.BLOCKER.value,
                layer=LAYER_TECHNICAL,
                observed="0 帧",
                expected="> 0 帧",
                unknown_reason="NO_FRAMES_DECLARED",
                evidence={"total_frames": 0},
            )
        for start, end in decode_errors:
            add(
                issue_kind="MEDIA_CORRUPT",
                severity=Severity.BLOCKER.value,
                layer=LAYER_TECHNICAL,
                start_frame=start,
                end_frame_exclusive=end,
                start_ms=_frames_to_ms(start, fps),
                end_ms=_frames_to_ms(end, fps),
                observed=f"{end - start} 帧解码失败",
                expected="全片每一帧均可解码",
                evidence={"frame_range": [start, end], "decode_error_frames": end - start},
            )
        if data.decoded_frames < total:
            add(
                issue_kind="DECODE_COVERAGE_INCOMPLETE",
                severity=Severity.BLOCKER.value,
                layer=LAYER_TECHNICAL,
                observed=f"已解码 {data.decoded_frames}/{total} 帧",
                expected="全片完全解码",
                evidence={"decoded_frames": data.decoded_frames, "total_frames": total},
            )
        # ------ black frames vs intentional black allowlist
        for interval in black:
            uncovered = _subtract_intervals(interval, allowed_black)
            covered_count = _interval_frame_count(_subtract_intervals(interval, uncovered))
            if covered_count:
                allowlisted_evidence.append(
                    {
                        "allowlist": "DECLARED_ALLOWED_BLACK",
                        "frame_range": [interval[0], interval[1]],
                        "suppressed_issue_kind": "VIDEO_BLACK_FRAME",
                        "suppressed_frames": covered_count,
                    }
                )
            for start, end in uncovered:
                add(
                    issue_kind="VIDEO_BLACK_FRAME",
                    severity=Severity.MAJOR.value,
                    layer=LAYER_TECHNICAL,
                    start_frame=start,
                    end_frame_exclusive=end,
                    start_ms=_frames_to_ms(start, fps),
                    end_ms=_frames_to_ms(end, fps),
                    observed=f"{end - start} 帧全黑",
                    expected="非有意黑场不得连续全黑",
                    evidence={"frame_range": [start, end], "declared_allowed_black": allowlisted_evidence},
                )
        # ------ frozen frames vs still allowlist (illustrations are not defects)
        for interval in frozen:
            uncovered = _subtract_intervals(interval, stills)
            covered_count = _interval_frame_count(_subtract_intervals(interval, uncovered))
            if covered_count:
                allowlisted_evidence.append(
                    {
                        "allowlist": "DECLARED_OR_COMPOSED_STILL",
                        "frame_range": [interval[0], interval[1]],
                        "suppressed_issue_kind": "VIDEO_FROZEN",
                        "suppressed_frames": covered_count,
                        "still_intervals": [list(item) for item in stills],
                    }
                )
            for start, end in uncovered:
                add(
                    issue_kind="VIDEO_FROZEN",
                    severity=Severity.MAJOR.value,
                    layer=LAYER_TECHNICAL,
                    start_frame=start,
                    end_frame_exclusive=end,
                    start_ms=_frames_to_ms(start, fps),
                    end_ms=_frames_to_ms(end, fps),
                    observed=f"{end - start} 帧画面静止",
                    expected="非静帧素材的镜头必须有画面变化",
                    evidence={"frame_range": [start, end], "still_allowlist": [list(item) for item in stills]},
                )
        # ------ glitch / pts / illegal dimensions
        for start, end in glitches:
            add(
                issue_kind="VIDEO_GLITCH",
                severity=Severity.MAJOR.value,
                layer=LAYER_TECHNICAL,
                start_frame=start,
                end_frame_exclusive=end,
                start_ms=_frames_to_ms(start, fps),
                end_ms=_frames_to_ms(end, fps),
                observed=f"{end - start} 帧花屏/拼接故障",
                expected="画面连续无故障帧",
                evidence={"frame_range": [start, end]},
            )
        for start, end in pts:
            add(
                issue_kind="PTS_ANOMALY",
                severity=Severity.MAJOR.value,
                layer=LAYER_TECHNICAL,
                start_frame=start,
                end_frame_exclusive=end,
                start_ms=_frames_to_ms(start, fps),
                end_ms=_frames_to_ms(end, fps),
                observed=f"时间戳异常 {start}–{end}",
                expected="时间戳单调且与帧数一致",
                evidence={"frame_range": [start, end], "fps": {"num": fps[0], "den": fps[1]} if fps else None},
            )
        for entry in data.illegal_dimensions:
            entry_map = dict(entry) if isinstance(entry, Mapping) else {}
            has_range = isinstance(entry, (list, tuple)) or any(
                key in entry_map
                for key in (
                    "start_frame",
                    "end_frame_exclusive",
                    "start",
                    "end",
                    "frame",
                    "frame_id",
                    "frame_range",
                )
            )
            if has_range:
                range_start, range_end = _read_interval(entry, field_name="illegal_dimensions")
                if range_end <= range_start:
                    range_end = range_start + 1
                if total and range_start >= total:
                    notes.append("INTERVAL_OUTSIDE_FILM_DROPPED:illegal_dimensions")
                    continue
                if total and range_end > total:
                    range_end = total
                    notes.append("INTERVAL_CLAMPED_TO_FILM:illegal_dimensions")
            else:
                range_start, range_end = None, None
                notes.append("ILLEGAL_DIMENSIONS_WITHOUT_FRAME_RANGE")
            width = entry_map.get("width")
            height = entry_map.get("height")
            add(
                issue_kind="ILLEGAL_DIMENSIONS",
                severity=Severity.BLOCKER.value,
                layer=LAYER_TECHNICAL,
                start_frame=range_start,
                end_frame_exclusive=range_end,
                start_ms=_frames_to_ms(range_start, fps) if range_start is not None else None,
                end_ms=_frames_to_ms(range_end, fps) if range_end is not None else None,
                observed=f"分辨率 {width}x{height}",
                expected="与 edition 画幅/分辨率一致",
                evidence={"declared": entry_map or [range_start, range_end], "fps": {"num": fps[0], "den": fps[1]} if fps else None},
            )
        # ------ audio
        gaps = _normalise_audio_gaps(data.audio_gaps_ms)
        long_gaps = [gap for gap in gaps if gap["duration_ms"] >= AUDIO_GAP_WARN_MS]
        if long_gaps:
            worst = max(long_gaps, key=lambda item: item["duration_ms"])
            severity = (
                Severity.MAJOR.value
                if worst["duration_ms"] >= AUDIO_GAP_MAJOR_MS
                else Severity.MINOR.value
            )
            add(
                issue_kind="AUDIO_GAP_DETECTED",
                severity=severity,
                layer=LAYER_TECHNICAL,
                start_ms=worst["start_ms"],
                end_ms=worst["end_ms"],
                start_frame=_ms_to_frames(worst["start_ms"], fps),
                end_frame_exclusive=_ms_to_frames(worst["end_ms"], fps),
                observed=f"最长静音断档 {worst['duration_ms']}ms，共 {len(long_gaps)} 处",
                expected=f"单处断档 < {AUDIO_GAP_WARN_MS}ms",
                evidence={"audio_gaps": long_gaps, "thresholds_ms": [AUDIO_GAP_WARN_MS, AUDIO_GAP_MAJOR_MS]},
            )
        thresholds = Thresholds()
        if data.peak_dbtp is None:
            notes.append("AUDIO_TRUE_PEAK_NOT_MEASURED")
        elif data.peak_dbtp > thresholds.true_peak_dbtp:
            add(
                issue_kind="AUDIO_PEAK_EXCEEDED",
                severity=Severity.MAJOR.value,
                layer=LAYER_TECHNICAL,
                observed=f"{data.peak_dbtp} dBTP",
                expected=f"<= {thresholds.true_peak_dbtp} dBTP",
                evidence={"peak_dbtp": data.peak_dbtp, "limit_dbtp": thresholds.true_peak_dbtp},
            )
        if data.loudness_lufs is None:
            notes.append("AUDIO_LOUDNESS_NOT_MEASURED")
        else:
            low = thresholds.loudness_lufs - thresholds.loudness_tolerance_lu
            high = thresholds.loudness_lufs + thresholds.loudness_tolerance_lu
            if not low <= data.loudness_lufs <= high:
                add(
                    issue_kind="AUDIO_LOUDNESS_OUT_OF_BAND",
                    severity=Severity.MINOR.value,
                    layer=LAYER_TECHNICAL,
                    observed=f"{data.loudness_lufs} LUFS",
                    expected=f"{low}–{high} LUFS",
                    evidence={
                        "loudness_lufs": data.loudness_lufs,
                        "band_lufs": [low, high],
                        "source": "PRODUCT_DEFAULT_NOT_PLATFORM_STANDARD",
                    },
                )
        # ------ subtitle pixel overflow (hard blocker kind from design §18.2)
        for start, end in overflows:
            add(
                issue_kind="SUBTITLE_OUT_OF_SAFE_AREA",
                severity=Severity.BLOCKER.value,
                layer=LAYER_TECHNICAL,
                start_frame=start,
                end_frame_exclusive=end,
                start_ms=_frames_to_ms(start, fps),
                end_ms=_frames_to_ms(end, fps),
                observed="字幕像素溢出安全区",
                expected="字幕完全位于安全区内",
                evidence={"frame_range": [start, end], "detector": "PIXEL_SAFE_AREA"},
            )
        # ------ source clip coverage
        if source_clip_coverage["complete"] is False:
            add(
                issue_kind="SOURCE_CLIP_DECODE_INCOMPLETE",
                severity=Severity.BLOCKER.value,
                layer=LAYER_TECHNICAL,
                observed=source_clip_coverage["reason"] or "源素材未完成解码",
                expected="每个源片段都完成逐帧解码",
                evidence={"source_clip_coverage": source_clip_coverage},
            )

        decoded = data.decoded_frames
        coverage = summarise_coverage(
            total_frames=total,
            decoded_frames=decoded,
            technical_checked_frames=decoded,
            semantic_checked_frames=0,
            human_reviewed_intervals=(),
            sampled_frame_ids=(),
        )
        coverage.update(
            {
                "technical_detector": TECHNICAL_DETECTOR,
                "technical_scope": "FULL_FILM_ALL_FRAMES",
                "semantic_detector_available": False,
                "semantic_layer_checked": False,
                "visual_provider_available": bool(self._capability(self.visual_provider)["available"]),
                "decode_complete": total > 0 and decoded >= total,
                "technical_complete": total > 0 and decoded >= total,
                "source_clip_coverage": source_clip_coverage,
                "allowlisted_intervals": allowlisted_evidence,
                "decode_coverage_is_not_semantic_coverage": True,
            }
        )
        unverified = list(notes)
        unverified.append("HUMAN_REVIEW_NOT_PERFORMED_BY_TECHNICAL_LAYER")
        unverified.append("TECHNICAL_PASS_DOES_NOT_IMPLY_SEMANTIC_OR_FACTUAL_VERIFICATION")
        if source_clip_coverage["complete"] is None:
            unverified.append("SOURCE_CLIP_COVERAGE_NOT_DECLARED")
        if fps is None:
            unverified.append("FPS_UNAVAILABLE_FRAME_RANGES_ONLY")

        blocker_count = sum(1 for issue in issues if issue["severity"] == Severity.BLOCKER.value)
        if blocker_count:
            status = QcReportStatus.BLOCKED.value
        elif not issues and total > 0 and decoded >= total:
            status = QcReportStatus.PASS.value
        elif total == 0:
            status = QcReportStatus.BLOCKED.value
        else:
            status = QcReportStatus.PASS_WITH_ISSUES.value

        summary = {
            "layer": LAYER_TECHNICAL,
            "status_basis": (
                "PASS 仅在整片完全解码、技术检测覆盖全部帧且无硬阻塞时成立；"
                "通过只覆盖已测量范围，不代表语义或事实层面的理解。"
            ),
            "detections": {
                "decode_error_intervals": [list(item) for item in decode_errors],
                "black_frame_intervals": [list(item) for item in black],
                "frozen_intervals": [list(item) for item in frozen],
                "glitch_intervals": [list(item) for item in glitches],
                "pts_anomalies": [list(item) for item in pts],
                "illegal_dimensions": len(data.illegal_dimensions),
                "audio_gaps_ms": gaps,
                "peak_dbtp": data.peak_dbtp,
                "loudness_lufs": data.loudness_lufs,
                "subtitle_overflow_intervals": [list(item) for item in overflows],
            },
            "allowlisted_intervals": allowlisted_evidence,
            "issue_count": len(issues),
            "blocker_count": blocker_count,
            "ignored_input_keys": sorted(key for key in data.extras if key != "source_clips"),
            "technical_checked_frames": decoded,
            "total_frames": total,
            "repair_returns_to_responsible_node": True,
            "downstream_recheck_required": True,
        }
        report = self.repo.update(
            "explainer_qc_reports",
            report["id"],
            {
                "status": status,
                "coverage_json": coverage,
                "unverified_checks_json": list(dict.fromkeys(unverified)),
                "summary_json": summary,
            },
        )
        dependency = self._link_report(
            project_id=project_id,
            video_id=video_id,
            edition_id=edition_id,
            subject_kind=subject_kind,
            subject_revision_id=subject_revision_id,
            subject_hash=subject_hash,
            report_id=report["id"],
        )
        return {
            "report": report,
            "issues": issues,
            "status": status,
            "coverage": coverage,
            "unverified_checks": report["unverified_checks_json"],
            "detectors": report["detectors_json"],
            "summary": summary,
            "allowlisted_intervals": allowlisted_evidence,
            "source_clip_coverage": source_clip_coverage,
            "dependency": dependency,
        }

    # ------------------------------------------------------------------ semantic layer
    def plan_semantic_sampling(
        self,
        *,
        video_id: str,
        edition_id: str,
        beat_frame_ranges: Mapping[str, Sequence[int]],
        transition_frames: Sequence[int] = (),
        entity_change_frames: Sequence[int] = (),
        text_change_frames: Sequence[int] = (),
        density: str = "STANDARD",
    ) -> dict[str, Any]:
        """Deterministic sampling plan; never a claim of full understanding."""

        density_key = str(density or "").upper()
        if density_key not in DENSITY_MULTIPLIERS:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "density 只能是 STANDARD 或 DENSE",
                {"density": density},
            )
        edition = self.repo.find("explainer_editions", edition_id)
        if edition is None:
            raise ExplainerContractError("NOT_FOUND", "edition 不存在", {"edition_id": edition_id})
        if str(edition["video_id"]) != str(video_id):
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "edition 不属于该视频",
                {"edition_id": edition_id, "video_id": video_id},
            )
        if not isinstance(beat_frame_ranges, Mapping) or not beat_frame_ranges:
            raise ExplainerContractError("INVALID_REQUEST", "beat_frame_ranges 不能为空", {})

        ranges: list[tuple[str, Interval]] = []
        for beat_id, raw in beat_frame_ranges.items():
            if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)) or len(raw) != 2:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "每个 beat 的帧区间必须是 [start, end)",
                    {"beat_id": beat_id, "value": raw},
                )
            start = _coerce_int(raw[0], field_name=f"beat_frame_ranges[{beat_id}][0]")
            end = _coerce_int(raw[1], field_name=f"beat_frame_ranges[{beat_id}][1]")
            if start < 0 or end <= start:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    "beat 帧区间必须满足 0 <= start < end",
                    {"beat_id": beat_id, "start": start, "end": end},
                )
            ranges.append((str(beat_id), (start, end)))
        ranges.sort(key=lambda item: (item[1][0], item[0]))
        total_frames = max(end for _, (_, end) in ranges)

        def _read_change_frames(values: Sequence[int], *, field_name: str) -> list[int]:
            out: list[int] = []
            for entry in values or ():
                frame = _coerce_int(entry, field_name=field_name)
                if frame < 0 or frame > total_frames:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        f"{field_name} 的帧号必须位于 [0, 片长] 内",
                        {"frame": frame, "total_frames": total_frames},
                    )
                out.append(frame)
            return sorted(dict.fromkeys(out))

        transitions = _read_change_frames(transition_frames, field_name="transition_frames")
        entity_changes = _read_change_frames(entity_change_frames, field_name="entity_change_frames")
        text_changes = _read_change_frames(text_change_frames, field_name="text_change_frames")

        anomalies: list[Interval] = []
        for issue in self.repo.open_issues_for_edition(edition_id):
            start = issue.get("start_frame")
            end = issue.get("end_frame_exclusive")
            if start is None or end is None or int(end) <= int(start):
                continue
            anomalies.append((max(0, int(start)), min(total_frames, int(end))))
        anomalies = [item for item in _merge_intervals(anomalies) if item[1] > item[0]]

        base_multiplier = DENSITY_MULTIPLIERS[density_key]
        sampled: set[int] = set()
        per_beat: dict[str, Any] = {}
        for beat_id, (start, end) in ranges:
            length = end - start
            anomalous = any(_intervals_overlap((start, end), item) for item in anomalies)
            multiplier = base_multiplier * (2 if anomalous else 1)
            frames: set[int] = {start, start + length // 2, end - 1}
            for level in range(1, multiplier + 1):
                denominator = 1 << (level + 1)
                for step in range(1, denominator, 2):
                    offset = (length * step) // denominator
                    if 0 <= offset < length:
                        frames.add(start + offset)
            frames = {frame for frame in frames if start <= frame < end}
            sampled |= frames
            per_beat[beat_id] = {
                "frame_range": [start, end],
                "sampled_frame_ids": sorted(frames),
                "sample_count": len(frames),
                "density_multiplier": multiplier,
                "density": density_key,
                "inside_flagged_anomaly": anomalous,
            }

        def _window(frames: Sequence[int], radius: int) -> list[Interval]:
            windows: list[Interval] = []
            for frame in frames:
                low = max(0, frame - radius)
                high = min(total_frames, frame + radius + 1)
                if high > low:
                    windows.append((low, high))
                    sampled.update(range(low, high))
            return _merge_intervals(windows)

        cut_windows = _window(transitions, CUT_SAMPLE_RADIUS)
        entity_windows = _window(entity_changes, CHANGE_SAMPLE_RADIUS)
        text_windows = _window(text_changes, CHANGE_SAMPLE_RADIUS)
        for start, end in anomalies:
            sampled |= {start, (start + end) // 2, max(start, end - 1)}

        sampled_frame_ids = sorted(frame for frame in sampled if 0 <= frame < total_frames)
        if not sampled_frame_ids:
            raise ExplainerContractError(
                "INVALID_REQUEST", "抽样计划为空：没有任何可用帧", {"total_frames": total_frames}
            )
        sampled_intervals = _merge_intervals([(frame, frame + 1) for frame in sampled_frame_ids])
        ratio = round(len(sampled_frame_ids) / total_frames, 6) if total_frames else 0.0
        plan_hash = content_hash(
            {
                "video_id": video_id,
                "edition_id": edition_id,
                "density": density_key,
                "sampled_frame_ids": sampled_frame_ids,
                "total_frames": total_frames,
            }
        )
        return {
            "video_id": video_id,
            "edition_id": edition_id,
            "total_frames": total_frames,
            "sampled_frame_ids": sampled_frame_ids,
            "sampled_intervals": [list(item) for item in sampled_intervals],
            "sampling_ratio": ratio,
            "per_beat": per_beat,
            "density": density_key,
            "density_multiplier": base_multiplier,
            "cut_windows": [list(item) for item in cut_windows],
            "entity_change_windows": [list(item) for item in entity_windows],
            "text_change_windows": [list(item) for item in text_windows],
            "anomalous_intervals": [list(item) for item in anomalies],
            "plan_hash": plan_hash,
            "sampling_is_full_understanding": False,
            "sampling_purpose": "SEMANTIC_SAMPLE_OF_SELECTED_FRAMES_ONLY",
            "unverified_checks": [
                "SAMPLING_COVERS_SELECTED_FRAMES_ONLY",
                "SEMANTIC_SAMPLE_IS_NOT_FULL_FRAME_UNDERSTANDING",
            ],
        }

    def run_semantic_check(
        self,
        *,
        project_id: str,
        video_id: str,
        edition_id: str | None,
        subject_kind: str,
        subject_revision_id: str,
        subject_hash: str,
        plan: Mapping[str, Any],
        reference_frames: Sequence[Mapping[str, Any]],
        questions: Sequence[str],
    ) -> dict[str, Any]:
        """Layer (b): sampled semantic findings from a real image-reading provider.

        Without an installed provider the report is ``NOT_RUN`` with an
        ``UNKNOWN`` ``SEMANTIC_QC_UNCHECKED`` issue — never a pass.
        """

        self._require_scope(project_id=project_id, video_id=video_id, edition_id=edition_id)
        if not isinstance(plan, Mapping) or not plan.get("sampled_frame_ids"):
            raise ExplainerContractError("INVALID_REQUEST", "抽样计划无效或为空", {})
        expected_plan_hash = content_hash(
            {
                "video_id": plan.get("video_id"),
                "edition_id": plan.get("edition_id"),
                "density": plan.get("density"),
                "sampled_frame_ids": list(plan.get("sampled_frame_ids") or []),
                "total_frames": plan.get("total_frames"),
            }
        )
        if plan.get("plan_hash") and str(plan["plan_hash"]) != expected_plan_hash:
            raise ExplainerContractError(
                "STALE_REVISION",
                "抽样计划已被修改，不能按旧计划声称检查结果",
                {"plan_hash": plan.get("plan_hash"), "recomputed": expected_plan_hash},
            )
        if str(plan.get("video_id") or video_id) != str(video_id):
            raise ExplainerContractError("INVALID_REQUEST", "抽样计划不属于该视频", {"video_id": video_id})
        total_frames = int(plan.get("total_frames") or 0)
        planned_ids = sorted(
            {int(frame) for frame in plan["sampled_frame_ids"] if 0 <= int(frame) < max(total_frames, 1)}
        )
        if not planned_ids:
            raise ExplainerContractError("INVALID_REQUEST", "抽样计划没有任何片内帧号", {})

        by_frame: dict[int, Mapping[str, Any]] = {}
        for entry in reference_frames or ():
            if not isinstance(entry, Mapping):
                raise ExplainerContractError("SCHEMA_INVALID", "reference_frames 的每一项必须是对象", {})
            raw = entry.get("frame_id", entry.get("frame"))
            if raw is None:
                raise ExplainerContractError("SCHEMA_INVALID", "reference_frames 缺少 frame_id", {})
            frame_id = _coerce_int(raw, field_name="reference_frames.frame_id")
            by_frame[frame_id] = entry
        frames_for_provider: list[dict[str, Any]] = []
        for frame_id in planned_ids:
            entry = by_frame.get(frame_id, {})
            frames_for_provider.append(
                {
                    "frame_id": frame_id,
                    "frame_ref": str(entry.get("frame_ref") or frame_id),
                    "rel_path": entry.get("rel_path"),
                    "media_version_id": entry.get("media_version_id"),
                    "frame_range": entry.get("frame_range"),
                }
            )
        capability = self._capability(self.visual_provider)
        unverified: list[str] = list(plan.get("unverified_checks") or [])
        unverified.append("SAMPLING_IS_NOT_FULL_UNDERSTANDING")
        unverified.append("SEMANTIC_COVERAGE_IS_SAMPLED_NOT_COMPLETE")

        coverage = summarise_coverage(
            total_frames=total_frames,
            decoded_frames=0,
            technical_checked_frames=0,
            semantic_checked_frames=0,
            human_reviewed_intervals=(),
            sampled_frame_ids=planned_ids if len(planned_ids) <= MAX_COVERAGE_FRAME_ID_LIST else (),
        )
        coverage.update(
            {
                "semantic_detector": SEMANTIC_DETECTOR,
                "visual_provider_available": capability["available"],
                "visual_provider_id": capability["provider_id"],
                "visual_model_revision": capability["model_revision"],
                "semantic_layer_checked": False,
                "semantic_detector_available": False,
                "sampling_ratio": plan.get("sampling_ratio"),
                "sampled_intervals": plan.get("sampled_intervals"),
                "sampled_frame_id_count": len(planned_ids),
                "sampling_is_full_understanding": False,
                "semantic_equals_full_understanding": False,
                "decode_coverage_established_by_this_report": False,
                "technical_coverage_established_by_this_report": False,
            }
        )
        summary: dict[str, Any] = {
            "layer": LAYER_SEMANTIC,
            "detector": SEMANTIC_DETECTOR,
            "plan_hash": plan.get("plan_hash"),
            "density": plan.get("density"),
            "density_multiplier": plan.get("density_multiplier"),
            "questions": list(questions or ()),
            "provider": capability,
            "status_basis": (
                "语义层只覆盖被抽样的帧；抽样通过不等于全片理解，"
                "未安装 Provider 时结果为 UNCHECKED 而不是 PASS。"
            ),
            "finding_count": 0,
            "unknown_finding_count": 0,
            "sampled_frame_ids": planned_ids if len(planned_ids) <= MAX_COVERAGE_FRAME_ID_LIST else None,
            "sampled_frame_id_count": len(planned_ids),
        }

        if not capability["available"]:
            unverified.append("VISUAL_QC_PROVIDER_UNAVAILABLE")
            report = self._create_report(
                project_id=project_id,
                video_id=video_id,
                edition_id=edition_id,
                subject_kind=subject_kind,
                subject_revision_id=subject_revision_id,
                subject_hash=subject_hash,
                status=QcReportStatus.NOT_RUN.value,
                coverage=coverage,
                unverified_checks=unverified,
                detectors=(
                    {
                        "detector": SEMANTIC_DETECTOR,
                        "detector_version": _detector_version(SEMANTIC_DETECTOR),
                        "layer": LAYER_SEMANTIC,
                        "available": False,
                        "reason": capability["reason"],
                    },
                ),
                summary={**summary, "status_basis": summary["status_basis"], "not_run_reason": capability["reason"]},
            )
            issue = self._insert_issue(
                report=report,
                issue_kind="SEMANTIC_QC_UNCHECKED",
                severity=Severity.UNKNOWN.value,
                detector=SEMANTIC_DETECTOR,
                layer=LAYER_SEMANTIC,
                observed="未执行语义检查",
                expected="由本地视觉 QC Provider 读取抽样帧并给出结论",
                unknown_reason=str(capability["reason"] or "VISUAL_QC_PROVIDER_UNAVAILABLE"),
                evidence={"provider": capability, "sampled_frame_id_count": len(planned_ids)},
                scope={"unchecked_not_passed": True, "sampled_frame_ids": planned_ids[:200]},
            )
            self._link_report(
                project_id=project_id,
                video_id=video_id,
                edition_id=edition_id,
                subject_kind=subject_kind,
                subject_revision_id=subject_revision_id,
                subject_hash=subject_hash,
                report_id=report["id"],
            )
            return {
                "report": report,
                "issues": [issue],
                "status": report["status"],
                "coverage": report["coverage_json"],
                "unverified_checks": report["unverified_checks_json"],
                "detectors": report["detectors_json"],
                "summary": report["summary_json"],
                "provider": capability,
                "checked_frame_ids": [],
                "unchecked_frame_ids": planned_ids,
                "findings": [],
            }

        findings_raw: list[Mapping[str, Any]] = []
        provider_error: str | None = None
        try:
            result = self.visual_provider.check_frames(  # type: ignore[union-attr]
                frames=frames_for_provider, questions=list(questions or ())
            )
            if isinstance(result, (str, bytes)) or not isinstance(result, (list, tuple)):
                raise TypeError("check_frames must return a list of findings")
            findings_raw = [item for item in result if isinstance(item, Mapping)]
            if len(findings_raw) != len(result):
                raise TypeError("check_frames returned a non-mapping finding")
        except Exception as exc:  # noqa: BLE001 - a failing provider is UNCHECKED, never a pass
            # The class name alone told an operator nothing: a configured provider
            # that cannot read the frames and one that is missing a model both
            # surfaced as "VISUAL_QC_PROVIDER_ERROR:ExplainerContractError".  The
            # message and the structured details travel with it (bounded), because
            # the next step depends on which of the two it was.
            provider_error = f"VISUAL_QC_PROVIDER_ERROR:{type(exc).__name__}:{_error_detail_text(exc)}"

        if provider_error:
            unverified.append(provider_error)
            report = self._create_report(
                project_id=project_id,
                video_id=video_id,
                edition_id=edition_id,
                subject_kind=subject_kind,
                subject_revision_id=subject_revision_id,
                subject_hash=subject_hash,
                status=QcReportStatus.NOT_RUN.value,
                coverage=coverage,
                unverified_checks=unverified,
                detectors=(
                    {
                        "detector": SEMANTIC_DETECTOR,
                        "detector_version": _detector_version(SEMANTIC_DETECTOR),
                        "layer": LAYER_SEMANTIC,
                        "available": True,
                        "error": provider_error,
                    },
                ),
                summary={**summary, "provider_error": provider_error},
            )
            issue = self._insert_issue(
                report=report,
                issue_kind="SEMANTIC_QC_UNCHECKED",
                severity=Severity.UNKNOWN.value,
                detector=SEMANTIC_DETECTOR,
                layer=LAYER_SEMANTIC,
                observed="Provider 调用失败",
                expected="Provider 返回逐帧发现",
                unknown_reason=provider_error,
                evidence={"provider": capability},
                scope={"unchecked_not_passed": True},
            )
            self._link_report(
                project_id=project_id,
                video_id=video_id,
                edition_id=edition_id,
                subject_kind=subject_kind,
                subject_revision_id=subject_revision_id,
                subject_hash=subject_hash,
                report_id=report["id"],
            )
            return {
                "report": report,
                "issues": [issue],
                "status": report["status"],
                "coverage": report["coverage_json"],
                "unverified_checks": report["unverified_checks_json"],
                "detectors": report["detectors_json"],
                "summary": report["summary_json"],
                "provider": capability,
                "checked_frame_ids": [],
                "unchecked_frame_ids": planned_ids,
                "findings": [],
            }

        report = self._create_report(
            project_id=project_id,
            video_id=video_id,
            edition_id=edition_id,
            subject_kind=subject_kind,
            subject_revision_id=subject_revision_id,
            subject_hash=subject_hash,
            status=QcReportStatus.RUNNING.value,
            coverage=coverage,
            unverified_checks=unverified,
            detectors=(
                {
                    "detector": SEMANTIC_DETECTOR,
                    "detector_version": _detector_version(SEMANTIC_DETECTOR),
                    "layer": LAYER_SEMANTIC,
                    "available": True,
                    "provider_id": capability["provider_id"],
                    "model_revision": capability["model_revision"],
                    "reads_images": capability["reads_images"],
                    "reads_video": capability["reads_video"],
                },
            ),
            summary={**summary, "checked_frame_id_count": len(frames_for_provider)},
        )

        issues: list[dict[str, Any]] = []
        normalized_findings: list[dict[str, Any]] = []
        planned_set = set(planned_ids)
        for finding in findings_raw:
            raw_ref = finding.get("frame_ref", finding.get("frame_id"))
            frame_id: int | None
            try:
                frame_id = _coerce_int(raw_ref, field_name="finding.frame_ref") if raw_ref is not None else None
            except ExplainerContractError:
                frame_id = None
            issue_kind = str(finding.get("issue_kind") or "VISUAL_FINDING_UNCLASSIFIED")
            unknown_reason = finding.get("unknown_reason")
            confidence = finding.get("confidence")
            if frame_id is None:
                unknown_reason = unknown_reason or "PROVIDER_FRAME_REF_UNRESOLVED"
                unverified.append("PROVIDER_FRAME_REF_UNRESOLVED")
            elif frame_id not in planned_set:
                unknown_reason = unknown_reason or "PROVIDER_RETURNED_UNPLANNED_FRAME"
                unverified.append("PROVIDER_RETURNED_UNPLANNED_FRAME")
            if is_hard_blocker(issue_kind):
                severity = Severity.BLOCKER.value
            elif is_soft_issue(issue_kind):
                severity = Severity.MINOR.value
            elif unknown_reason:
                severity = Severity.UNKNOWN.value
            else:
                severity = UNCLASSIFIED_FINDING_SEVERITY
            resolved_confidence, confidence_note = _coerce_confidence(confidence)
            if confidence_note:
                unknown_reason = " / ".join(item for item in (unknown_reason, confidence_note) if item)
                severity = Severity.UNKNOWN.value
            elif resolved_confidence is not None and resolved_confidence < LOW_CONFIDENCE_THRESHOLD:
                unknown_reason = " / ".join(
                    item
                    for item in (unknown_reason, f"LOW_CONFIDENCE_BELOW_{LOW_CONFIDENCE_THRESHOLD}")
                    if item
                )
                severity = Severity.UNKNOWN.value
            issue = self._insert_issue(
                report=report,
                issue_kind=issue_kind,
                severity=severity,
                detector=SEMANTIC_DETECTOR,
                layer=LAYER_SEMANTIC,
                observed=finding.get("observed", ""),
                expected=finding.get("expected", ""),
                confidence=resolved_confidence,
                unknown_reason=unknown_reason,
                start_frame=frame_id,
                end_frame_exclusive=None if frame_id is None else frame_id + 1,
                evidence={
                    "frame_ref": raw_ref,
                    "provider_id": capability["provider_id"],
                    "model_revision": capability["model_revision"],
                    "questions": list(questions or ()),
                    "sampling_only": True,
                },
                scope={"sampled_frame": frame_id in planned_set},
            )
            issues.append(issue)
            normalized_findings.append(
                {
                    "frame_ref": raw_ref,
                    "frame_id": frame_id,
                    "issue_kind": issue_kind,
                    "severity": severity,
                    "observed": _stringify(finding.get("observed", "")),
                    "expected": _stringify(finding.get("expected", "")),
                    "confidence": resolved_confidence,
                    "unknown_reason": unknown_reason,
                }
            )

        checked_frame_ids = sorted({frame["frame_id"] for frame in frames_for_provider})
        coverage = dict(report["coverage_json"])
        coverage.update(
            {
                "semantic_checked_frames": len(checked_frame_ids),
                "semantic_ratio": round(len(checked_frame_ids) / total_frames, 6) if total_frames else 0.0,
                "semantic_layer_checked": True,
                "semantic_detector_available": True,
                "semantic_equals_full_understanding": False,
            }
        )
        blocker_count = sum(1 for issue in issues if issue["severity"] == Severity.BLOCKER.value)
        if blocker_count:
            status = QcReportStatus.BLOCKED.value
        elif issues:
            status = QcReportStatus.PASS_WITH_ISSUES.value
        else:
            status = QcReportStatus.PASS.value
        summary = dict(report["summary_json"])
        summary.update(
            {
                "finding_count": len(issues),
                "unknown_finding_count": sum(1 for issue in issues if issue["severity"] == Severity.UNKNOWN.value),
                "blocker_count": blocker_count,
                "checked_frame_id_count": len(checked_frame_ids),
                "checked_frame_ids": checked_frame_ids
                if len(checked_frame_ids) <= MAX_COVERAGE_FRAME_ID_LIST
                else None,
                "sampling_is_full_understanding": False,
                "model_score_is_not_factual_proof": True,
            }
        )
        report = self.repo.update(
            "explainer_qc_reports",
            report["id"],
            {
                "status": status,
                "coverage_json": coverage,
                "unverified_checks_json": list(dict.fromkeys(unverified)),
                "summary_json": summary,
            },
        )
        self._link_report(
            project_id=project_id,
            video_id=video_id,
            edition_id=edition_id,
            subject_kind=subject_kind,
            subject_revision_id=subject_revision_id,
            subject_hash=subject_hash,
            report_id=report["id"],
        )
        return {
            "report": report,
            "issues": issues,
            "status": status,
            "coverage": coverage,
            "unverified_checks": report["unverified_checks_json"],
            "detectors": report["detectors_json"],
            "summary": summary,
            "provider": capability,
            "checked_frame_ids": checked_frame_ids,
            "unchecked_frame_ids": [frame for frame in planned_ids if frame not in set(checked_frame_ids)],
            "findings": normalized_findings,
        }

    # ------------------------------------------------------------------ depth layer
    def run_depth_check(
        self,
        *,
        project_id: str,
        video_id: str,
        edition_id: str | None,
        subject_revision_id: str,
        subject_hash: str,
        frame_range: Sequence[int],
        provider: VisualQcProvider | None,
        batch_size: int = 8,
        cancel_check: Callable[[], bool] | None = None,
        already_processed: Sequence[int] = (),
        questions: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Layer (c): full per-frame VLM pass over one named shot, resumable.

        ``already_processed`` lets a stopped pass continue without re-labelling the
        frames it already covered.  Frames that were not processed are reported as
        unchecked; this never claims an unspecified frame was reviewed.
        """

        self._require_scope(project_id=project_id, video_id=video_id, edition_id=edition_id)
        if isinstance(frame_range, (str, bytes)) or not isinstance(frame_range, (list, tuple)) or len(frame_range) != 2:
            raise ExplainerContractError("SCHEMA_INVALID", "frame_range 必须是 [start, end)", {})
        start = _coerce_int(frame_range[0], field_name="frame_range[0]")
        end = _coerce_int(frame_range[1], field_name="frame_range[1]")
        if start < 0 or end <= start:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "frame_range 必须满足 0 <= start < end", {"start": start, "end": end}
            )
        if int(batch_size) <= 0:
            raise ExplainerContractError("INVALID_REQUEST", "batch_size 必须为正整数", {"batch_size": batch_size})
        span = end - start
        frame_ids = list(range(start, end))
        processed: set[int] = set()
        for entry in already_processed or ():
            frame_id = _coerce_int(entry, field_name="already_processed")
            if frame_id in frame_ids:
                processed.add(frame_id)
        pending = [frame_id for frame_id in frame_ids if frame_id not in processed]

        capability = self._capability(provider)
        unverified = ["DEEP_PASS_COVERS_ONLY_PROCESSED_FRAMES", "UNPROCESSED_FRAMES_ARE_UNCHECKED"]
        coverage = summarise_coverage(
            total_frames=span,
            decoded_frames=0,
            technical_checked_frames=0,
            semantic_checked_frames=len(processed),
            human_reviewed_intervals=(),
            sampled_frame_ids=sorted(processed) if len(processed) <= MAX_COVERAGE_FRAME_ID_LIST else (),
        )
        coverage.update(
            {
                "semantic_detector": DEPTH_DETECTOR,
                "scope_frame_range": [start, end],
                "visual_provider_available": capability["available"],
                "visual_provider_id": capability["provider_id"],
                "visual_model_revision": capability["model_revision"],
                "semantic_layer_checked": bool(processed) and capability["available"],
                "semantic_detector_available": bool(processed) and capability["available"],
                "decode_coverage_established_by_this_report": False,
                "technical_coverage_established_by_this_report": False,
                "processed_intervals": [list(item) for item in _merge_intervals([(f, f + 1) for f in sorted(processed)])],
                "unprocessed_intervals": [
                    list(item) for item in _merge_intervals([(f, f + 1) for f in pending])
                ],
                "processed_frame_count": len(processed),
                "unprocessed_frame_count": len(pending),
                "semantic_equals_full_understanding": False,
            }
        )

        if not capability["available"]:
            unverified.append("VISUAL_QC_PROVIDER_UNAVAILABLE")
            report = self._create_report(
                project_id=project_id,
                video_id=video_id,
                edition_id=edition_id,
                subject_kind=QcSubjectKind.VISUAL_BEAT.value,
                subject_revision_id=subject_revision_id,
                subject_hash=subject_hash,
                status=QcReportStatus.NOT_RUN.value,
                coverage=coverage,
                unverified_checks=unverified,
                detectors=(
                    {
                        "detector": DEPTH_DETECTOR,
                        "detector_version": _detector_version(DEPTH_DETECTOR),
                        "layer": LAYER_DEPTH,
                        "frame_range": [start, end],
                        "available": False,
                        "reason": capability["reason"],
                    },
                ),
                summary={
                    "layer": LAYER_DEPTH,
                    "frame_range": [start, end],
                    "provider": capability,
                    "status_basis": "没有可用的逐帧理解能力时，结果为 UNCHECKED 而不是通过。",
                },
            )
            issue = self._insert_issue(
                report=report,
                issue_kind="SEMANTIC_QC_UNCHECKED",
                severity=Severity.UNKNOWN.value,
                detector=DEPTH_DETECTOR,
                layer=LAYER_DEPTH,
                start_frame=start,
                end_frame_exclusive=end,
                observed="逐帧深度检查未执行",
                expected="Provider 实际读取每一帧",
                unknown_reason=str(capability["reason"] or "VISUAL_QC_PROVIDER_UNAVAILABLE"),
                evidence={"provider": capability, "frame_range": [start, end]},
                scope={"unchecked_not_passed": True},
            )
            self._link_report(
                project_id=project_id,
                video_id=video_id,
                edition_id=edition_id,
                subject_kind=QcSubjectKind.VISUAL_BEAT.value,
                subject_revision_id=subject_revision_id,
                subject_hash=subject_hash,
                report_id=report["id"],
            )
            return {
                "report": report,
                "issues": [issue],
                "status": report["status"],
                "coverage": coverage,
                "unverified_checks": report["unverified_checks_json"],
                "detectors": report["detectors_json"],
                "summary": report["summary_json"],
                "provider": capability,
                "processed_frame_ids": sorted(processed),
                "unprocessed_frame_ids": pending,
                "cancelled": False,
                "resumable": True,
                "findings": [],
            }

        report = self._create_report(
            project_id=project_id,
            video_id=video_id,
            edition_id=edition_id,
            subject_kind=QcSubjectKind.VISUAL_BEAT.value,
            subject_revision_id=subject_revision_id,
            subject_hash=subject_hash,
            status=QcReportStatus.RUNNING.value,
            coverage=coverage,
            unverified_checks=unverified,
            detectors=(
                {
                    "detector": DEPTH_DETECTOR,
                    "detector_version": _detector_version(DEPTH_DETECTOR),
                    "layer": LAYER_DEPTH,
                    "frame_range": [start, end],
                    "available": True,
                    "provider_id": capability["provider_id"],
                    "model_revision": capability["model_revision"],
                    "batch_size": int(batch_size),
                },
            ),
            summary={"layer": LAYER_DEPTH, "frame_range": [start, end], "provider": capability},
        )

        issues: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        cancelled = False
        error: str | None = None
        cursor = 0
        while cursor < len(pending):
            if cancel_check is not None and bool(cancel_check()):
                cancelled = True
                break
            batch = pending[cursor : cursor + int(batch_size)]
            frames = [{"frame_id": frame_id, "frame_ref": str(frame_id)} for frame_id in batch]
            try:
                result = provider.check_frames(frames=frames, questions=list(questions or ()))  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001 - a failing batch stays unchecked
                error = f"VISUAL_QC_PROVIDER_ERROR:{type(exc).__name__}:{_error_detail_text(exc)}"
                unverified.append(error)
                break
            if isinstance(result, (str, bytes)) or not isinstance(result, (list, tuple)):
                error = "VISUAL_QC_PROVIDER_ERROR:MALFORMED_RESULT"
                unverified.append(error)
                break
            processed.update(batch)
            for finding in result:
                if not isinstance(finding, Mapping):
                    error = "VISUAL_QC_PROVIDER_ERROR:MALFORMED_FINDING"
                    unverified.append(error)
                    continue
                raw_ref = finding.get("frame_ref", finding.get("frame_id"))
                frame_id = _coerce_int(raw_ref, field_name="finding.frame_ref") if raw_ref is not None else None
                if frame_id is not None and frame_id not in frame_ids:
                    frame_id = None
                issue_kind = str(finding.get("issue_kind") or "VISUAL_FINDING_UNCLASSIFIED")
                unknown_reason = finding.get("unknown_reason")
                if frame_id is None:
                    unknown_reason = unknown_reason or "PROVIDER_FRAME_REF_UNRESOLVED"
                if is_hard_blocker(issue_kind):
                    severity = Severity.BLOCKER.value
                elif is_soft_issue(issue_kind):
                    severity = Severity.MINOR.value
                else:
                    severity = Severity.UNKNOWN.value if unknown_reason else UNCLASSIFIED_FINDING_SEVERITY
                resolved_confidence, confidence_note = _coerce_confidence(finding.get("confidence"))
                if confidence_note:
                    unknown_reason = " / ".join(item for item in (unknown_reason, confidence_note) if item)
                    severity = Severity.UNKNOWN.value
                elif resolved_confidence is not None and resolved_confidence < LOW_CONFIDENCE_THRESHOLD:
                    unknown_reason = " / ".join(
                        item
                        for item in (unknown_reason, f"LOW_CONFIDENCE_BELOW_{LOW_CONFIDENCE_THRESHOLD}")
                        if item
                    )
                    severity = Severity.UNKNOWN.value
                issue = self._insert_issue(
                    report=report,
                    issue_kind=issue_kind,
                    severity=severity,
                    detector=DEPTH_DETECTOR,
                    layer=LAYER_DEPTH,
                    start_frame=frame_id,
                    end_frame_exclusive=None if frame_id is None else frame_id + 1,
                    observed=finding.get("observed", ""),
                    expected=finding.get("expected", ""),
                    confidence=resolved_confidence,
                    unknown_reason=unknown_reason,
                    evidence={
                        "frame_ref": raw_ref,
                        "provider_id": capability["provider_id"],
                        "model_revision": capability["model_revision"],
                        "deep_pass": True,
                    },
                )
                issues.append(issue)
                findings.append(
                    {
                        "frame_ref": raw_ref,
                        "frame_id": frame_id,
                        "issue_kind": issue_kind,
                        "severity": severity,
                        "confidence": resolved_confidence,
                        "unknown_reason": unknown_reason,
                    }
                )
            cursor += len(batch)

        unprocessed = [frame_id for frame_id in frame_ids if frame_id not in processed]
        processed_intervals = _merge_intervals([(frame_id, frame_id + 1) for frame_id in sorted(processed)])
        unprocessed_intervals = _merge_intervals([(frame_id, frame_id + 1) for frame_id in unprocessed])
        coverage.update(
            {
                "semantic_checked_frames": len(processed),
                "semantic_ratio": round(len(processed) / span, 6) if span else 0.0,
                "sampled_frame_ids": sorted(processed) if len(processed) <= MAX_COVERAGE_FRAME_ID_LIST else [],
                "semantic_layer_checked": bool(processed),
                "semantic_detector_available": bool(processed),
                "processed_intervals": [list(item) for item in processed_intervals],
                "unprocessed_intervals": [list(item) for item in unprocessed_intervals],
                "processed_frame_count": len(processed),
                "unprocessed_frame_count": len(unprocessed),
            }
        )
        if unprocessed:
            unverified.append(
                "FRAMES_NOT_REVIEWED:"
                + ";".join(f"{item[0]}-{item[1]}" for item in unprocessed_intervals)
            )
        blocker_count = sum(1 for issue in issues if issue["severity"] == Severity.BLOCKER.value)
        if not processed:
            status = QcReportStatus.NOT_RUN.value
        elif unprocessed:
            status = QcReportStatus.RUNNING.value
        elif blocker_count:
            status = QcReportStatus.BLOCKED.value
        elif issues:
            status = QcReportStatus.PASS_WITH_ISSUES.value
        else:
            status = QcReportStatus.PASS.value
        summary = dict(report["summary_json"])
        summary.update(
            {
                "finding_count": len(issues),
                "blocker_count": blocker_count,
                "processed_frame_ids": sorted(processed) if len(processed) <= MAX_COVERAGE_FRAME_ID_LIST else None,
                "processed_intervals": [list(item) for item in processed_intervals],
                "unprocessed_intervals": [list(item) for item in unprocessed_intervals],
                "cancelled": cancelled,
                "resumable": bool(unprocessed),
                "requested_frame_range": [start, end],
                "unreviewed_frames_never_claimed": True,
            }
        )
        report = self.repo.update(
            "explainer_qc_reports",
            report["id"],
            {
                "status": status,
                "coverage_json": coverage,
                "unverified_checks_json": list(dict.fromkeys(unverified)),
                "summary_json": summary,
            },
        )
        self._link_report(
            project_id=project_id,
            video_id=video_id,
            edition_id=edition_id,
            subject_kind=QcSubjectKind.VISUAL_BEAT.value,
            subject_revision_id=subject_revision_id,
            subject_hash=subject_hash,
            report_id=report["id"],
        )
        return {
            "report": report,
            "issues": issues,
            "status": status,
            "coverage": coverage,
            "unverified_checks": report["unverified_checks_json"],
            "detectors": report["detectors_json"],
            "summary": summary,
            "provider": capability,
            "processed_frame_ids": sorted(processed),
            "unprocessed_frame_ids": unprocessed,
            "cancelled": cancelled,
            "resumable": bool(unprocessed),
            "findings": findings,
        }

    # ------------------------------------------------------------------ subtitle layer
    def run_subtitle_check(
        self,
        *,
        project_id: str,
        video_id: str,
        edition_id: str | None,
        subject_hash: str,
        cues: Sequence[Mapping[str, Any]],
        safe_area: Mapping[str, Any],
        fps_num: int,
        fps_den: int,
        total_frames: int,
        current_revision_id: str | None,
    ) -> dict[str, Any]:
        """``subtitle_layout_v1``: frozen-revision text, safe area, reading rate, overlap."""

        scope = self._require_scope(project_id=project_id, video_id=video_id, edition_id=edition_id)
        fps_num = _coerce_int(fps_num, field_name="fps_num")
        fps_den = _coerce_int(fps_den, field_name="fps_den")
        total_frames = _coerce_int(total_frames, field_name="total_frames")
        if fps_num <= 0 or fps_den <= 0:
            raise ExplainerContractError("INVALID_REQUEST", "帧率必须为正", {"fps_num": fps_num, "fps_den": fps_den})
        if total_frames < 0:
            raise ExplainerContractError("INVALID_REQUEST", "总帧数不能为负", {"total_frames": total_frames})
        rect = _read_safe_area(safe_area)
        film_end_ms = total_frames * fps_den * 1000 // fps_num if total_frames else 0
        edition = scope.get("edition")
        frozen_revision_id = str((edition or {}).get("frozen_subtitle_revision_id") or current_revision_id or "")

        coverage = summarise_coverage(
            total_frames=total_frames,
            decoded_frames=0,
            technical_checked_frames=0,
            semantic_checked_frames=0,
            human_reviewed_intervals=(),
            sampled_frame_ids=(),
        )
        unverified = [
            "SUBTITLE_LAYOUT_DOES_NOT_ESTABLISH_DECODE_OR_SEMANTIC_COVERAGE",
            "SUBTITLE_LAYOUT_DETECTOR_READS_DECLARED_CUE_GEOMETRY",
        ]
        report = self._create_report(
            project_id=project_id,
            video_id=video_id,
            edition_id=edition_id,
            subject_kind=QcSubjectKind.SUBTITLE_REVISION.value,
            subject_revision_id=frozen_revision_id or "UNFROZEN_SUBTITLE",
            subject_hash=subject_hash,
            status=QcReportStatus.RUNNING.value,
            coverage=coverage,
            unverified_checks=unverified,
            detectors=(
                {
                    "detector": SUBTITLE_DETECTOR,
                    "detector_version": _detector_version(SUBTITLE_DETECTOR),
                    "layer": LAYER_SUBTITLE,
                    "safe_area": list(rect),
                    "fps": {"num": fps_num, "den": fps_den},
                    "film_end_ms": film_end_ms,
                },
            ),
            summary={"layer": LAYER_SUBTITLE, "cue_count": len(cues or ())},
        )

        issues: list[dict[str, Any]] = []
        normalized_cues: list[dict[str, Any]] = []
        for index, cue in enumerate(cues or ()):
            if not isinstance(cue, Mapping):
                raise ExplainerContractError("SCHEMA_INVALID", "cues 的每一项必须是对象", {"index": index})
            cue_id = str(cue.get("cue_id") or cue.get("id") or f"cue-{index}")
            start_ms = _coerce_int(cue.get("start_ms", 0), field_name=f"cues[{index}].start_ms")
            end_ms = _coerce_int(cue.get("end_ms", start_ms), field_name=f"cues[{index}].end_ms")
            text = str(cue.get("text") or "")
            locale = str(cue.get("locale") or (edition or {}).get("voice_locale") or "zh-CN")
            declared_revision = cue.get("subtitle_revision_id") or cue.get("revision_id")
            normalized_cues.append(
                {
                    "cue_id": cue_id,
                    "locale": locale,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "text": text,
                    "line_count": int(cue.get("line_count") or (2 if "\n" in text else 1)),
                    "subtitle_revision_id": None if declared_revision is None else str(declared_revision),
                    "box": _cue_box(cue),
                    "paired_locale": cue.get("paired_locale"),
                }
            )

        def add(**kwargs: Any) -> None:
            issues.append(self._insert_issue(report=report, detector=SUBTITLE_DETECTOR, **kwargs))

        # ------ frozen revision text
        for cue in normalized_cues:
            declared = cue["subtitle_revision_id"]
            start_frame = _ms_to_frames(cue["start_ms"], (fps_num, fps_den))
            end_frame = _ms_to_frames(cue["end_ms"], (fps_num, fps_den))
            if frozen_revision_id and declared and declared != frozen_revision_id:
                add(
                    issue_kind="SUBTITLE_STALE_TEXT",
                    severity=Severity.BLOCKER.value,
                    layer=LAYER_SUBTITLE,
                    start_ms=cue["start_ms"],
                    end_ms=cue["end_ms"],
                    start_frame=start_frame,
                    end_frame_exclusive=end_frame,
                    locale=cue["locale"],
                    subtitle_cue_id=cue["cue_id"],
                    observed=f"cue 文本来自字幕 revision {declared}",
                    expected=f"用于本次渲染的冻结字幕 revision {frozen_revision_id}",
                    evidence_text_span=cue["text"][:400],
                    evidence={
                        "cue_subtitle_revision_id": declared,
                        "frozen_subtitle_revision_id": frozen_revision_id,
                    },
                )
            elif not declared:
                add(
                    issue_kind="SUBTITLE_REVISION_UNVERIFIED",
                    severity=Severity.MAJOR.value,
                    layer=LAYER_SUBTITLE,
                    start_ms=cue["start_ms"],
                    end_ms=cue["end_ms"],
                    start_frame=start_frame,
                    end_frame_exclusive=end_frame,
                    locale=cue["locale"],
                    subtitle_cue_id=cue["cue_id"],
                    observed="cue 未声明所属字幕 revision",
                    expected=f"应声明冻结字幕 revision {frozen_revision_id or '(未冻结)'}",
                    unknown_reason="CUE_REVISION_UNSPECIFIED",
                    evidence_text_span=cue["text"][:400],
                )

        # ------ duration / film end
        for cue in normalized_cues:
            start_frame = _ms_to_frames(cue["start_ms"], (fps_num, fps_den))
            end_frame = _ms_to_frames(cue["end_ms"], (fps_num, fps_den))
            duration_ms = cue["end_ms"] - cue["start_ms"]
            if duration_ms <= 0:
                add(
                    issue_kind="SUBTITLE_NEGATIVE_DURATION",
                    severity=Severity.BLOCKER.value,
                    layer=LAYER_SUBTITLE,
                    start_ms=cue["start_ms"],
                    end_ms=cue["end_ms"],
                    start_frame=start_frame,
                    end_frame_exclusive=end_frame,
                    locale=cue["locale"],
                    subtitle_cue_id=cue["cue_id"],
                    observed=f"{duration_ms}ms",
                    expected="> 0ms",
                    evidence={"duration_ms": duration_ms},
                )
            if film_end_ms and cue["end_ms"] > film_end_ms:
                add(
                    issue_kind="SUBTITLE_BEYOND_FILM_END",
                    severity=Severity.BLOCKER.value,
                    layer=LAYER_SUBTITLE,
                    start_ms=cue["start_ms"],
                    end_ms=cue["end_ms"],
                    start_frame=start_frame,
                    end_frame_exclusive=end_frame,
                    locale=cue["locale"],
                    subtitle_cue_id=cue["cue_id"],
                    observed=f"cue 结束于 {cue['end_ms']}ms",
                    expected=f"片长 {film_end_ms}ms 以内",
                    evidence={
                        "film_end_ms": film_end_ms,
                        "total_frames": total_frames,
                        "cue_frame_range": [start_frame, end_frame],
                        "cue_range_exceeds_film": True,
                    },
                )

        # ------ overlapping cues on the same locale/track
        by_locale: dict[str, list[dict[str, Any]]] = {}
        for cue in normalized_cues:
            by_locale.setdefault(cue["locale"], []).append(cue)
        for locale, locale_cues in sorted(by_locale.items()):
            ordered = sorted(locale_cues, key=lambda item: (item["start_ms"], item["cue_id"]))
            for previous, current in zip(ordered, ordered[1:], strict=False):
                overlap_ms = previous["end_ms"] - current["start_ms"]
                if overlap_ms > SUBTITLE_OVERLAP_TOLERANCE_MS:
                    add(
                        issue_kind="SUBTITLE_CUE_OVERLAP",
                        severity=Severity.MAJOR.value,
                        layer=LAYER_SUBTITLE,
                        start_ms=current["start_ms"],
                        end_ms=min(previous["end_ms"], current["end_ms"]),
                        start_frame=_ms_to_frames(current["start_ms"], (fps_num, fps_den)),
                        end_frame_exclusive=_ms_to_frames(min(previous["end_ms"], current["end_ms"]), (fps_num, fps_den)),
                        locale=locale,
                        subtitle_cue_id=current["cue_id"],
                        observed=f"与 {previous['cue_id']} 重叠 {overlap_ms}ms",
                        expected=f"同轨同语言重叠 <= {SUBTITLE_OVERLAP_TOLERANCE_MS}ms",
                        evidence={
                            "previous_cue_id": previous["cue_id"],
                            "overlap_ms": overlap_ms,
                            "tolerance_ms": SUBTITLE_OVERLAP_TOLERANCE_MS,
                        },
                    )

        # ------ reading rate and safe area / bilingual reflow
        for cue in normalized_cues:
            duration_ms = cue["end_ms"] - cue["start_ms"]
            if duration_ms <= 0 or not cue["text"].strip():
                continue
            rate = subtitle_reading_rates(locale=cue["locale"], text=cue["text"], duration_ms=duration_ms)
            limit = float(rate["limit_characters_per_second"])
            actual = float(rate["characters_per_second"])
            if rate["exceeds_limit"]:
                gross = actual > limit * SUBTITLE_READING_RATE_GROSS_FACTOR
                add(
                    issue_kind="SUBTITLE_READING_RATE_EXCEEDED" if gross else "SUBTITLE_READING_RATE_HINT",
                    severity=Severity.MAJOR.value if gross else Severity.MINOR.value,
                    layer=LAYER_SUBTITLE,
                    start_ms=cue["start_ms"],
                    end_ms=cue["end_ms"],
                    start_frame=_ms_to_frames(cue["start_ms"], (fps_num, fps_den)),
                    end_frame_exclusive=_ms_to_frames(cue["end_ms"], (fps_num, fps_den)),
                    locale=cue["locale"],
                    subtitle_cue_id=cue["cue_id"],
                    observed=f"{actual} 字/秒",
                    expected=f"<= {limit} 字/秒",
                    confidence=None,
                    evidence={"reading_rate": rate, "gross_factor": SUBTITLE_READING_RATE_GROSS_FACTOR},
                    scope={"soft_hint": not gross, "never_triggers_automatic_redraw": not gross},
                )
            box = cue["box"]
            start_frame = _ms_to_frames(cue["start_ms"], (fps_num, fps_den))
            end_frame = _ms_to_frames(cue["end_ms"], (fps_num, fps_den))
            if box is None:
                add(
                    issue_kind="SUBTITLE_GEOMETRY_UNKNOWN",
                    severity=Severity.UNKNOWN.value,
                    layer=LAYER_SUBTITLE,
                    start_ms=cue["start_ms"],
                    end_ms=cue["end_ms"],
                    start_frame=start_frame,
                    end_frame_exclusive=end_frame,
                    locale=cue["locale"],
                    subtitle_cue_id=cue["cue_id"],
                    observed="cue 未提供几何信息",
                    expected="提供 box 或 font_size_px 以检查安全区",
                    unknown_reason="CUE_GEOMETRY_UNSPECIFIED",
                    evidence={"safe_area": list(rect)},
                )
                continue
            outside = _box_outside(box, rect)
            if outside:
                add(
                    issue_kind="SUBTITLE_OUT_OF_SAFE_AREA",
                    severity=Severity.BLOCKER.value,
                    layer=LAYER_SUBTITLE,
                    start_ms=cue["start_ms"],
                    end_ms=cue["end_ms"],
                    start_frame=start_frame,
                    end_frame_exclusive=end_frame,
                    locale=cue["locale"],
                    subtitle_cue_id=cue["cue_id"],
                    observed=f"字幕框 {box} 超出安全区 {list(rect)}",
                    expected="字幕完全位于安全区内",
                    evidence={"box": box, "safe_area": list(rect), "outside_edges": outside},
                )
            elif box["width"] > (rect[2] - rect[0]) and cue["line_count"] >= 1:
                add(
                    issue_kind="SUBTITLE_BILINGUAL_OVERFLOW",
                    severity=Severity.MAJOR.value,
                    layer=LAYER_SUBTITLE,
                    start_ms=cue["start_ms"],
                    end_ms=cue["end_ms"],
                    start_frame=start_frame,
                    end_frame_exclusive=end_frame,
                    locale=cue["locale"],
                    subtitle_cue_id=cue["cue_id"],
                    observed=f"文本宽度 {box['width']}px 超过安全区宽度 {rect[2] - rect[0]}px",
                    expected="折行或缩短文案后重新排版",
                    evidence={"box": box, "safe_area": list(rect), "reflow_required": True},
                )

        blocker_count = sum(1 for issue in issues if issue["severity"] == Severity.BLOCKER.value)
        if blocker_count:
            status = QcReportStatus.BLOCKED.value
        elif issues:
            status = QcReportStatus.PASS_WITH_ISSUES.value
        else:
            status = QcReportStatus.PASS.value
        fallback_required = blocker_count > 0
        coverage.update(
            {
                "subtitle_cue_count": len(normalized_cues),
                "subtitle_locales": sorted(by_locale),
                "frozen_subtitle_revision_id": frozen_revision_id or None,
                "decode_coverage_established_by_this_report": False,
                "semantic_coverage_established_by_this_report": False,
            }
        )
        summary = dict(report["summary_json"])
        summary.update(
            {
                "issue_count": len(issues),
                "blocker_count": blocker_count,
                "fallback_required": fallback_required,
                "soft_subtitle_fallback_only_if_pre_authorized": True,
                "film_end_ms": film_end_ms,
                "fps": {"num": fps_num, "den": fps_den},
                "cue_ids": [cue["cue_id"] for cue in normalized_cues],
                "reading_rate_limits": {
                    "cjk_chars_per_second": 8.0,
                    "latin_chars_per_second": 20.0,
                    "source": "PRODUCT_DEFAULT_NOT_PLATFORM_STANDARD",
                },
            }
        )
        report = self.repo.update(
            "explainer_qc_reports",
            report["id"],
            {
                "status": status,
                "coverage_json": coverage,
                "unverified_checks_json": list(dict.fromkeys(unverified)),
                "summary_json": summary,
            },
        )
        self._link_report(
            project_id=project_id,
            video_id=video_id,
            edition_id=edition_id,
            subject_kind=QcSubjectKind.SUBTITLE_REVISION.value,
            subject_revision_id=frozen_revision_id or "UNFROZEN_SUBTITLE",
            subject_hash=subject_hash,
            report_id=report["id"],
        )
        return {
            "report": report,
            "issues": issues,
            "status": status,
            "coverage": coverage,
            "unverified_checks": report["unverified_checks_json"],
            "detectors": report["detectors_json"],
            "summary": summary,
            "fallback_required": fallback_required,
            "soft_subtitle_fallback_only_if_pre_authorized": True,
            "cues": normalized_cues,
        }

    # ------------------------------------------------------------------ fact layer
    def _existing_narration_segment_id(self, segment: Mapping[str, Any]) -> str | None:
        """Only bind a real narration_segments row; a dangling id would break the FK."""

        candidate = segment.get("id") or segment.get("narration_segment_id")
        if not candidate:
            return None
        row = self.repo.find("narration_segments", str(candidate))
        return str(row["id"]) if row is not None else None

    def run_fact_check(
        self,
        *,
        project_id: str,
        video_id: str,
        subject_hash: str,
        claims: Sequence[Mapping[str, Any]],
        segment_claims: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Deterministic-first factual gate (design §18.2, §12.2).

        Numbers/names/sources are decided by resolution against recorded evidence.
        A model confidence is recorded but never clears a conflict.
        """

        self._require_scope(project_id=project_id, video_id=video_id)
        claim_index: dict[str, dict[str, Any]] = {}
        for index, claim in enumerate(claims or ()):
            if not isinstance(claim, Mapping):
                raise ExplainerContractError("SCHEMA_INVALID", "claims 的每一项必须是对象", {"index": index})
            code = str(claim.get("claim_id") or claim.get("code") or claim.get("id") or "")
            if not code:
                raise ExplainerContractError("SCHEMA_INVALID", "claim 缺少 claim_id/code", {"index": index})
            if code in claim_index:
                raise ExplainerContractError("SCHEMA_INVALID", "claim 编码重复", {"code": code})
            evidence = [item for item in (claim.get("evidence") or []) if isinstance(item, Mapping)]
            resolvable = [
                item
                for item in evidence
                if bool(item.get("resolves", item.get("resolved", True)))
                and (
                    item.get("span_id")
                    or item.get("source_span_id")
                    or item.get("evidence_id")
                    or item.get("quote_text")
                )
            ]
            claim_index[code] = {
                "claim_id": code,
                "statement": str(claim.get("statement") or ""),
                "status": str(claim.get("status") or ClaimStatus.UNVERIFIED.value),
                "importance": str(claim.get("importance") or "KEY"),
                "evidence_count": len(evidence),
                "resolvable_evidence_count": len(resolvable),
                "evidence": evidence,
                "key_terms": [str(item) for item in (claim.get("key_terms") or [])],
                "aliases": [str(item) for item in (claim.get("aliases") or [])],
                "model_confidence": claim.get("model_confidence", claim.get("confidence")),
            }

        script_revisions = {
            str(segment.get("script_revision_id"))
            for segment in (segment_claims or ())
            if isinstance(segment, Mapping) and segment.get("script_revision_id")
        }
        subject_revision_id = (
            next(iter(script_revisions)) if len(script_revisions) == 1 else f"fact-ledger:{subject_hash[:24]}"
        )
        coverage = summarise_coverage(
            total_frames=0,
            decoded_frames=0,
            technical_checked_frames=0,
            semantic_checked_frames=0,
            human_reviewed_intervals=(),
            sampled_frame_ids=(),
        )
        coverage.update(
            {
                "fact_layer": True,
                "claim_count": len(claim_index),
                "segment_count": len(segment_claims or ()),
                "decode_coverage_established_by_this_report": False,
                "semantic_coverage_established_by_this_report": False,
            }
        )
        report = self._create_report(
            project_id=project_id,
            video_id=video_id,
            edition_id=None,
            subject_kind=QcSubjectKind.SCRIPT_REVISION.value,
            subject_revision_id=subject_revision_id,
            subject_hash=subject_hash,
            status=QcReportStatus.RUNNING.value,
            coverage=coverage,
            unverified_checks=(
                "FACT_LAYER_DOES_NOT_ESTABLISH_DECODE_OR_SEMANTIC_COVERAGE",
                "MODEL_CONFIDENCE_NEVER_CLEARS_A_CONFLICT",
            ),
            detectors=(
                {
                    "detector": FACT_DETECTOR,
                    "detector_version": _detector_version(FACT_DETECTOR),
                    "layer": LAYER_FACT,
                    "method": "DETERMINISTIC_RESOLUTION_FIRST",
                },
            ),
            summary={"layer": LAYER_FACT},
        )

        issues: list[dict[str, Any]] = []
        segment_index: list[dict[str, Any]] = []

        def add(**kwargs: Any) -> None:
            issues.append(self._insert_issue(report=report, detector=FACT_DETECTOR, **kwargs))

        for index, segment in enumerate(segment_claims or ()):
            if not isinstance(segment, Mapping):
                raise ExplainerContractError("SCHEMA_INVALID", "segment_claims 的每一项必须是对象", {"index": index})
            segment_id = str(segment.get("segment_id") or segment.get("canonical_segment_id") or f"segment-{index}")
            statement_type = str(segment.get("statement_type") or StatementType.FACT.value).upper()
            display_text = str(segment.get("display_text") or segment.get("text") or "")
            cited = [str(item) for item in (segment.get("claim_ids") or segment.get("claim_refs") or [])]
            cited_records = [claim_index[code] for code in cited if code in claim_index]
            missing_claims = [code for code in cited if code not in claim_index]
            narration_segment_id = self._existing_narration_segment_id(segment)
            entry = {
                "segment_id": segment_id,
                "statement_type": statement_type,
                "cited_claim_ids": cited,
                "resolved_claim_ids": [record["claim_id"] for record in cited_records],
                "unknown_claim_ids": missing_claims,
            }
            segment_index.append(entry)
            if statement_type == StatementType.FACT.value:
                if not cited:
                    add(
                        issue_kind="SOURCE_EVIDENCE_MISSING",
                        severity=Severity.BLOCKER.value,
                        layer=LAYER_FACT,
                        narration_segment_id=narration_segment_id,
                        observed=f"FACT 片段 {segment_id} 未引用任何断言",
                        expected="每个 FACT 片段至少引用一条可定位证据的断言",
                        evidence_text_span=display_text[:400],
                        evidence={"segment_id": segment_id, "cited_claim_ids": []},
                    )
                    continue
                if missing_claims:
                    add(
                        issue_kind="SOURCE_EVIDENCE_MISSING",
                        severity=Severity.BLOCKER.value,
                        layer=LAYER_FACT,
                        narration_segment_id=narration_segment_id,
                        observed=f"引用了不存在的断言：{', '.join(missing_claims)}",
                        expected="引用真实存在的断言编码",
                        evidence_text_span=display_text[:400],
                        evidence={"segment_id": segment_id, "unknown_claim_ids": missing_claims},
                    )
                resolvable = [record for record in cited_records if record["resolvable_evidence_count"] > 0]
                if not resolvable:
                    add(
                        issue_kind="SOURCE_EVIDENCE_MISSING",
                        severity=Severity.BLOCKER.value,
                        layer=LAYER_FACT,
                        narration_segment_id=narration_segment_id,
                        observed=f"FACT 片段 {segment_id} 引用的断言都没有可定位证据",
                        expected="至少一条引用断言可解析到真实来源片段",
                        evidence_text_span=display_text[:400],
                        evidence={
                            "segment_id": segment_id,
                            "cited_claim_ids": cited,
                            "evidence_counts": {
                                record["claim_id"]: record["resolvable_evidence_count"]
                                for record in cited_records
                            },
                        },
                    )
                disputed = [
                    record
                    for record in cited_records
                    if record["status"] == ClaimStatus.DISPUTED.value
                    and record["importance"] in {"CORE", "KEY"}
                ]
                for record in disputed:
                    add(
                        issue_kind="FACT_KEY_CONFLICT",
                        severity=Severity.BLOCKER.value,
                        layer=LAYER_FACT,
                        narration_segment_id=narration_segment_id,
                        observed=f"核心断言 {record['claim_id']} 处于 DISPUTED",
                        expected="核心断言在引用前必须解决冲突",
                        confidence=record["model_confidence"],
                        evidence_text_span=display_text[:400],
                        evidence={
                            "segment_id": segment_id,
                            "claim_id": record["claim_id"],
                            "claim_status": record["status"],
                            "importance": record["importance"],
                            "model_confidence_recorded_only": True,
                        },
                    )
                # deterministic number / name comparison against cited claims
                claim_text = " ".join(record["statement"] for record in cited_records)
                claim_numbers = _extract_numbers(claim_text)
                segment_numbers = _extract_numbers(display_text)
                mismatched_numbers = sorted(segment_numbers - claim_numbers)
                if mismatched_numbers:
                    add(
                        issue_kind="FACT_KEY_NUMBER_MISMATCH",
                        severity=Severity.BLOCKER.value,
                        layer=LAYER_FACT,
                        narration_segment_id=narration_segment_id,
                        observed=f"片段出现未在引用断言中的数字：{', '.join(mismatched_numbers)}",
                        expected="数字必须能在引用断言中找到对应表述",
                        evidence_text_span=display_text[:400],
                        evidence={
                            "segment_id": segment_id,
                            "segment_numbers": sorted(segment_numbers),
                            "claim_numbers": sorted(claim_numbers),
                        },
                    )
                claim_terms = set()
                for record in cited_records:
                    claim_terms.add(record["claim_id"].lower())
                    claim_terms.add(record["statement"])
                    claim_terms.update(term.lower() for term in record["aliases"])
                    claim_terms.update(term.lower() for term in record["key_terms"])
                declared_terms = [str(item) for item in (segment.get("key_terms") or [])]
                mismatched_names = [
                    term
                    for term in declared_terms
                    if term.lower() not in " ".join(claim_terms).lower()
                ]
                if mismatched_names:
                    add(
                        issue_kind="FACT_KEY_NAME_MISMATCH",
                        severity=Severity.BLOCKER.value,
                        layer=LAYER_FACT,
                        narration_segment_id=narration_segment_id,
                        observed=f"关键名称未出现在引用断言中：{', '.join(mismatched_names)}",
                        expected="关键名称必须来自引用断言或其声明的别名",
                        evidence_text_span=display_text[:400],
                        evidence={
                            "segment_id": segment_id,
                            "declared_key_terms": declared_terms,
                            "cited_claim_ids": cited,
                        },
                    )
        # global core-claim conflicts are checked even when no segment cites them
        for record in claim_index.values():
            if record["status"] == ClaimStatus.DISPUTED.value and record["importance"] == "CORE":
                if any(
                    issue["issue_kind"] == "FACT_KEY_CONFLICT"
                    and record["claim_id"] in str(issue["evidence_json"])
                    for issue in issues
                ):
                    continue
                add(
                    issue_kind="FACT_KEY_CONFLICT",
                    severity=Severity.BLOCKER.value,
                    layer=LAYER_FACT,
                    observed=f"核心断言 {record['claim_id']} 处于 DISPUTED",
                    expected="核心断言必须在成稿前解决冲突",
                    confidence=record["model_confidence"],
                    evidence={
                        "claim_id": record["claim_id"],
                        "claim_status": record["status"],
                        "importance": record["importance"],
                        "model_confidence_recorded_only": True,
                    },
                )

        blocker_count = sum(1 for issue in issues if issue["severity"] == Severity.BLOCKER.value)
        if blocker_count:
            status = QcReportStatus.BLOCKED.value
        elif issues:
            status = QcReportStatus.PASS_WITH_ISSUES.value
        else:
            status = QcReportStatus.PASS.value
        coverage.update(
            {
                "claims_checked": len(claim_index),
                "fact_segments_checked": sum(
                    1 for item in segment_index if item["statement_type"] == StatementType.FACT.value
                ),
                "segments_with_unresolved_claims": sum(
                    1 for item in segment_index if item["unknown_claim_ids"]
                ),
            }
        )
        summary = dict(report["summary_json"])
        summary.update(
            {
                "issue_count": len(issues),
                "blocker_count": blocker_count,
                "segments": segment_index,
                "deterministic_checks": [
                    "SOURCE_RESOLUTION",
                    "DISPUTED_KEY_CLAIM",
                    "NUMBER_PRESENCE",
                    "NAME_PRESENCE",
                ],
                "model_confidence_never_clears_conflict": True,
                "checks_are_not_a_truth_proof": True,
            }
        )
        report = self.repo.update(
            "explainer_qc_reports",
            report["id"],
            {"status": status, "coverage_json": coverage, "summary_json": summary},
        )
        self._link_report(
            project_id=project_id,
            video_id=video_id,
            edition_id=None,
            subject_kind=QcSubjectKind.SCRIPT_REVISION.value,
            subject_revision_id=subject_revision_id,
            subject_hash=subject_hash,
            report_id=report["id"],
        )
        return {
            "report": report,
            "issues": issues,
            "status": status,
            "coverage": coverage,
            "unverified_checks": report["unverified_checks_json"],
            "detectors": report["detectors_json"],
            "summary": summary,
            "segment_index": segment_index,
        }

    # ------------------------------------------------------------------ policy / decisions
    def evaluate_policy(
        self,
        *,
        project_id: str,
        video_id: str,
        edition_id: str | None,
        subject_kind: str,
        subject_revision_id: str,
        subject_hash: str,
        report_id: str,
        human_reviewed_intervals: Sequence[Sequence[int]] = (),
        decision_kind: str = DecisionKind.POLICY_ACCEPTED.value,
    ) -> dict[str, Any]:
        """Write a ``POLICY_ACCEPTED`` machine decision bound to one exact hash.

        The machine path can only write ``POLICY_ACCEPTED``; asking it for
        ``HUMAN_APPROVED`` raises instead of producing a fake human approval.

        The work is delegated to the static :meth:`evaluate_policy_for_repo` so a
        worker can drive the same policy path with an explicit repository without
        constructing this service (``scripts/audit_architecture_debt.py`` forbids
        new cross-service construction).
        """

        return ExplainerQualityService.evaluate_policy_for_repo(
            self.repo,
            project_id=project_id,
            video_id=video_id,
            edition_id=edition_id,
            subject_kind=subject_kind,
            subject_revision_id=subject_revision_id,
            subject_hash=subject_hash,
            report_id=report_id,
            human_reviewed_intervals=human_reviewed_intervals,
            decision_kind=decision_kind,
        )

    @staticmethod
    def evaluate_policy_for_repo(
        repo: ExplainerRepository,
        *,
        project_id: str,
        video_id: str,
        edition_id: str | None,
        subject_kind: str,
        subject_revision_id: str,
        subject_hash: str,
        report_id: str,
        human_reviewed_intervals: Sequence[Sequence[int]] = (),
        decision_kind: str = DecisionKind.POLICY_ACCEPTED.value,
    ) -> dict[str, Any]:
        """Repository-explicit form of :meth:`evaluate_policy`."""

        ensure_policy_decision_allowed(
            decision_kind=str(decision_kind),
            actor_type=ActorType.MACHINE.value,
            actor=None,
            policy_processor=POLICY_PROCESSOR_NAME,
        )
        if str(decision_kind) != DecisionKind.POLICY_ACCEPTED.value:
            raise ExplainerContractError(
                ExplainerErrorCode.INVALID_REQUEST.value,
                "机器政策路径只能写入 POLICY_ACCEPTED；人工通过必须由人工通道记录",
                {"decision_kind": str(decision_kind)},
            )
        ExplainerQualityService._require_scope_for_repo(repo, project_id=project_id, video_id=video_id, edition_id=edition_id)
        ExplainerQualityService._require_hash_for_repo(subject_hash, field_name="subject_hash")
        report = repo.get("explainer_qc_reports", report_id)
        if str(report["project_id"]) != str(project_id) or str(report["video_id"]) != str(video_id):
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "QC 报告不属于该项目的该视频，不能用于本判定",
                {
                    "report_project_id": report["project_id"],
                    "report_video_id": report["video_id"],
                    "project_id": project_id,
                    "video_id": video_id,
                },
            )
        if str(report["subject_kind"]) != str(subject_kind) or str(report["subject_revision_id"]) != str(
            subject_revision_id
        ):
            raise ExplainerContractError(
                "STALE_REVISION",
                "QC 报告不是针对该对象写的，不能用于本判定",
                {
                    "report_subject_kind": report["subject_kind"],
                    "report_subject_revision_id": report["subject_revision_id"],
                    "subject_kind": subject_kind,
                    "subject_revision_id": subject_revision_id,
                },
            )
        if str(report["subject_hash"]) != str(subject_hash):
            raise ExplainerContractError(
                "STALE_REVISION",
                "对象内容已变化：报告绑定的哈希与当前哈希不一致，必须重新检查",
                {"report_subject_hash": report["subject_hash"], "subject_hash": subject_hash},
            )

        issue_rows = repo.issues(report_id)
        issue_facts = [_issue_fact(row) for row in issue_rows]
        coverage_json = report.get("coverage_json") or {}
        coverage = CoverageFact(
            total_frames=int(coverage_json.get("total_frames") or 0),
            decoded_frames=int(coverage_json.get("decoded_frames") or 0),
            technical_checked_frames=int(coverage_json.get("technical_checked_frames") or 0),
            semantic_checked_frames=int(coverage_json.get("semantic_checked_frames") or 0),
            human_reviewed_frames=_human_reviewed_frames(human_reviewed_intervals),
            semantic_detector_available=bool(
                coverage_json.get(
                    "semantic_detector_available",
                    int(coverage_json.get("semantic_checked_frames") or 0) > 0,
                )
            ),
        )
        video = repo.require_video_for_project(project_id)
        automation_mode = str(video.get("automation_mode") or "AUTO_WITH_EXCEPTIONS")
        acceptance = evaluate_machine_acceptance(
            subject_kind=str(subject_kind),
            subject_revision_id=str(subject_revision_id),
            subject_hash=str(subject_hash),
            issues=issue_facts,
            coverage=coverage,
            thresholds=Thresholds(),
            automation_mode=automation_mode,
            policy_rule_version=POLICY_RULE_VERSION,
        )
        acceptance, unknown_guard = _apply_unknown_guard(acceptance, issue_facts)

        evidence = dict(acceptance.evidence)
        evidence.update(
            {
                "qc_report_id": report_id,
                "report_status": report["status"],
                "report_policy_version": report.get("policy_version"),
                "unverified_checks": list(report.get("unverified_checks_json") or []),
                "human_reviewed_intervals": [[int(item[0]), int(item[1])] for item in human_reviewed_intervals],
                "unknown_guard": unknown_guard,
                "machine_decision_is_not_human_approval": True,
                "publication_authorized": False,
            }
        )
        limitations = acceptance.limitations
        limitations += (
            " 该机器判定不构成人工审阅、不构成发布授权，也不代表未检测区域已被理解；"
            "soft 提示不会自动触发重绘。"
        )
        if unknown_guard:
            limitations += " 存在 UNKNOWN/未判定结果，机器只能请求人工处理。"
        decision = repo.insert(
            "explainer_decisions",
            {
                "video_id": video_id,
                "project_id": project_id,
                "edition_id": edition_id,
                "decision_kind": DecisionKind.POLICY_ACCEPTED.value,
                "subject_kind": str(subject_kind),
                "subject_revision_id": str(subject_revision_id),
                "subject_hash": str(subject_hash),
                "rule_version": POLICY_RULE_VERSION,
                "thresholds_json": dict(acceptance.thresholds),
                "evidence_json": evidence,
                "limitations": limitations,
                "actor": None,
                "actor_type": ActorType.MACHINE.value,
                "reviewed_intervals_json": [[int(item[0]), int(item[1])] for item in human_reviewed_intervals],
                "content_hash_at_decision": str(subject_hash),
                "source_qc_report_id": report_id,
                "policy_processor": POLICY_PROCESSOR_NAME,
                "status": "ACTIVE",
                "stale": False,
                "decided_at": utc_now_iso(),
            },
        )
        repo.update("explainer_qc_reports", report_id, {"policy_decision_id": decision["id"]})
        if subject_kind == QcSubjectKind.COMPOSITION_RENDER.value:
            render = repo.find("composition_renders", subject_revision_id)
            if render is not None and str(render.get("sha256") or "") == str(subject_hash):
                repo.update(
                    "composition_renders", subject_revision_id, {"machine_policy_decision_id": decision["id"]}
                )
        payload = acceptance.as_dict()
        payload.update(
            {
                "decision_id": decision["id"],
                "accepted": acceptance.accepted,
                "rule_version": POLICY_RULE_VERSION,
                "thresholds": dict(acceptance.thresholds),
                "evidence": evidence,
                "limitations": limitations,
                "blockers": [dict(item) for item in acceptance.blockers],
                "warnings": [dict(item) for item in acceptance.warnings],
                "workflow_effect": acceptance.workflow_effect,
                "human_approval_written": False,
                "policy_processor": POLICY_PROCESSOR_NAME,
                "subject_kind": str(subject_kind),
                "subject_revision_id": str(subject_revision_id),
                "subject_hash": str(subject_hash),
            }
        )
        return {
            "decision": payload,
            "row": decision,
            "workflow_effect": acceptance.workflow_effect,
            "human_approval_written": False,
            "accepted": acceptance.accepted,
            "unknown_guard": unknown_guard,
        }

    def record_human_decision(
        self,
        *,
        project_id: str,
        video_id: str,
        edition_id: str | None,
        subject_kind: str,
        subject_revision_id: str,
        subject_hash: str,
        decision_kind: str,
        actor: str,
        reviewed_intervals: Sequence[Sequence[int]] = (),
        note: str = "",
        current_hash: str | None = None,
    ) -> dict[str, Any]:
        """Record a real operator decision bound to the CURRENT content hash."""

        allowed = {
            DecisionKind.HUMAN_APPROVED.value,
            DecisionKind.REJECTED.value,
            DecisionKind.CHANGES_REQUESTED.value,
            DecisionKind.PUBLICATION_AUTHORIZED.value,
        }
        if str(decision_kind) not in allowed:
            raise ExplainerContractError(
                ExplainerErrorCode.INVALID_REQUEST.value,
                "人工决定只能是 HUMAN_APPROVED / REJECTED / CHANGES_REQUESTED / PUBLICATION_AUTHORIZED",
                {"decision_kind": str(decision_kind)},
            )
        ensure_policy_decision_allowed(
            decision_kind=str(decision_kind),
            actor_type=ActorType.HUMAN.value,
            actor=actor,
            policy_processor=None,
        )
        if not str(actor or "").strip():
            raise ExplainerContractError(
                ExplainerErrorCode.INVALID_REQUEST.value,
                "人工决定必须记录真实操作者",
                {"decision_kind": str(decision_kind)},
            )
        self._require_scope(project_id=project_id, video_id=video_id, edition_id=edition_id)
        self._require_hash(subject_hash, field_name="subject_hash")
        live_hash = current_hash or self._live_hash(
            subject_kind=subject_kind, subject_revision_id=subject_revision_id
        )
        if live_hash is None:
            raise ExplainerContractError(
                ExplainerErrorCode.INVALID_REQUEST.value,
                "无法解析该对象的当前内容哈希；调用方必须显式提供 current_hash",
                {"subject_kind": subject_kind, "subject_revision_id": subject_revision_id},
            )
        if str(live_hash) != str(subject_hash):
            raise ExplainerContractError(
                "STALE_REVISION",
                "对象内容已变化：调用方哈希与当前对象哈希不一致，人工决定基于旧版本无效",
                {
                    "subject_hash": str(subject_hash),
                    "current_hash": str(live_hash),
                    "subject_kind": str(subject_kind),
                    "subject_revision_id": str(subject_revision_id),
                },
            )
        intervals = [[int(item[0]), int(item[1])] for item in reviewed_intervals]
        limitations = LOCAL_OPERATOR_LIMITATION
        if str(decision_kind) == DecisionKind.PUBLICATION_AUTHORIZED.value:
            limitations += " 该发布授权只对本次记录的内容哈希有效，内容任何变化都会使其失效。"
        if note:
            limitations += f" 操作者备注：{note}"
        decision = self.repo.insert(
            "explainer_decisions",
            {
                "video_id": video_id,
                "project_id": project_id,
                "edition_id": edition_id,
                "decision_kind": str(decision_kind),
                "subject_kind": str(subject_kind),
                "subject_revision_id": str(subject_revision_id),
                "subject_hash": str(subject_hash),
                "rule_version": None,
                "thresholds_json": {},
                "evidence_json": {
                    "note": note,
                    "source": "LOCAL_OPERATOR_ACTION",
                    "authenticated_identity": False,
                    "reviewed_intervals": intervals,
                },
                "limitations": limitations,
                "actor": str(actor),
                "actor_type": ActorType.HUMAN.value,
                "reviewed_intervals_json": intervals,
                "content_hash_at_decision": str(live_hash),
                "source_qc_report_id": None,
                "policy_processor": None,
                "status": "ACTIVE",
                "stale": False,
                "decided_at": utc_now_iso(),
            },
        )
        if str(decision_kind) == DecisionKind.HUMAN_APPROVED.value and subject_kind == QcSubjectKind.COMPOSITION_RENDER.value:
            render = self.repo.find("composition_renders", subject_revision_id)
            if render is not None:
                self.repo.update(
                    "composition_renders", subject_revision_id, {"human_approval_id": decision["id"]}
                )
        return {
            "decision": decision,
            "decision_kind": str(decision_kind),
            "actor": str(actor),
            "actor_type": ActorType.HUMAN.value,
            "content_hash_at_decision": str(live_hash),
            "reviewed_intervals": intervals,
            "limitations": limitations,
            "authenticated_identity": False,
            "machine_decision_written": False,
            "legacy_episode_render_approval_untouched": True,
        }

    def _live_hash(self, *, subject_kind: str, subject_revision_id: str) -> str | None:
        """Resolve the current content hash of a subject from its stored row."""

        candidates: dict[str, tuple[str, str]] = {
            QcSubjectKind.COMPOSITION_RENDER.value: ("composition_renders", "sha256"),
            QcSubjectKind.COMPOSITION_REVISION.value: ("composition_revisions", "manifest_hash"),
            QcSubjectKind.SUBTITLE_REVISION.value: ("explainer_subtitle_revisions", "content_hash"),
        }
        target = candidates.get(str(subject_kind))
        if target is None:
            return None
        table, column = target
        row = self.repo.find(table, subject_revision_id)
        if row is None:
            return None
        value = row.get(column)
        return str(value) if value else None

    def decision_reuse(
        self, *, subject_kind: str, subject_revision_id: str, subject_hash: str
    ) -> dict[str, Any]:
        """Which active decisions may be reused for this exact hash, and which are stale."""

        self._require_hash(subject_hash, field_name="subject_hash")
        active = self.repo.active_decisions(
            subject_kind=str(subject_kind),
            subject_revision_id=str(subject_revision_id),
            subject_hash=str(subject_hash),
        )
        reusable: list[dict[str, Any]] = []
        rule_blocked: list[dict[str, Any]] = []
        for row in active:
            record = _decision_summary(row)
            if row["decision_kind"] == DecisionKind.POLICY_ACCEPTED.value:
                if str(row.get("rule_version") or "") == POLICY_RULE_VERSION:
                    record["reuse_reason"] = "SAME_RULE_VERSION_AND_UNCHANGED_HASH"
                    reusable.append(record)
                else:
                    record["reuse_reason"] = "RULE_VERSION_CHANGED_RECHECK_REQUIRED"
                    rule_blocked.append(record)
            else:
                record["reuse_reason"] = "HUMAN_DECISION_BOUND_TO_UNCHANGED_HASH"
                reusable.append(record)
        stale_rows = self.repo.query_all(
            """
            SELECT * FROM explainer_decisions
            WHERE subject_kind = ? AND subject_revision_id = ?
              AND (stale = 1 OR subject_hash <> ? OR status <> 'ACTIVE')
            ORDER BY decided_at DESC
            """,
            (str(subject_kind), str(subject_revision_id), str(subject_hash)),
        )
        stale = [_decision_summary(dict(row)) for row in stale_rows]
        return {
            "subject_kind": str(subject_kind),
            "subject_revision_id": str(subject_revision_id),
            "subject_hash": str(subject_hash),
            "reusable": reusable,
            "stale": stale,
            "rule_blocked": rule_blocked,
            "current_rule_version": POLICY_RULE_VERSION,
            "rule_version_changed": bool(rule_blocked),
            "human_approval_reusable": any(
                item["decision_kind"] == DecisionKind.HUMAN_APPROVED.value for item in reusable
            ),
            "publication_authorization_reusable": any(
                item["decision_kind"] == DecisionKind.PUBLICATION_AUTHORIZED.value for item in reusable
            ),
            "old_reports_rewritten": False,
            "reuse_scope_note": (
                "只有哈希未变、且规则版本仍为当前版本的机器判定可复用；"
                "新规则版本不会改写旧报告，只会要求重新检查。"
            ),
        }

    # ------------------------------------------------------------------ issue bookkeeping
    def issue_report(self, *, report_id: str) -> dict[str, Any]:
        report = self.repo.get("explainer_qc_reports", report_id)
        issues = self.repo.issues(report_id)
        by_severity: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for issue in issues:
            by_severity[issue["severity"]] = by_severity.get(issue["severity"], 0) + 1
            by_status[issue["status"]] = by_status.get(issue["status"], 0) + 1
        open_issues = [issue for issue in issues if issue["status"] in {"OPEN", "FIXING"}]
        unknown_issues = [
            issue
            for issue in open_issues
            if issue["severity"] == Severity.UNKNOWN.value or issue.get("unknown_reason")
        ]
        return {
            "report": report,
            "issues": issues,
            "issue_count": len(issues),
            "open_issue_count": len(open_issues),
            "by_severity": by_severity,
            "by_status": by_status,
            "hard_blocker_count": sum(
                1 for issue in open_issues if issue["severity"] == Severity.BLOCKER.value
            ),
            "unknown_issue_count": len(unknown_issues),
            "unverified_checks": list(report.get("unverified_checks_json") or []),
            "blocking": any(issue["severity"] == Severity.BLOCKER.value for issue in open_issues),
            "coverage": report.get("coverage_json") or {},
            "coverage_layers_are_separate": True,
        }

    def close_issue(
        self,
        *,
        issue_id: str,
        decision_id: str | None = None,
        evidence: Mapping[str, Any] | str | None,
        status: str = IssueStatus.CLOSED.value,
    ) -> dict[str, Any]:
        """Close an issue with real evidence; ``ACCEPTED_AS_IS`` needs a human decision."""

        issue = self.repo.get("explainer_qc_issues", issue_id)
        if str(status) not in {IssueStatus.CLOSED.value, IssueStatus.ACCEPTED_AS_IS.value}:
            raise ExplainerContractError(
                ExplainerErrorCode.INVALID_REQUEST.value,
                "只能把问题关闭为 CLOSED 或 ACCEPTED_AS_IS",
                {"status": str(status)},
            )
        if not evidence:
            raise ExplainerContractError(
                ExplainerErrorCode.INVALID_REQUEST.value,
                "关闭问题必须提供实际证据（修复结果、复检报告或人工判定依据）",
                {"issue_id": issue_id},
            )
        decision: dict[str, Any] | None = None
        if decision_id:
            decision = self.repo.get("explainer_decisions", decision_id)
            source_report_id = decision.get("source_qc_report_id")
            if source_report_id is not None and str(source_report_id) != str(issue["report_id"]):
                raise ExplainerContractError(
                    ExplainerErrorCode.INVALID_REQUEST.value,
                    "该决定来自另一份 QC 报告，不能用于关闭本问题",
                    {
                        "decision_id": decision_id,
                        "decision_report_id": source_report_id,
                        "issue_report_id": issue["report_id"],
                    },
                )
        if str(status) == IssueStatus.ACCEPTED_AS_IS.value:
            if decision is None:
                raise ExplainerContractError(
                    ExplainerErrorCode.INVALID_REQUEST.value,
                    "ACCEPTED_AS_IS 必须引用一条真实的人工决定",
                    {"issue_id": issue_id},
                )
            if str(decision.get("actor_type")) != ActorType.HUMAN.value:
                raise ExplainerContractError(
                    ExplainerErrorCode.INVALID_REQUEST.value,
                    "只有人工决定才能把问题标记为 ACCEPTED_AS_IS；机器判定不能放行",
                    {"decision_id": decision_id, "actor_type": decision.get("actor_type")},
                )
        if is_hard_blocker(str(issue["issue_kind"])) or str(issue["severity"]) == Severity.BLOCKER.value:
            if decision is not None and str(decision.get("actor_type")) == ActorType.MACHINE.value:
                raise ExplainerContractError(
                    ExplainerErrorCode.INVALID_REQUEST.value,
                    "硬阻塞问题不能被机器判定关闭；必须由人工决定或真实修复证据关闭",
                    {"issue_id": issue_id, "decision_id": decision_id},
                )
        evidence_payload: dict[str, Any] = (
            dict(evidence) if isinstance(evidence, Mapping) else {"note": str(evidence)}
        )
        evidence_payload.setdefault("recorded_at", utc_now_iso())
        if decision is not None:
            evidence_payload["decision_id"] = decision["id"]
            evidence_payload["decision_kind"] = decision["decision_kind"]
            evidence_payload["actor_type"] = decision["actor_type"]
        updated = self.repo.update(
            "explainer_qc_issues",
            issue_id,
            {
                "status": str(status),
                "closed_by_decision_id": decision["id"] if decision else None,
                "closed_evidence_json": evidence_payload,
            },
        )
        return {
            "issue": updated,
            "closed": True,
            "status": str(status),
            "evidence_recorded": evidence_payload,
            "decision_id": decision["id"] if decision else None,
            "hard_blocker": is_hard_blocker(str(issue["issue_kind"]))
            or str(issue["severity"]) == Severity.BLOCKER.value,
            "recheck_downstream_required": True,
        }

    # ------------------------------------------------------------------ defect metrics
    def defect_metrics(
        self,
        *,
        detected_issue_kinds: Sequence[Any],
        injected_truth: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """TP/FP/FN per injected defect with explicit denominators — never one score."""

        detected: list[dict[str, Any]] = []
        for entry in detected_issue_kinds or ():
            if isinstance(entry, Mapping):
                detected.append(
                    {
                        "issue_kind": str(entry.get("issue_kind") or entry.get("kind") or ""),
                        "severity": str(entry.get("severity") or ""),
                        "is_unknown": bool(
                            entry.get("is_unknown")
                            or str(entry.get("severity") or "") == Severity.UNKNOWN.value
                            or entry.get("unknown_reason")
                        ),
                        "issue_id": entry.get("issue_id"),
                    }
                )
            else:
                detected.append({"issue_kind": str(entry), "severity": "", "is_unknown": False, "issue_id": None})
        detected_kinds = [item["issue_kind"] for item in detected if item["issue_kind"]]

        truth: list[dict[str, Any]] = []
        for entry in injected_truth or ():
            if not isinstance(entry, Mapping):
                raise ExplainerContractError("SCHEMA_INVALID", "injected_truth 的每一项必须是对象", {})
            kind = str(entry.get("issue_kind") or entry.get("defect_kind") or "NONE")
            truth.append(
                {
                    "defect_id": str(entry.get("defect_id") or entry.get("id") or f"defect-{len(truth) + 1}"),
                    "issue_kind": kind,
                    "is_control": kind.upper() in {"NONE", "NO_DEFECT", "CLEAN"},
                }
            )
        truth_kinds = {item["issue_kind"] for item in truth if not item["is_control"]}

        per_defect: list[dict[str, Any]] = []
        true_positives = 0
        false_negatives = 0
        unknown_matches = 0
        for item in truth:
            if item["is_control"]:
                per_defect.append(
                    {
                        "defect_id": item["defect_id"],
                        "injected_issue_kind": item["issue_kind"],
                        "classification": "CONTROL_NO_DEFECT",
                        "true_positive": None,
                        "false_negative": None,
                        "unknown_detection": False,
                    }
                )
                continue
            matches = [entry for entry in detected if entry["issue_kind"] == item["issue_kind"]]
            unknown_matches_here = [entry for entry in matches if entry["is_unknown"]]
            is_tp = bool(matches)
            is_unknown = bool(matches) and len(unknown_matches_here) == len(matches)
            true_positives += 1 if is_tp else 0
            false_negatives += 0 if is_tp else 1
            unknown_matches += 1 if is_unknown else 0
            per_defect.append(
                {
                    "defect_id": item["defect_id"],
                    "injected_issue_kind": item["issue_kind"],
                    "classification": (
                        "UNKNOWN_DETECTION" if is_unknown else "TRUE_POSITIVE" if is_tp else "FALSE_NEGATIVE"
                    ),
                    "true_positive": bool(is_tp),
                    "false_negative": not is_tp,
                    "unknown_detection": is_unknown,
                    "match_count": len(matches),
                }
            )
        controls = [item for item in truth if item["is_control"]]
        false_positives = [entry for entry in detected if entry["issue_kind"] not in truth_kinds]
        unknown_detected_total = sum(1 for entry in detected if entry["is_unknown"])
        positive_denominator = len(truth) - len(controls)
        precision_denominator = len(detected)
        return {
            "per_defect": per_defect,
            "totals": {
                "injected_defect_count": positive_denominator,
                "control_count": len(controls),
                "detected_issue_count": len(detected),
                "true_positives": true_positives,
                "false_negatives": false_negatives,
                "false_positives": len(false_positives),
                "unknown_detections": unknown_detected_total,
            },
            "false_positive_kinds": sorted({entry["issue_kind"] for entry in false_positives}),
            "unknown_detections_matching_injected_defects": unknown_matches,
            "denominators": {
                "recall": positive_denominator,
                "precision": precision_denominator,
                "false_positive_rate": len(controls),
            },
            "recall": {
                "value": round(true_positives / positive_denominator, 6) if positive_denominator else None,
                "numerator": true_positives,
                "denominator": positive_denominator,
                "undefined_reason": None if positive_denominator else "NO_INJECTED_POSITIVE_SAMPLE",
            },
            "precision": {
                "value": round(true_positives / precision_denominator, 6) if precision_denominator else None,
                "numerator": true_positives,
                "denominator": precision_denominator,
                "undefined_reason": None if precision_denominator else "NO_DETECTION_SAMPLE",
            },
            "sample_counts": {
                "injected": len(truth),
                "injected_positive": positive_denominator,
                "controls": len(controls),
                "detected": len(detected),
                "detected_distinct_kinds": len(set(detected_kinds)),
            },
            "unknown_detections_are_not_true_positives": True,
            "score_kind": "CONFUSION_MATRIX_PER_DEFECT",
            "single_score_deliberately_absent": True,
        }


# --------------------------------------------------------------------------- #
# module-level helpers
# --------------------------------------------------------------------------- #
def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return content_hash(value)[:16] if isinstance(value, (Mapping, list, tuple)) else str(value)


def _normalise_audio_gaps(values: Sequence[Any] | None) -> list[dict[str, int]]:
    gaps: list[dict[str, int]] = []
    for entry in values or ():
        if isinstance(entry, Mapping):
            start = _coerce_int(entry.get("start_ms", 0), field_name="audio_gaps_ms.start_ms")
            if "end_ms" in entry:
                end = _coerce_int(entry.get("end_ms"), field_name="audio_gaps_ms.end_ms")
            else:
                end = start + _coerce_int(entry.get("duration_ms", 0), field_name="audio_gaps_ms.duration_ms")
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            start = _coerce_int(entry[0], field_name="audio_gaps_ms[0]")
            end = _coerce_int(entry[1], field_name="audio_gaps_ms[1]")
        else:
            start = 0
            end = _coerce_int(entry, field_name="audio_gaps_ms")
        if end < start:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "audio_gaps_ms 的区间必须满足 end >= start", {"start_ms": start, "end_ms": end}
            )
        gaps.append({"start_ms": start, "end_ms": end, "duration_ms": end - start})
    return sorted(gaps, key=lambda item: (-item["duration_ms"], item["start_ms"]))


def _read_safe_area(safe_area: Mapping[str, Any]) -> tuple[int, int, int, int]:
    if not isinstance(safe_area, Mapping):
        raise ExplainerContractError("SCHEMA_INVALID", "safe_area 必须是对象", {})
    if {"left_px", "top_px", "right_px", "bottom_px"} <= set(safe_area):
        left = _coerce_int(safe_area["left_px"], field_name="safe_area.left_px")
        top = _coerce_int(safe_area["top_px"], field_name="safe_area.top_px")
        right = _coerce_int(safe_area["right_px"], field_name="safe_area.right_px")
        bottom = _coerce_int(safe_area["bottom_px"], field_name="safe_area.bottom_px")
    elif {"x", "y", "width", "height"} <= set(safe_area):
        x = _coerce_int(safe_area["x"], field_name="safe_area.x")
        y = _coerce_int(safe_area["y"], field_name="safe_area.y")
        left = x
        top = y
        right = x + _coerce_int(safe_area["width"], field_name="safe_area.width")
        bottom = y + _coerce_int(safe_area["height"], field_name="safe_area.height")
    else:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "safe_area 必须提供 left_px/top_px/right_px/bottom_px 或 x/y/width/height",
            {"safe_area": dict(safe_area)},
        )
    if right <= left or bottom <= top:
        raise ExplainerContractError(
            "SCHEMA_INVALID", "safe_area 必须是正矩形", {"safe_area": [left, top, right, bottom]}
        )
    return (left, top, right, bottom)


def _cue_box(cue: Mapping[str, Any]) -> dict[str, int] | None:
    box = cue.get("box")
    if isinstance(box, Mapping) and {"x", "y", "width", "height"} <= set(box):
        return {
            "x": _coerce_int(box["x"], field_name="cues.box.x"),
            "y": _coerce_int(box["y"], field_name="cues.box.y"),
            "width": _coerce_int(box["width"], field_name="cues.box.width"),
            "height": _coerce_int(box["height"], field_name="cues.box.height"),
        }
    font_size = cue.get("font_size_px")
    if font_size is None:
        return None
    font = _coerce_int(font_size, field_name="cues.font_size_px")
    line_count = int(cue.get("line_count") or 1)
    text = str(cue.get("text") or "")
    lines = text.split("\n") if text else [""]
    longest = max((len(line.strip()) for line in lines), default=0)
    width = max(1, int(longest * font * 0.95))
    height = max(1, int(line_count * font * 1.35))
    x = cue.get("x")
    y = cue.get("y")
    return {
        "x": _coerce_int(x, field_name="cues.x") if x is not None else 0,
        "y": _coerce_int(y, field_name="cues.y") if y is not None else 0,
        "width": width,
        "height": height,
    }


def _box_outside(box: Mapping[str, int], rect: tuple[int, int, int, int]) -> list[str]:
    left, top, right, bottom = rect
    outside: list[str] = []
    if box["x"] < left:
        outside.append("LEFT")
    if box["y"] < top:
        outside.append("TOP")
    if box["x"] + box["width"] > right:
        outside.append("RIGHT")
    if box["y"] + box["height"] > bottom:
        outside.append("BOTTOM")
    return outside


def _extract_numbers(text: str) -> set[str]:
    numbers: set[str] = set()
    for match in _NUMBER_RE.findall(text or ""):
        numbers.add(match.replace(",", ""))
    for match in _CJK_NUMBER_RE.findall(text or ""):
        numbers.add(match)
    return numbers


def _issue_fact(row: Mapping[str, Any]) -> IssueFact:
    return IssueFact(
        issue_kind=str(row.get("issue_kind") or ""),
        severity=str(row.get("severity") or Severity.UNKNOWN.value),
        status=str(row.get("status") or IssueStatus.OPEN.value),
        detector=str(row.get("detector") or ""),
        confidence=row.get("confidence"),
        unknown_reason=row.get("unknown_reason"),
        scope=str((row.get("scope_json") or {}).get("layer") or ""),
    )


def _human_reviewed_frames(intervals: Sequence[Sequence[int]]) -> int:
    total = 0
    for item in intervals or ():
        start, end = int(item[0]), int(item[1])
        if end > start:
            total += end - start
    return total


def _apply_unknown_guard(
    acceptance: MachineAcceptance, issues: Sequence[IssueFact]
) -> tuple[MachineAcceptance, list[dict[str, Any]]]:
    """UNKNOWN open results must never be converted into a machine pass."""

    unknown_open = [
        issue
        for issue in issues
        if issue.is_open
        and (issue.severity == Severity.UNKNOWN.value or bool(issue.unknown_reason))
        and issue.severity != Severity.BLOCKER.value
    ]
    if not unknown_open or not acceptance.accepted:
        return acceptance, []
    guard = [
        {
            "issue_kind": issue.issue_kind,
            "severity": issue.severity,
            "detector": issue.detector,
            "unknown_reason": issue.unknown_reason,
            "treatment": "UNKNOWN_IS_NOT_A_PASS",
        }
        for issue in unknown_open
    ]
    guarded = replace(
        acceptance,
        accepted=False,
        workflow_effect="REQUEST_HUMAN",
        blockers=tuple(acceptance.blockers)
        + tuple(
            {
                "issue_kind": item["issue_kind"],
                "severity": Severity.UNKNOWN.value,
                "reason": "UNKNOWN_RESULT_REQUIRES_HUMAN",
            }
            for item in guard
        ),
    )
    return guarded, guard


def _decision_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "decision_id": row.get("id"),
        "decision_kind": row.get("decision_kind"),
        "actor": row.get("actor"),
        "actor_type": row.get("actor_type"),
        "rule_version": row.get("rule_version"),
        "policy_processor": row.get("policy_processor"),
        "status": row.get("status"),
        "stale": bool(row.get("stale")),
        "subject_hash": row.get("subject_hash"),
        "content_hash_at_decision": row.get("content_hash_at_decision"),
        "decided_at": row.get("decided_at"),
        "reviewed_intervals": list(row.get("reviewed_intervals_json") or []),
        "limitations": row.get("limitations"),
    }


__all__ = [
    "DENSITY_MULTIPLIERS",
    "DEPTH_DETECTOR",
    "ExplainerQualityService",
    "FACT_DETECTOR",
    "LAYER_DEPTH",
    "LAYER_FACT",
    "LAYER_SEMANTIC",
    "LAYER_SUBTITLE",
    "LAYER_TECHNICAL",
    "LOCAL_OPERATOR_LIMITATION",
    "SEMANTIC_DETECTOR",
    "SUBTITLE_DETECTOR",
    "TECHNICAL_DETECTOR",
    "TechnicalCheckInput",
    "VisualQcProvider",
    "responsible_step_for",
]
