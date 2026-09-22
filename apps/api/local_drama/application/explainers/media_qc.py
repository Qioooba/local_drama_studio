"""Real ffmpeg/ffprobe measurement ports for the explainer technical QC layer.

The design requires the technical layer to be a *full decode* of the whole film:
"decoded 100% of frames and found no anomalies" is a different claim from "the
metadata says it should be fine", and only the first one may ever be reported as
PASS.  This module produces those numbers from real local tools:

* ``ffprobe -count_frames`` gives the authoritative frame count;
* one ``ffmpeg -f null -`` pass decodes every frame and reports decode errors.

Nothing here invents a measurement: if the tools are missing, the file cannot be
resolved, or the decode does not complete inside its budget, the reader returns a
partial measurement and the report stays incomplete/blocked instead of passing.

The frame sampler follows the same rule: it extracts exactly the frames named by
the deterministic sampling plan, and a frame it could not extract is simply absent
from the result, which the semantic layer then reports as unchecked.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import ExplainerContractError

#: ``ffprobe`` exit status is 0/1; ``ffmpeg`` uses 1 for a decode error too, so the
#: error text is the signal and an exit status alone is never treated as success.
_DECODE_ERROR_PATTERN = re.compile(
    r"(Invalid data found|Error while decoding|corrupt|decode_slice_header error|"
    r"no frame|Packet corrupt|error reading header|Truncating packet)",
    re.IGNORECASE,
)
_FRAME_PROGRESS_PATTERN = re.compile(r"^frame=(\d+)$", re.MULTILINE)


class DecodeVerification:
    """The outcome of one full-decode verification pass."""

    def __init__(
        self,
        *,
        total_frames: int,
        decoded_frames: int,
        frame_rate: tuple[int, int] | None,
        decode_error_count: int,
        error_examples: Sequence[str],
        stderr_tail: str,
        completed: bool,
        failure_reason: str | None,
    ) -> None:
        self.total_frames = total_frames
        self.decoded_frames = decoded_frames
        self.frame_rate = frame_rate
        self.decode_error_count = decode_error_count
        self.error_examples = tuple(error_examples)
        self.stderr_tail = stderr_tail
        self.completed = completed
        self.failure_reason = failure_reason

    @property
    def decode_complete(self) -> bool:
        return bool(
            self.completed
            and self.total_frames > 0
            and self.decoded_frames >= self.total_frames
            and self.decode_error_count == 0
        )

    def as_technical_input(self, *, rel_path: str, media_version_id: str | None) -> dict[str, Any]:
        """The technical-layer mapping; frame errors are reported as one interval.

        The verifier knows how many frames failed and which run of stderr carried
        the messages, but not which *frame numbers* failed, so it declares the whole
        film as the affected interval rather than inventing a narrower one.  That
        over-reports the range on purpose: a blocker that points at the whole film
        is honest, a fabricated ``[1234, 1300)`` would not be.
        """

        payload: dict[str, Any] = {
            "total_frames": self.total_frames,
            "decoded_frames": self.decoded_frames,
            "decode_errors": [[0, self.total_frames]] if self.decode_error_count else [],
            "source_clips": [
                {
                    "media_version_id": media_version_id,
                    "rel_path": rel_path,
                    "total_frames": self.total_frames,
                    "decoded_frames": self.decoded_frames,
                    "decode_errors": list(self.error_examples),
                }
            ],
            "decode_verification": {
                "completed": self.completed,
                "failure_reason": self.failure_reason,
                "decode_error_count": self.decode_error_count,
                "error_examples": list(self.error_examples),
                "stderr_tail": self.stderr_tail,
                "frame_count_source": "ffprobe -count_frames",
                "decode_pass": "ffmpeg -f null -",
            },
        }
        if self.frame_rate is not None:
            payload["fps_num"], payload["fps_den"] = self.frame_rate
        return payload


def parse_frame_rate(value: Any) -> tuple[int, int] | None:
    """Parse ffprobe's ``num/den`` frame-rate string into an exact rational."""

    text = str(value or "").strip()
    if not text:
        return None
    if "/" in text:
        num_text, _, den_text = text.partition("/")
        try:
            num = int(num_text)
            den = int(den_text)
        except ValueError:
            return None
    else:
        try:
            num = int(round(float(text) * 1000))
            den = 1000
        except ValueError:
            return None
    if num <= 0 or den <= 0:
        return None
    divisor = _gcd(num, den)
    return (num // divisor, den // divisor)


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a or 1


def _run(command: Sequence[str], *, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - command list is built here from settings paths
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )


class FfmpegDecodeVerifier:
    """Full-decode verification over the local ``ffmpeg``/``ffprobe`` binaries."""

    def __init__(
        self,
        *,
        ffmpeg_path: str | None,
        ffprobe_path: str | None,
        timeout_seconds: float = 3600.0,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.timeout_seconds = float(timeout_seconds)

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg_path) and bool(self.ffprobe_path)

    def _probe(self, path: Path) -> dict[str, Any]:
        if not self.ffprobe_path:
            raise DomainRuleError("DECODE_TOOL_UNAVAILABLE", "本机没有可用的 ffprobe，无法统计帧数")
        result = _run(
            [
                self.ffprobe_path,
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=nb_read_frames,avg_frame_rate,r_frame_rate,nb_frames,width,height",
                "-of",
                "json",
                str(path),
            ],
            timeout_seconds=min(self.timeout_seconds, 900.0),
        )
        if result.returncode != 0:
            raise DomainRuleError(
                "DECODE_PROBE_FAILED",
                "ffprobe 无法读取该媒体文件的视频流",
                {"stderr_tail": (result.stderr or "")[-2000:], "returncode": result.returncode},
            )
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as error:
            raise DomainRuleError("DECODE_PROBE_FAILED", "ffprobe 输出不是合法 JSON") from error
        streams = payload.get("streams") or []
        return dict(streams[0]) if streams else {}

    def verify(self, path: Path) -> DecodeVerification:
        """Decode every frame of ``path`` and report what was actually decoded."""

        if not self.available:
            raise DomainRuleError(
                "DECODE_TOOL_UNAVAILABLE",
                "本机没有配置 ffmpeg/ffprobe，无法完成全片解码验证",
                {"ffmpeg": self.ffmpeg_path, "ffprobe": self.ffprobe_path},
            )
        if not path.is_file():
            raise ExplainerContractError(
                "NOT_FOUND", "待质检的媒体文件不存在", {"path": str(path)}
            )
        stream = self._probe(path)
        total_frames = _coerce_int(stream.get("nb_read_frames"), default=0)
        if total_frames <= 0:
            total_frames = _coerce_int(stream.get("nb_frames"), default=0)
        frame_rate = parse_frame_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))

        command = [
            str(self.ffmpeg_path),
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-progress",
            "pipe:1",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-f",
            "null",
            "-",
        ]
        try:
            result = _run(command, timeout_seconds=self.timeout_seconds)
        except subprocess.TimeoutExpired as error:
            # A decode that did not finish cannot be called complete.
            return DecodeVerification(
                total_frames=total_frames,
                decoded_frames=0,
                frame_rate=frame_rate,
                decode_error_count=0,
                error_examples=(),
                stderr_tail=str(error)[:2000],
                completed=False,
                failure_reason="DECODE_TIMEOUT",
            )
        stderr = result.stderr or ""
        decoded_frames = _last_progress_frame(result.stdout or "")
        # The pattern alternation can match the same message twice, so the count is
        # the number of distinct failing lines: the number of frames that failed is
        # not recoverable from stderr, but the number of reported failures is.
        error_lines = _context_lines(stderr)
        if decoded_frames == 0 and result.returncode == 0 and total_frames:
            # ``-progress`` is written to stdout; when it is unavailable the exit
            # status of a null-decode over the whole file is the only evidence, so
            # the frame count stays unknown rather than being assumed equal.
            decoded_frames = 0
        completed = result.returncode == 0
        return DecodeVerification(
            total_frames=total_frames,
            decoded_frames=decoded_frames,
            frame_rate=frame_rate,
            decode_error_count=len(error_lines),
            error_examples=tuple(error_lines[:10]),
            stderr_tail=stderr[-2000:],
            completed=completed,
            failure_reason=None if completed else "DECODE_EXIT_STATUS_NONZERO",
        )


def _last_progress_frame(stdout: str) -> int:
    matches = _FRAME_PROGRESS_PATTERN.findall(stdout or "")
    return max((int(item) for item in matches), default=0)


def _context_lines(stderr: str) -> list[str]:
    """The distinct stderr lines that actually mention a decode failure."""

    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    hits = [line for line in lines if _DECODE_ERROR_PATTERN.search(line)]
    return list(dict.fromkeys(hits))


def _coerce_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class FfmpegFrameSampler:
    """Extract exactly the frames a deterministic sampling plan names.

    Frames are written under ``frame_root/<namespace>/`` with a zero-padded name so
    the file for a given frame id is stable across runs.  A frame whose extraction
    fails is omitted; the semantic layer then records it as unchecked, which is the
    only honest outcome for a frame nobody looked at.
    """

    def __init__(
        self,
        *,
        ffmpeg_path: str | None,
        frame_root: Path,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.frame_root = frame_root
        self.timeout_seconds = float(timeout_seconds)

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg_path)

    def extract(
        self,
        *,
        source: Path,
        frame_ids: Sequence[int],
        frame_rate: tuple[int, int],
        namespace: str,
    ) -> dict[int, str]:
        """Return ``frame_id -> work-root-relative path`` for the frames extracted."""

        if not self.available:
            raise DomainRuleError("DECODE_TOOL_UNAVAILABLE", "本机没有配置 ffmpeg，无法抽取质检帧")
        num, den = frame_rate
        if num <= 0 or den <= 0:
            raise ExplainerContractError("SCHEMA_INVALID", "帧率必须为正", {"fps_num": num, "fps_den": den})
        target_directory = self.frame_root / _safe_namespace(namespace)
        target_directory.mkdir(parents=True, exist_ok=True)
        extracted: dict[int, str] = {}
        for frame_id in sorted({int(item) for item in frame_ids}):
            if frame_id < 0:
                continue
            # Frame id -> presentation time is exact in the rational frame rate.
            seconds = frame_id * den / num
            destination = target_directory / f"frame_{frame_id:09d}.png"
            if destination.is_file() and destination.stat().st_size > 0:
                extracted[frame_id] = destination.relative_to(self.frame_root).as_posix()
                continue
            command = [
                str(self.ffmpeg_path),
                "-nostdin",
                "-hide_banner",
                "-v",
                "error",
                "-ss",
                f"{seconds:.6f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-y",
                str(destination),
            ]
            try:
                result = _run(command, timeout_seconds=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                continue
            if result.returncode == 0 and destination.is_file() and destination.stat().st_size > 0:
                extracted[frame_id] = destination.relative_to(self.frame_root).as_posix()
        return extracted


def _safe_namespace(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "default"))
    return cleaned[:120] or "default"


#: ``task_code`` values the media readers accept as a sampling namespace.
FramePathResolver = Callable[[Mapping[str, Any]], Path | None]


def beat_frame_ranges_from_items(items: Sequence[Mapping[str, Any]]) -> dict[str, list[int]]:
    """Per-beat frame ranges from the frozen composition items.

    The ranges come from the stored composition, not from a fresh guess, so the
    sampling plan and the film it describes stay bound to the same revision.
    """

    ranges: dict[str, list[int]] = {}
    for item in items:
        beat_id = str(item.get("beat_id") or "")
        if not beat_id:
            continue
        try:
            start = int(item.get("start_frame") or 0)
            end = int(item.get("end_frame_exclusive") or 0)
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        current = ranges.get(beat_id)
        ranges[beat_id] = [start, end] if current is None else [min(current[0], start), max(current[1], end)]
    return ranges


def _resolve_media_version(
    repo: Any, *, project_id: str, context: Mapping[str, Any], content_path: Callable[[str], Path]
) -> tuple[str, Path]:
    """Locate the file the technical layer should decode.

    Preference order is frozen-revision first: an explicit ``media_version_id``,
    then the render named by ``subject_revision_id``, then the edition's newest
    verified root render.  Only when no render exists yet does it fall back to the
    adopted per-beat media candidates.  Every hit is re-checked against the project
    through ``require_same_project_media``, and the file path comes from the media
    service rather than from a stored string, so a QC pass can never read a file
    outside the project's own media tree.
    """

    def resolve(media_version_id: str) -> tuple[str, Path]:
        record = repo.require_same_project_media(project_id=project_id, media_version_id=media_version_id)
        return str(record["media_version_id"]), content_path(str(record["media_version_id"]))

    declared = str(context.get("media_version_id") or "")
    if declared:
        return resolve(declared)

    subject_kind = str(context.get("subject_kind") or "").upper()
    subject_revision_id = str(context.get("subject_revision_id") or "")
    if subject_kind == "COMPOSITION_RENDER" and subject_revision_id:
        render = repo.get("composition_renders", subject_revision_id)
        media_version_id = str(render.get("media_version_id") or "")
        if media_version_id:
            return resolve(media_version_id)

    edition_id = str(context.get("edition_id") or "")
    if edition_id:
        render = repo.current_root_render(edition_id)
        media_version_id = str((render or {}).get("media_version_id") or "")
        if media_version_id:
            return resolve(media_version_id)

    video_id = str(context.get("video_id") or "")
    candidates = repo.list_where(
        "explainer_media_candidates",
        {"video_id": video_id},
        order_by="variant_no",
        descending=False,
    )
    adopted = [item for item in candidates if item.get("adopted") and item.get("media_version_id")]
    chosen = (adopted or [item for item in candidates if item.get("media_version_id")] or [None])[0]
    media_version_id = str((chosen or {}).get("media_version_id") or "")
    if not media_version_id:
        raise ExplainerContractError(
            "NOT_RUN",
            "还没有可解码的成片或素材候选，技术质检层保持未运行",
            {"video_id": video_id, "edition_id": edition_id or None},
        )
    return resolve(media_version_id)


def build_media_qc_readers(
    *,
    ffmpeg_path: str | None,
    ffprobe_path: str | None,
    frame_root: Path,
    content_path: Callable[[str], Path],
    decode_timeout_seconds: float = 3600.0,
) -> dict[str, Callable[..., Any]]:
    """Bind the technical and sampling readers to real local decoder tools.

    The technical reader always runs a real full decode.  The sampling reader only
    produces a plan when the frozen composition actually declares per-beat frame
    ranges: without them a sampling plan would have to invent which frames to look
    at, so it raises ``NOT_RUN`` and the semantic layer stays unchecked.
    """

    verifier = FfmpegDecodeVerifier(
        ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path, timeout_seconds=decode_timeout_seconds
    )
    sampler = FfmpegFrameSampler(ffmpeg_path=ffmpeg_path, frame_root=frame_root)

    def technical_reader(repo: Any, context: Mapping[str, Any]) -> dict[str, Any]:
        project_id = str(context.get("project_id") or "")
        media_version_id, path = _resolve_media_version(
            repo, project_id=project_id, context=context, content_path=content_path
        )
        verification = verifier.verify(path)
        return verification.as_technical_input(rel_path=str(path), media_version_id=media_version_id)

    def sampling_reader(repo: Any, context: Mapping[str, Any]) -> dict[str, Any]:
        frame_ranges = context.get("beat_frame_ranges")
        if isinstance(frame_ranges, Mapping) and frame_ranges:
            ranges = {str(key): [int(value[0]), int(value[1])] for key, value in frame_ranges.items()}
        else:
            ranges = beat_frame_ranges_from_items(_composition_items(repo, context))
        if not ranges:
            raise ExplainerContractError(
                "NOT_RUN",
                "合成清单还没有逐 beat 帧区间，抽样计划无法在不臆造帧号的前提下建立",
                {"video_id": context.get("video_id"), "edition_id": context.get("edition_id")},
            )
        frame_rate = _frame_rate_from_context(context, repo=repo)
        plan = _plan_from_ranges(
            video_id=str(context.get("video_id") or ""),
            edition_id=str(context.get("edition_id") or ""),
            ranges=ranges,
            total_frames=_total_frames_from_ranges(ranges, context),
            density=str(context.get("density") or "STANDARD"),
        )
        if not sampler.available:
            raise DomainRuleError("DECODE_TOOL_UNAVAILABLE", "本机没有配置 ffmpeg，无法抽取质检帧")
        media_version_id, path = _resolve_media_version(
            repo, project_id=str(context.get("project_id") or ""), context=context, content_path=content_path
        )
        extracted = sampler.extract(
            source=path,
            frame_ids=plan["sampled_frame_ids"],
            frame_rate=frame_rate,
            namespace=f"{context.get('edition_id') or context.get('video_id')}",
        )
        return {
            "plan": plan,
            "subject_hash": str(context.get("subject_hash") or ""),
            "reference_frames": [
                {"frame_id": frame_id, "rel_path": rel_path}
                for frame_id, rel_path in sorted(extracted.items())
            ],
            "reference_frame_count": len(extracted),
            "frames_not_extracted": sorted(set(plan["sampled_frame_ids"]) - set(extracted)),
            "questions": list(context.get("questions") or ()),
            "media_version_id": media_version_id,
            "frame_source": str(path),
        }

    return {"technical_reader": technical_reader, "sampling_reader": sampling_reader}


def _composition_items(repo: Any, context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The frozen composition items the sampling ranges are derived from."""

    edition_id = str(context.get("edition_id") or "")
    if not edition_id:
        return []
    revision_id = str(context.get("composition_revision_id") or "")
    if not revision_id:
        render = repo.current_root_render(edition_id)
        revision_id = str((render or {}).get("composition_revision_id") or "")
    if not revision_id:
        revisions = repo.list_where(
            "composition_revisions", {"edition_id": edition_id}, order_by="revision_no", descending=True, limit=1
        )
        revision_id = str(revisions[0]["id"]) if revisions else ""
    if not revision_id:
        return []
    return list(repo.composition_items(revision_id))


def _frame_rate_from_context(context: Mapping[str, Any], *, repo: Any = None) -> tuple[int, int]:
    try:
        num = int(context.get("fps_num") or 0)
        den = int(context.get("fps_den") or 0)
    except (TypeError, ValueError):
        num = den = 0
    if num > 0 and den > 0:
        return (num, den)
    edition_id = str(context.get("edition_id") or "")
    if repo is not None and edition_id:
        edition = repo.find("explainer_editions", edition_id) or {}
        try:
            num = int(edition.get("fps_num") or 0)
            den = int(edition.get("fps_den") or 0)
        except (TypeError, ValueError):
            num = den = 0
        if num > 0 and den > 0:
            return (num, den)
    return (25, 1)


def _total_frames_from_ranges(ranges: Mapping[str, Sequence[int]], context: Mapping[str, Any]) -> int:
    declared = context.get("total_frames")
    try:
        declared_frames = int(declared) if declared is not None else 0
    except (TypeError, ValueError):
        declared_frames = 0
    highest = max((int(value[1]) for value in ranges.values()), default=0)
    return max(declared_frames, highest)


def _plan_from_ranges(
    *,
    video_id: str,
    edition_id: str,
    ranges: Mapping[str, Sequence[int]],
    total_frames: int,
    density: str,
) -> dict[str, Any]:
    """Deterministic sampling plan over declared beat ranges.

    The same dyadic pattern the service's own planner uses, computed here so the
    plan is derivable from the frozen composition alone; the plan hash is computed
    exactly as ``run_semantic_check`` recomputes it.
    """

    from local_drama.domain.explainers.contracts import content_hash

    density_key = str(density or "STANDARD").upper()
    if density_key not in {"STANDARD", "DENSE"}:
        raise ExplainerContractError("INVALID_REQUEST", "density 只能是 STANDARD 或 DENSE", {"density": density})
    multiplier = 1 if density_key == "STANDARD" else 2
    if total_frames <= 0:
        raise ExplainerContractError(
            "INVALID_REQUEST", "抽样计划需要正片长", {"total_frames": total_frames}
        )
    sampled: set[int] = set()
    per_beat: dict[str, Any] = {}
    for beat_id, raw in ranges.items():
        start, end = int(raw[0]), int(raw[1])
        if end <= start:
            continue
        length = end - start
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
            "inside_flagged_anomaly": False,
        }
    sampled_frame_ids = sorted(frame for frame in sampled if 0 <= frame < total_frames)
    if not sampled_frame_ids:
        raise ExplainerContractError("INVALID_REQUEST", "抽样计划为空：没有任何可用帧", {})
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
        "sampled_intervals": [[frame, frame + 1] for frame in sampled_frame_ids],
        "sampling_ratio": round(len(sampled_frame_ids) / total_frames, 6) if total_frames else 0.0,
        "per_beat": per_beat,
        "density": density_key,
        "density_multiplier": multiplier,
        "plan_hash": plan_hash,
        "sampling_is_full_understanding": False,
        "sampling_purpose": "SEMANTIC_SAMPLE_OF_SELECTED_FRAMES_ONLY",
        "unverified_checks": [
            "SAMPLING_COVERS_SELECTED_FRAMES_ONLY",
            "SEMANTIC_SAMPLE_IS_NOT_FULL_FRAME_UNDERSTANDING",
        ],
    }
