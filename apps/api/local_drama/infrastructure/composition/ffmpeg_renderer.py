"""FFmpeg execution for a deterministic :class:`RenderManifest`.

This module turns an already-validated manifest into real FFmpeg process
invocations.  Every command is assembled from whitelisted structured fields, so
there is no code path — and no input field — through which an arbitrary shell
string can reach a process.  ``FfmpegCommand.to_argv()`` is the only way to get
an argument vector, and each element is a primitive appended by a builder.

The technical approach deliberately mirrors the proven drama renderer
(``local_drama.application.timeline``), without importing it:

* every source is normalised to one pixel format, one timebase, one frame rate
  and one audio sample rate before it is used;
* ``-c copy`` concatenation is chosen only when
  :func:`local_drama.application.composition.validation.concat_compatibility`
  proves codec, extradata, timebase, pixel format and audio parameters equal;
* an output is written to a temporary path, probed, fully decoded, hashed, and
  only then published with :func:`publish_atomically`;
* ``-shortest`` is never appended.  A missing narration, an under-length video
  or an overlong subtitle is a diagnosis, not something to hide.

What this module deliberately does NOT do:

* it never depends on Episode SQL and never reads "the latest" candidate — the
  manifest is the complete input, and a caller-supplied ``media_path_resolver``
  is the only way a path enters;
* it never spawns a process when a ``runner`` callable is injected, and it never
  fakes a probe: where ffprobe/ffmpeg is unavailable the answer is a structured
  ``UNAVAILABLE`` result;
* it never measures loudness itself.  ``-16 LUFS ±1`` and true peak ``≤ -1 dBTP``
  are *product defaults* (design §11.4), not a platform standard, and the
  loudness filter carries a ``note`` saying exactly that;
* it never trusts a container duration for length: frame counts come from the
  manifest and are validated after concatenation.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from local_drama.application.composition.manifest import (
    ManifestChunkSpec,
    ManifestClip,
    Ratio,
    RenderManifest,
)
from local_drama.application.composition.slices import compute_chunk_slices
from local_drama.application.composition.validation import concat_compatibility
from local_drama.domain.explainers.contracts import ExplainerContractError, content_hash

__all__ = [
    "AUDIO_BITRATE",
    "AUDIO_CHANNEL_LAYOUT",
    "AUDIO_CODEC",
    "AUDIO_SAMPLE_FMT",
    "DEFAULT_LOUDNESS_LRA",
    "DEFAULT_LOUDNESS_TARGET_LUFS",
    "DEFAULT_TRUE_PEAK_DBTP",
    "FfmpegCommand",
    "FfmpegFilterGraph",
    "FfmpegRunner",
    "KNOWN_FILTERS",
    "LOUDNESS_NOTE",
    "REASON_ARGUMENT_INVALID",
    "REASON_DISK_FULL",
    "REASON_FILTER_INVALID",
    "REASON_INPUT_MISSING",
    "REASON_NO_OUTPUT",
    "REASON_PROCESS_FAILED",
    "REASON_PROCESS_KILLED",
    "REASON_RESOURCE_EXHAUSTED",
    "REASON_TIMEOUT",
    "REASON_TOOL_MISSING",
    "REASON_USER_CANCELLED",
    "VIDEO_PIX_FMT",
    "VIDEO_TIMEBASE",
    "X264_CRF",
    "X264_PRESET",
    "atomic_render",
    "build_chunk_command",
    "build_concat_command",
    "build_loudness_measure_command",
    "build_loudness_normalise_command",
    "build_mix_command",
    "build_subtitle_burn_command",
    "classify_process_failure",
    "compute_chunk_slices",
    "decode_signature",
    "escape_concat_quote",
    "escape_drawtext_text",
    "escape_filter_value",
    "parse_loudness_measurement",
    "publish_atomically",
]

#: The one frozen encoder profile.  Chunk 0 and chunk N must be encoded with the
#: same settings, otherwise the concat step could not stream-copy at all.
X264_PRESET = "veryfast"
X264_CRF = 18
VIDEO_PIX_FMT = "yuv420p"
AUDIO_BITRATE = "192k"
AUDIO_CODEC = "aac"
AUDIO_SAMPLE_FMT = "fltp"
AUDIO_CHANNEL_LAYOUT = "stereo"
VIDEO_TIMEBASE = "AVTB"

#: Product loudness defaults (design §11.4).  NOT a platform standard.
DEFAULT_LOUDNESS_TARGET_LUFS = -16.0
DEFAULT_TRUE_PEAK_DBTP = -1.0
DEFAULT_LOUDNESS_LRA = 11.0

#: Head-room subtracted from the loudnorm true-peak target so the *delivered*
#: lossy encode satisfies the ceiling.  Measured on the real 406 s audited film:
#: loudnorm targeting -1.0 dBTP produced a delivered AAC true peak of -0.7 dBTP, a
#: 0.5 dB margin reached -0.9 dBTP, and 1.0 dB reached -1.6 dBTP with an integrated
#: loudness of -17.0 LUFS — inside the documented -16 +/-1 band on both counts.
ENCODE_TRUE_PEAK_MARGIN_DB = 1.0
LOUDNESS_NOTE = (
    "loudnorm 目标为产品默认值（-16 LUFS ±1、真峰值 ≤ -1 dBTP），"
    "不是任何平台的标准；平台档位必须由调用方显式覆盖"
)

#: Exit codes that mean "the machine ran out of something", not "the input is bad".
_RECOVERABLE_ERRNO = frozenset({28})  # ENOSPC
_RECOVERABLE_STDERR_MARKERS = (
    "no space left on device",
    "disk quota exceeded",
    "not enough space",
)

#: Reason codes for a failed process.  ``recoverable=True`` means "a technical
#: retry inside the declared budget may help"; a deterministic input, filter or
#: argument error is *not* recoverable, because repeating the same command
#: cannot change it.
REASON_DISK_FULL = "DISK_FULL"
REASON_TOOL_MISSING = "TOOL_MISSING"
REASON_INPUT_MISSING = "INPUT_MISSING"
REASON_PERMISSION_DENIED = "PERMISSION_DENIED"
REASON_FILTER_INVALID = "FILTER_INVALID"
REASON_ARGUMENT_INVALID = "ARGUMENT_INVALID"
REASON_RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
REASON_TIMEOUT = "TIMEOUT"
REASON_USER_CANCELLED = "USER_CANCELLED"
REASON_PROCESS_KILLED = "PROCESS_KILLED"
REASON_NO_OUTPUT = "NO_OUTPUT"
REASON_PROCESS_FAILED = "PROCESS_FAILED"

#: Permanent configuration errors.  Repeating the identical command cannot fix
#: them, so they must never be reported as "just retry".
_PERMANENT_STDERR_MARKERS: tuple[tuple[str, str], ...] = (
    ("no such file or directory", REASON_INPUT_MISSING),
    ("matches no streams", REASON_INPUT_MISSING),
    ("invalid data found when processing input", REASON_INPUT_MISSING),
    ("moov atom not found", REASON_INPUT_MISSING),
    ("does not contain any stream", REASON_INPUT_MISSING),
    ("no such filter", REASON_FILTER_INVALID),
    ("option not found", REASON_FILTER_INVALID),
    ("filter not found", REASON_FILTER_INVALID),
    ("has output", REASON_FILTER_INVALID),
    ("unconnected", REASON_FILTER_INVALID),
    ("invalid stream specifier", REASON_FILTER_INVALID),
    ("error binding filtergraph", REASON_FILTER_INVALID),
    ("error initializing filter", REASON_FILTER_INVALID),
    ("error reinitializing filters", REASON_FILTER_INVALID),
    ("error while opening encoder", REASON_ARGUMENT_INVALID),
    ("error while opening decoder", REASON_ARGUMENT_INVALID),
    ("unrecognized option", REASON_ARGUMENT_INVALID),
    ("invalid argument", REASON_ARGUMENT_INVALID),
    ("permission denied", REASON_PERMISSION_DENIED),
)

#: Resource pressure: retryable, but only after the caller has freed something.
_RESOURCE_STDERR_MARKERS: tuple[tuple[str, str], ...] = (
    ("cannot allocate memory", REASON_RESOURCE_EXHAUSTED),
    ("out of memory", REASON_RESOURCE_EXHAUSTED),
    ("resource temporarily unavailable", REASON_RESOURCE_EXHAUSTED),
)

#: A cooperative stop requested by the caller; it must never be retried silently.
_CANCEL_STDERR_MARKERS: tuple[str, ...] = (
    "received signal 2",
    "received signal 15",
    "exiting normally, received signal",
)

#: Default in-memory log tail.  The whole stream still goes to a log file when one
#: is configured, so bounding memory never loses the diagnosis.
DEFAULT_LOG_TAIL_BYTES = 64 * 1024

#: macOS/Linux signal numbers that mean "the user or the host stopped this".
_SIGINT = -2
_SIGTERM = -15


def _domain_error(code: str, message: str, details: Mapping[str, Any] | None = None) -> ExplainerContractError:
    return ExplainerContractError(code, message, details)


# --------------------------------------------------------------------------- #
# escaping and primitive validation
# --------------------------------------------------------------------------- #
def escape_filter_value(value: str) -> str:
    """Escape one value for a filtergraph argument, using single quoting.

    Backslash first, then the quote and the separator.  The result is wrapped in
    single quotes because FFmpeg's filtergraph parser treats a quoted run
    literally, which is the only way a Windows path (``C:\\dir``) or a path with
    a ``:`` survives ``subtitles=`` and ``drawtext=``.
    """

    text = str(value)
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\\'")
    text = text.replace(":", "\\:")
    return f"'{text}'"


def escape_drawtext_text(value: str) -> str:
    """Escape text for ``drawtext=text='...'``.

    Drawtext expands its value a second time, so a bare ``%`` would be read as a
    strftime sequence and a newline would break the graph.  Both are made
    literal here; single quotes and separators go through the ordinary filter
    escaping.
    """

    text = str(value).replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    text = text.replace("%", "\\%")
    return escape_filter_value(text)


def escape_concat_quote(value: str) -> str:
    """Escape a path for the single-quoted ``file '...'`` demuxer syntax."""

    text = str(value).replace("\\", "/")
    text = text.replace("'", "'\\''")
    return f"'{text}'"


def _fmt_number(value: float) -> str:
    """Format a derived duration without scientific notation."""

    text = f"{float(value):.6f}"
    if text.endswith("0" * 6):
        text = f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"
    return text


def _validate_primitive(value: Any, *, field_name: str) -> str:
    if not isinstance(value, (str, int, float)):
        raise _domain_error(
            "SCHEMA_INVALID",
            f"FFmpeg 参数 {field_name} 只能是字符串或数字",
            {"field": field_name, "type": type(value).__name__},
        )
    text = str(value)
    if "\x00" in text or "\n" in text or "\r" in text:
        raise _domain_error(
            "SCHEMA_INVALID",
            f"FFmpeg 参数 {field_name} 不能包含 NUL 或换行",
            {"field": field_name},
        )
    return text


# --------------------------------------------------------------------------- #
# filtergraph builder
# --------------------------------------------------------------------------- #
@dataclass
class FfmpegFilterGraph:
    """Small structured builder for ``-filter_complex`` chains.

    Each chain is ``inputs -> filters -> outputs``.  Filter names are checked
    against :data:`KNOWN_FILTERS` so a caller cannot smuggle a raw graph string
    in through a filter name, and every value that reaches a filter argument is
    escaped for the filtergraph parser.
    """

    known_filters: frozenset[str] = field(default_factory=lambda: KNOWN_FILTERS, repr=False)
    _chains: list[dict[str, Any]] = field(default_factory=list, repr=False)

    # -------------------------------------------------------------- chain API
    def chain(
        self,
        inputs: Sequence[str],
        filters: Sequence[str] = (),
        outputs: Sequence[str] = (),
    ) -> FfmpegFilterGraph:
        normalised_inputs = tuple(str(label).strip() for label in inputs)
        normalised_outputs = tuple(str(label).strip() for label in outputs)
        for label in (*normalised_inputs, *normalised_outputs):
            _check_label(label)
        steps = tuple(self._check_filter(spec) for spec in filters)
        self._chains.append({"inputs": normalised_inputs, "filters": steps, "outputs": normalised_outputs})
        return self

    def _check_filter(self, spec: str) -> str:
        text = str(spec)
        name = text.split("=", 1)[0].strip()
        if name not in self.known_filters:
            raise _domain_error(
                "FFMPEG_FILTER_NOT_ALLOWED",
                f"过滤器 {name!r} 不在白名单内，禁止拼接任意滤镜字符串",
                {"filter": name},
            )
        for forbidden in (";", chr(10), chr(13), chr(0)):
            if forbidden in text:
                raise _domain_error(
                    "FFMPEG_FILTER_NOT_ALLOWED",
                    "过滤器参数不能包含分号或换行",
                    {"filter": name},
                )
        return text

    def __str__(self) -> str:
        parts: list[str] = []
        for chain in self._chains:
            head = "".join(f"[{label}]" for label in chain["inputs"])
            body = ",".join(chain["filters"])
            tail = "".join(f"[{label}]" for label in chain["outputs"])
            parts.append(f"{head}{body}{tail}")
        return ";".join(parts)

    def __bool__(self) -> bool:
        return bool(self._chains)

    def as_dict(self) -> dict[str, Any]:
        return {"filter_complex": str(self), "chains": [dict(chain) for chain in self._chains]}

    # ------------------------------------------------------- structural proof
    def validate(self, *, terminal: Sequence[str] = ()) -> None:
        """Prove every internal label has exactly one producer and one consumer.

        A hand-written graph that consumes ``[voice]`` while something else
        produced ``[voice_raw]`` is structurally invalid, but FFmpeg only reports
        it as an opaque exit code at run time.  The builder therefore proves the
        two properties that make a graph executable *before* a process starts:

        * every internal label (one that is not a bare input-stream specifier
          such as ``0:v:0``) has exactly one producing chain;
        * every internal label a chain consumes has a producer;
        * every produced label is either consumed by another chain or declared
          ``terminal`` (i.e. mapped to an output with ``-map``).

        Two chains producing the same label, or a label fed to two chains
        without an explicit ``split``/``asplit``, are refused rather than left
        for FFmpeg to interpret.
        """

        terminal_labels = {str(label).strip() for label in terminal}
        producers: dict[str, int] = {}
        for index, chain in enumerate(self._chains):
            for label in chain["outputs"]:
                if label in producers:
                    raise _domain_error(
                        "FFMPEG_FILTERGRAPH_INVALID",
                        f"标签 [{label}] 有多个生产者，必须显式 split/asplit",
                        {"label": label, "chains": [producers[label], index]},
                    )
                producers[label] = index
        consumers: dict[str, list[int]] = {}
        for index, chain in enumerate(self._chains):
            for label in chain["inputs"]:
                if _is_stream_specifier(label):
                    # ``0:a:0`` addresses an input stream, not an internal label.
                    continue
                if label not in producers:
                    raise _domain_error(
                        "FFMPEG_FILTERGRAPH_INVALID",
                        f"标签 [{label}] 没有生产者，滤镜图无法执行",
                        {"label": label, "chain": index, "terminal": sorted(terminal_labels)},
                    )
                consumers.setdefault(label, []).append(index)
        for label, index in producers.items():
            if label in terminal_labels:
                continue
            if len(consumers.get(label, ())) != 1:
                raise _domain_error(
                    "FFMPEG_FILTERGRAPH_INVALID",
                    f"标签 [{label}] 未被消费，或需要多路分支时缺少 split/asplit",
                    {"label": label, "chain": index, "consumers": consumers.get(label, [])},
                )

    def label(self, base: str) -> str:
        """Allocate a unique internal label derived from ``base``.

        Hand-written label strings are what produced the ``[voice_raw]`` /
        ``[voice]`` mismatch this builder now refuses; callers should allocate
        through this method so a second use of the same base cannot collide.
        """

        stem = "".join(
            character if (character.isalnum() or character == "_") else "_"
            for character in str(base).strip()
        ) or "label"
        used = {label for chain in self._chains for label in (*chain["inputs"], *chain["outputs"])}
        if stem not in used:
            return stem
        index = 2
        while f"{stem}_{index}" in used:
            index += 1
        return f"{stem}_{index}"

    # ------------------------------------------------------- simple filters
    @staticmethod
    def setsar(value: str = "1") -> str:
        return f"setsar={value}"

    @staticmethod
    def asplit(*, outputs: int = 2) -> str:
        if outputs < 2:
            raise _domain_error("SCHEMA_INVALID", "asplit 至少需要两个输出")
        return f"asplit={int(outputs)}"

    @staticmethod
    def scale(width: int, height: int, *, fit: str = "LETTERBOX") -> str:
        fit_key = str(fit).upper()
        if fit_key == "LETTERBOX":
            return f"scale={int(width)}:{int(height)}:force_original_aspect_ratio=decrease:flags=lanczos"
        if fit_key in {"COVER", "CROP"}:
            return f"scale={int(width)}:{int(height)}:force_original_aspect_ratio=increase:flags=lanczos"
        raise _domain_error("SCHEMA_INVALID", "fit 必须是 LETTERBOX、COVER 或 CROP", {"fit": fit})

    @staticmethod
    def pad(width: int, height: int, *, color: str = "black") -> str:
        return f"pad={int(width)}:{int(height)}:(ow-iw)/2:(oh-ih)/2:color={color}"

    @staticmethod
    def crop(width: int, height: int) -> str:
        return f"crop={int(width)}:{int(height)}:(iw-ow)/2:(ih-oh)/2"

    @staticmethod
    def fps(fps_num: int, fps_den: int, *, round_mode: str = "near") -> str:
        return f"fps=fps={int(fps_num)}/{int(fps_den)}:round={round_mode}"

    @staticmethod
    def format(pix_fmt: str = VIDEO_PIX_FMT) -> str:
        return f"format=pix_fmts={pix_fmt}"

    @staticmethod
    def setpts(expression: str = "PTS-STARTPTS") -> str:
        return f"setpts={expression}"

    @staticmethod
    def asetpts(expression: str = "PTS-STARTPTS") -> str:
        return f"asetpts={expression}"

    @staticmethod
    def settb(timebase: str = VIDEO_TIMEBASE) -> str:
        return f"settb={timebase}"

    @staticmethod
    def trim(
        *,
        start: float | None = None,
        duration: float | None = None,
        start_frame: int | None = None,
        end_frame: int | None = None,
    ) -> str:
        parts: list[str] = []
        if start is not None:
            parts.append(f"start={_fmt_number(start)}")
        if duration is not None:
            parts.append(f"duration={_fmt_number(duration)}")
        if start_frame is not None:
            parts.append(f"start_frame={int(start_frame)}")
        if end_frame is not None:
            parts.append(f"end_frame={int(end_frame)}")
        if not parts:
            raise _domain_error("SCHEMA_INVALID", "trim 至少需要一个参数")
        return "trim=" + ":".join(parts)

    @staticmethod
    def atrim(
        *,
        start: float | None = None,
        duration: float | None = None,
        start_sample: int | None = None,
        end_sample: int | None = None,
        sample_count: int | None = None,
    ) -> str:
        """Build ``atrim`` with real FFmpeg sample options.

        ``sample_count`` is kept for callers that already used it, but it is not
        an option ``atrim`` accepts: FFmpeg rejects it with "Option not found" and
        exits 1.  Sample-domain cuts must be expressed as ``start_sample`` /
        ``end_sample``, and a sample count is translated into an ``end_sample``
        window instead of being emitted as an invalid option.
        """

        parts: list[str] = []
        if start is not None:
            parts.append(f"start={_fmt_number(start)}")
        if duration is not None:
            parts.append(f"duration={_fmt_number(duration)}")
        if start_sample is not None:
            parts.append(f"start_sample={int(start_sample)}")
        if end_sample is not None:
            parts.append(f"end_sample={int(end_sample)}")
        if sample_count is not None:
            if start_sample is not None or end_sample is not None:
                raise _domain_error(
                    "SCHEMA_INVALID",
                    "atrim 不能同时给出 sample_count 与 start_sample/end_sample",
                )
            parts.append("start_sample=0")
            parts.append(f"end_sample={int(sample_count)}")
        if not parts:
            raise _domain_error("SCHEMA_INVALID", "atrim 至少需要一个参数")
        return "atrim=" + ":".join(parts)

    @staticmethod
    def volume(*, gain_db: float = 0.0, factor: float | None = None) -> str:
        if factor is not None:
            return f"volume={float(factor):.6f}"
        return f"volume={10 ** (float(gain_db) / 20.0):.6f}"

    @staticmethod
    def aloop(*, loop: int = -1, size: int = 2_000_000_000, start: int = 0) -> str:
        return f"aloop=loop={int(loop)}:size={int(size)}:start={int(start)}"

    @staticmethod
    def afade(*, kind: str, start_seconds: float, duration_seconds: float, curve: str = "tri") -> str:
        direction = str(kind).lower()
        if direction not in {"in", "out"}:
            raise _domain_error("SCHEMA_INVALID", "afade kind 必须是 in 或 out", {"kind": kind})
        return (
            f"afade=t={direction}:st={_fmt_number(start_seconds)}:"
            f"d={_fmt_number(duration_seconds)}:curve={curve}"
        )

    @staticmethod
    def adelay(*, milliseconds: int = 0, all_channels: bool = True, samples: int | None = None) -> str:
        """Delay a stream, in milliseconds or — when given ``samples`` — exactly.

        A manifest places audio by *sample* position, and one millisecond is 48
        samples at 48 kHz, so a millisecond-only delay cannot honour the declared
        layout (design §7.2/§7.4).  ffmpeg accepts a sample count with the ``S``
        suffix, which is what a placement uses.
        """

        if samples is not None:
            value = f"{max(0, int(samples))}S"
        else:
            value = str(max(0, int(milliseconds)))
        return f"adelay={value}:all=1" if all_channels else f"adelay={value}"

    @staticmethod
    def loudnorm(*, i: float, tp: float, lra: float, print_format: str = "summary") -> str:
        return f"loudnorm=I={float(i):g}:TP={float(tp):g}:LRA={float(lra):g}:print_format={print_format}"

    @staticmethod
    def sidechaincompress(
        *,
        threshold: float = 0.05,
        ratio: float = 8.0,
        attack_ms: float = 20.0,
        release_ms: float = 300.0,
        makeup: float = 1.0,
    ) -> str:
        return (
            f"sidechaincompress=threshold={float(threshold):.6f}:ratio={float(ratio):.3f}:"
            f"attack={float(attack_ms):.3f}:release={float(release_ms):.3f}:makeup={float(makeup):.3f}"
        )

    @staticmethod
    def concat(*, inputs: int, video: bool = True, audio: bool = True) -> str:
        if inputs < 1:
            raise _domain_error("SCHEMA_INVALID", "concat 至少需要一个输入")
        return f"concat=n={int(inputs)}:v={1 if video else 0}:a={1 if audio else 0}"

    @staticmethod
    def overlay(*, x: str = "(W-w)/2", y: str = "(H-h)/2", shortest: bool = False) -> str:
        if shortest:
            raise _domain_error(
                "SHORTEST_SEMANTICS_FORBIDDEN",
                "禁止用 overlay 的最短流选项掩盖缺口",
                {"filter": "overlay"},
            )
        return f"overlay=x={x}:y={y}:shortest=0"

    @staticmethod
    def subtitles(filename: str, *, force_style: str | None = None) -> str:
        spec = f"subtitles=filename={escape_filter_value(filename)}"
        if force_style:
            spec += f":force_style={escape_filter_value(force_style)}"
        return spec

    @staticmethod
    def drawtext(*, text: str, x: str = "(w-text_w)/2", y: str = "h-th-40", font_size: int = 48,
                 font_color: str = "white", font_file: str | None = None, box: bool = False) -> str:
        parts = [
            f"text={escape_drawtext_text(text)}",
            f"x={x}",
            f"y={y}",
            f"fontsize={int(font_size)}",
            f"fontcolor={font_color}",
        ]
        if font_file:
            parts.append(f"fontfile={escape_filter_value(font_file)}")
        if box:
            parts.append("box=1:boxcolor=black@0.5")
        return "drawtext=" + ":".join(parts)

    @staticmethod
    def split(*, outputs: int) -> str:
        if outputs < 2:
            raise _domain_error("SCHEMA_INVALID", "split 至少需要两个输出")
        return f"split={int(outputs)}"

    @staticmethod
    def amix(*, inputs: int, duration: str = "longest", normalize: bool = False) -> str:
        if inputs < 1:
            raise _domain_error("SCHEMA_INVALID", "amix 至少需要一个输入")
        if duration not in {"longest", "shortest", "first"}:
            raise _domain_error("SCHEMA_INVALID", "amix duration 取值不合法", {"duration": duration})
        if duration == "shortest":
            raise _domain_error(
                "SHORTEST_SEMANTICS_FORBIDDEN",
                "禁止用 amix 的最短时长选项掩盖缺口",
                {"filter": "amix"},
            )
        return f"amix=inputs={int(inputs)}:duration={duration}:normalize={1 if normalize else 0}"

    @staticmethod
    def aformat(*, sample_rates: int = 48_000, channel_layouts: str = AUDIO_CHANNEL_LAYOUT,
                sample_fmts: str = AUDIO_SAMPLE_FMT) -> str:
        return (
            f"aformat=sample_fmts={sample_fmts}:sample_rates={int(sample_rates)}:"
            f"channel_layouts={channel_layouts}"
        )

    @staticmethod
    def apad(*, whole_duration: float | None = None) -> str:
        if whole_duration is None:
            return "apad"
        return f"apad=whole_dur={_fmt_number(whole_duration)}"

    @staticmethod
    def tpad(*, stop_mode: str = "clone", stop_duration: float = 0.0) -> str:
        return f"tpad=stop_mode={stop_mode}:stop_duration={_fmt_number(stop_duration)}"

    @staticmethod
    def anull() -> str:
        return "anull"


KNOWN_FILTERS: frozenset[str] = frozenset(
    {
        "scale",
        "fps",
        "format",
        "setpts",
        "asetpts",
        "settb",
        "trim",
        "atrim",
        "volume",
        "aloop",
        "afade",
        "adelay",
        "loudnorm",
        "sidechaincompress",
        "concat",
        "overlay",
        "subtitles",
        "drawtext",
        "split",
        "amix",
        "aformat",
        "apad",
        "tpad",
        "anull",
        "setsar",
        "asplit",
        "pad",
        "crop",
        "aresample",
        "color",
    }
)


def _check_label(label: str) -> None:
    if not label:
        raise _domain_error("SCHEMA_INVALID", "滤镜标签不能为空")
    newline, carriage_return = chr(10), chr(13)
    for forbidden in ("[", "]", ";", ",", newline, carriage_return):
        if forbidden in label:
            raise _domain_error("SCHEMA_INVALID", "滤镜标签包含非法字符", {"label": label})


def _is_stream_specifier(label: str) -> bool:
    """True for an input-stream address such as ``0:v:0`` rather than a label.

    ``_check_label`` refuses ``:`` in an internal label, so this distinction is
    exact: anything carrying a colon addresses an input stream and is produced by
    an ``-i`` input rather than by another chain.
    """

    return ":" in str(label)


# --------------------------------------------------------------------------- #
# command
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FfmpegCommand:
    """One fully-resolved FFmpeg invocation.

    ``args`` may only contain primitives appended by a builder; there is no
    constructor that accepts a pre-joined command line, and ``to_argv()`` never
    invokes a shell.

    ``options`` carries *sidecar content* that is not an argument — currently the
    concat demuxer list file, which the runner writes before starting FFmpeg.
    Keeping it here (rather than inside ``args``) is what makes it structurally
    impossible to smuggle a file body or a shell fragment into the argument
    vector.
    """

    args: tuple[str, ...]
    purpose: str
    chunk_no: int | None = None
    note: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", tuple(str(value) for value in self.args))
        object.__setattr__(self, "options", dict(self.options))
        if not self.args:
            raise _domain_error("SCHEMA_INVALID", "FFmpeg 命令不能为空")
        for index, value in enumerate(self.args):
            if "\x00" in value or "\n" in value:
                raise _domain_error(
                    "SCHEMA_INVALID",
                    "FFmpeg 参数不能包含 NUL 或换行",
                    {"index": index},
                )

    @property
    def sidecar(self) -> Mapping[str, Any]:
        """Sidecar content that must exist on disk before the command runs."""

        value = self.options.get("sidecar")
        return value if isinstance(value, Mapping) else {}

    def to_argv(self) -> list[str]:
        """The exact argument vector.  No shell, no interpolation, no expansion."""

        return list(self.args)

    def preview(self) -> str:
        """A display-safe one-line rendering.  Never executed as a shell string."""

        return " ".join(["ffmpeg", *(shlex.quote(value) for value in self.args)])

    def as_dict(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "chunk_no": self.chunk_no,
            "argv": self.to_argv(),
            "preview": self.preview(),
            "note": self.note,
            "shell": False,
            "options": dict(self.options),
            "command_hash": content_hash({"args": self.to_argv(), "purpose": self.purpose}),
        }


def _base_args() -> list[str]:
    return ["-hide_banner", "-nostats", "-y"]


# --------------------------------------------------------------------------- #
# command builders
# --------------------------------------------------------------------------- #
def _clip_media_path(
    clip: ManifestClip,
    media_path_resolver: Callable[[str, str | None], str | os.PathLike[str]] | None,
) -> str | None:
    return _resolve_media_path(
        media_version_id=clip.media_version_id,
        media_sha256=clip.media_sha256,
        owner_id=str(clip.clip_id),
        media_path_resolver=media_path_resolver,
    )


def _resolve_media_path(
    *,
    media_version_id: str | None,
    media_sha256: str | None,
    owner_id: str,
    media_path_resolver: Callable[[str, str | None], str | os.PathLike[str]] | None,
) -> str | None:
    if media_version_id is None:
        return None
    if media_path_resolver is None:
        raise _domain_error(
            "MEDIA_PATH_RESOLVER_REQUIRED",
            "清单包含媒体条目，但没有提供 media_path_resolver；渲染器不会自行猜测路径",
            {"clip_id": owner_id, "media_version_id": media_version_id},
        )
    resolved = media_path_resolver(str(media_version_id), media_sha256)
    return str(resolved)


def _clip_by_id(manifest: RenderManifest, clip_id: str) -> ManifestClip:
    for clip in manifest.clips:
        if str(clip.clip_id) == str(clip_id):
            return clip
    raise _domain_error(
        "SCHEMA_INVALID",
        "切片计划引用了清单中不存在的 clip",
        {"clip_id": str(clip_id)},
    )


def _input_count(args: Sequence[str]) -> int:
    """Number of ``-i`` inputs in a partially built argument vector."""

    return sum(1 for value in args if value == "-i")


def _samples_for_frames(frames: int, fps: Ratio, sample_rate: int) -> int:
    """Exact sample count for a frame count: one rational rounding, half-up."""

    return fps.samples_for_frames(int(frames), int(sample_rate))


#: Picture item kinds this chunk builder can compile today.  ``INFOGRAPHIC`` and
#: ``TEXT_LAYER`` need their own input strategy, so a manifest that declares them
#: is refused with a blocker instead of being rendered as unexplained black.
_COMPILABLE_VIDEO_ITEM_KINDS: frozenset[str] = frozenset(
    {"VIDEO_CLIP", "IMAGE_CLIP", "MOTION_CLIP"}
)


def _append_black_run(
    *,
    graph: FfmpegFilterGraph,
    args: list[str],
    frames: int,
    width: int,
    height: int,
    fps: Ratio,
    sample_rate: int,
    label_base: str,
) -> str:
    """Add an explicitly declared black picture run of exactly ``frames`` frames."""

    if int(frames) <= 0:
        raise _domain_error("SCHEMA_INVALID", "黑场时长必须为正", {"frames": int(frames)})
    args += [
        "-f", "lavfi", "-i",
        f"color=c=black:s={width}x{height}:r={fps.num}/{fps.den}:d={_fmt_number(fps.seconds_for_frames(int(frames)) + 1.0)}",
    ]
    input_index = _input_count(args) - 1
    label = graph.label(label_base)
    graph.chain(
        [f"{input_index}:v:0"],
        [
            FfmpegFilterGraph.fps(fps.num, fps.den),
            FfmpegFilterGraph.settb(VIDEO_TIMEBASE),
            FfmpegFilterGraph.trim(end_frame=int(frames)),
            FfmpegFilterGraph.setpts(),
            FfmpegFilterGraph.format(VIDEO_PIX_FMT),
        ],
        [label],
    )
    return label


def _expected_output_signature(manifest: RenderManifest) -> dict[str, Any]:
    return {
        "video_codec": "h264",
        "video_codec_tag": None,
        "profile": "High",
        "pix_fmt": VIDEO_PIX_FMT,
        "time_base": "1/1000000",
        "sample_rate_hz": int(manifest.audio_sample_rate_hz),
        "audio_codec": AUDIO_CODEC,
        "channel_layout": AUDIO_CHANNEL_LAYOUT,
        "sample_fmt": AUDIO_SAMPLE_FMT,
    }


def build_chunk_command(
    *,
    manifest: RenderManifest,
    chunk: ManifestChunkSpec,
    output_path: Path,
    work_dir: Path,
    media_path_resolver: Callable[[str, str | None], str | os.PathLike[str]] | None = None,
    has_audio_lookup: Mapping[str, bool] | None = None,
) -> FfmpegCommand:
    """Render one chunk as a self-contained CFR H.264/AAC file.

    The chunk's core tile is exactly ``[chunk.start_frame,
    chunk.end_frame_exclusive)``; ``handle_in_frames`` / ``handle_out_frames``
    extend the decoded window so a seam has material on both sides.  In-chunk
    cuts are joined with the ``concat`` filter (never with ``-shortest``), each
    source is normalised to the target pixel format/timebase/frame rate/sample
    rate, and the output is pinned with ``trim=end_frame`` plus a matching audio
    duration so the chunk's frame count is exact.
    """

    if not isinstance(manifest, RenderManifest):
        raise TypeError("build_chunk_command 需要 RenderManifest")
    if chunk.end_frame_exclusive > manifest.total_frames:
        raise _domain_error(
            "SCHEMA_INVALID",
            "分块区间超出整片帧数",
            {"chunk_no": chunk.chunk_no, "end_frame_exclusive": chunk.end_frame_exclusive, "total_frames": manifest.total_frames},
        )
    if chunk.start_frame >= chunk.end_frame_exclusive:
        raise _domain_error("SCHEMA_INVALID", "分块区间必须满足 end > start", {"chunk_no": chunk.chunk_no})

    decode_start = chunk.decode_start_frame
    decode_end = min(chunk.decode_end_frame_exclusive, int(manifest.total_frames))
    if decode_end <= decode_start:
        raise _domain_error("SCHEMA_INVALID", "分块解码窗口为空", {"chunk_no": chunk.chunk_no})

    # Only the picture track is compiled into the picture graph.  A narration or
    # BGM item that merely overlaps this block in time is *not* a video source;
    # feeding a WAV to the video concat used to fail with "[1:v:0] matches no
    # streams".  A declared item kind this builder cannot compile is refused
    # instead of being silently degraded to black fill.
    slices = compute_chunk_slices(manifest=manifest, chunk=chunk, tracks=("VIDEO",))
    if not slices:
        raise _domain_error(
            "CHUNK_HAS_NO_ITEMS",
            "分块内没有任何可编译的画面条目，无法渲染",
            {"chunk_no": chunk.chunk_no, "decode_start_frame": decode_start, "decode_end_frame": decode_end},
        )
    unsupported = sorted(
        {
            plan.item_kind
            for plan in slices
            if plan.item_kind not in _COMPILABLE_VIDEO_ITEM_KINDS
        }
    )
    if unsupported:
        raise _domain_error(
            "RENDER_ITEM_KIND_UNSUPPORTED",
            "分块含当前合成器无法编译的画面条目类型",
            {"chunk_no": chunk.chunk_no, "item_kinds": unsupported},
        )

    fps = manifest.fps
    handle_in_frames = min(int(chunk.handle_in_frames), max(0, decode_end - decode_start))
    output_frames = chunk.output_frame_count(int(manifest.total_frames))
    if output_frames <= 0:
        raise _domain_error(
            "CHUNK_HANDLES_EXHAUST_WINDOW",
            "分块的 handle 吃掉了整个解码窗口，没有可输出的帧",
            {"chunk_no": chunk.chunk_no, "decode_frames": decode_end - decode_start, "handle_in_frames": handle_in_frames},
        )
    target_frames = output_frames
    target_seconds = fps.seconds_for_frames(target_frames)
    width, height = int(manifest.width), int(manifest.height)
    sample_rate = int(manifest.audio_sample_rate_hz)

    args: list[str] = [*_base_args()]
    graph = FfmpegFilterGraph()
    #: film frame at which the next video slice starts.  A gap in the decode
    #: window is filled with an explicit black run, never with a stolen copy of
    #: another picture.
    cursor_frame = decode_start
    video_labels: list[str] = []
    audio_labels: list[str] = []
    # One declared silent input per block, added only if something actually needs
    # it: an input that no chain consumes would make the graph unprovable.
    silence_input: str | None = None

    def silence_stream() -> str:
        nonlocal silence_input
        if silence_input is None:
            args.extend([
                "-f", "lavfi", "-i",
                f"anullsrc=channel_layout={AUDIO_CHANNEL_LAYOUT}:sample_rate={sample_rate}",
            ])
            silence_input = f"{_input_count(args) - 1}:a"
        return silence_input

    def silence_bed(label_base: str, frames: int) -> str:
        label = graph.label(label_base)
        span_seconds = fps.seconds_for_frames(int(frames))
        graph.chain(
            [silence_stream()],
            [
                FfmpegFilterGraph.aformat(sample_rates=sample_rate),
                FfmpegFilterGraph.atrim(duration=span_seconds),
                FfmpegFilterGraph.asetpts(),
            ],
            [label],
        )
        return label

    for plan in slices:
        if plan.intersection_start_frame > cursor_frame:
            gap_frames = plan.intersection_start_frame - cursor_frame
            video_labels.append(
                _append_black_run(
                    graph=graph,
                    args=args,
                    frames=gap_frames,
                    width=width,
                    height=height,
                    fps=fps,
                    sample_rate=sample_rate,
                    label_base=f"gap{len(video_labels)}",
                )
            )
            audio_labels.append(silence_bed(f"gapa{len(video_labels)}", gap_frames))
        cursor_frame = plan.intersection_end_frame_exclusive
        clip = _clip_by_id(manifest, plan.clip_id)
        path = _resolve_media_path(
            media_version_id=plan.media_version_id,
            media_sha256=clip.media_sha256,
            owner_id=plan.clip_id,
            media_path_resolver=media_path_resolver,
        )
        span_seconds = fps.seconds_for_frames(plan.output_frame_count)
        if path is None:
            # A clip with no resolvable media becomes a declared black run of
            # exactly its own length, so the block layout does not change.
            video_labels.append(
                _append_black_run(
                    graph=graph,
                    args=args,
                    frames=plan.output_frame_count,
                    width=width,
                    height=height,
                    fps=fps,
                    sample_rate=sample_rate,
                    label_base=f"black{len(video_labels)}",
                )
            )
            audio_labels.append(silence_bed(f"blacka{len(video_labels)}", plan.output_frame_count))
            continue
        input_index = _input_count(args)
        args += ["-i", path]
        source_start_seconds = (plan.source_read_start_us or 0) / 1_000_000
        fit = str((clip.transform or {}).get("fit") or "LETTERBOX").upper()
        video_label = graph.label(f"v{len(video_labels)}")
        video_filters = [
            FfmpegFilterGraph.trim(start=source_start_seconds),
            FfmpegFilterGraph.setpts(),
            FfmpegFilterGraph.scale(width, height, fit=fit),
        ]
        if fit == "LETTERBOX":
            video_filters.append(FfmpegFilterGraph.pad(width, height))
        else:
            video_filters.append(FfmpegFilterGraph.crop(width, height))
        video_filters += [
            "setsar=1",
            FfmpegFilterGraph.fps(fps.num, fps.den),
            FfmpegFilterGraph.settb(VIDEO_TIMEBASE),
        ]
        declared_source_span = max(0, int(plan.local_source_span_us)) / 1_000_000
        if plan.covers_decode_end and declared_source_span < span_seconds:
            # The declared source is genuinely shorter than the block needs; the
            # design's answer is an explicit clone of the last frame for exactly
            # the declared shortfall, never a hidden -shortest substitution.
            video_filters.append(
                FfmpegFilterGraph.tpad(stop_mode="clone", stop_duration=span_seconds - declared_source_span)
            )
        video_filters += [
            FfmpegFilterGraph.trim(end_frame=max(1, plan.output_frame_count)),
            FfmpegFilterGraph.setpts(),
            FfmpegFilterGraph.format(VIDEO_PIX_FMT),
        ]
        graph.chain([f"{input_index}:v:0"], video_filters, [video_label])
        video_labels.append(video_label)

        declared_audio = True if has_audio_lookup is None else bool(
            has_audio_lookup.get(str(plan.media_version_id), True)
        )
        audio_label = graph.label(f"a{len(video_labels) - 1}")
        if declared_audio:
            audio_filters = [
                FfmpegFilterGraph.atrim(start=source_start_seconds),
                FfmpegFilterGraph.asetpts(),
                FfmpegFilterGraph.aformat(sample_rates=sample_rate),
                FfmpegFilterGraph.apad(whole_duration=span_seconds),
                FfmpegFilterGraph.atrim(duration=span_seconds),
                FfmpegFilterGraph.asetpts(),
            ]
            graph.chain([f"{input_index}:a:0"], audio_filters, [audio_label])
        else:
            graph.chain(
                [silence_stream()],
                [
                    FfmpegFilterGraph.aformat(sample_rates=sample_rate),
                    FfmpegFilterGraph.atrim(duration=span_seconds),
                    FfmpegFilterGraph.asetpts(),
                ],
                [audio_label],
            )
        audio_labels.append(audio_label)

    # ``concat`` needs at least two inputs.  A single label is used directly:
    # an empty filter chain (``[in][out]``) is not a valid filtergraph, so the
    # pass-through must not be emitted as a chain at all.
    if len(video_labels) > 1:
        vcat_label = graph.label("vcat")
        graph.chain(
            video_labels,
            [FfmpegFilterGraph.concat(inputs=len(video_labels), video=True, audio=False)],
            [vcat_label],
        )
        video_source = vcat_label
    else:
        video_source = video_labels[0]
    # The head handle is dropped in the frame domain (never seconds) so a
    # fractional frame rate cannot round a frame away at the seam.
    if handle_in_frames > 0:
        vhead_label = graph.label("vhead")
        graph.chain(
            [video_source],
            [
                FfmpegFilterGraph.fps(fps.num, fps.den),
                FfmpegFilterGraph.settb(VIDEO_TIMEBASE),
                FfmpegFilterGraph.trim(start_frame=handle_in_frames),
                FfmpegFilterGraph.setpts(),
            ],
            [vhead_label],
        )
        video_source = vhead_label
    vout_label = graph.label("vout")
    graph.chain(
        [video_source],
        [
            FfmpegFilterGraph.trim(start_frame=0, end_frame=target_frames),
            FfmpegFilterGraph.setpts(),
            FfmpegFilterGraph.format(VIDEO_PIX_FMT),
        ],
        [vout_label],
    )

    if len(audio_labels) > 1:
        acat_label = graph.label("acat")
        graph.chain(
            audio_labels,
            [FfmpegFilterGraph.concat(inputs=len(audio_labels), video=False, audio=True)],
            [acat_label],
        )
        audio_source = acat_label
    else:
        audio_source = audio_labels[0]
    # The audio handle is dropped in the *sample* domain: the same fractional
    # frame rate that decides the picture decides the sample, rounding once.
    handle_in_samples = _samples_for_frames(handle_in_frames, fps, sample_rate)
    if handle_in_samples > 0:
        ahead_label = graph.label("ahead")
        graph.chain(
            [audio_source],
            [FfmpegFilterGraph.atrim(start_sample=handle_in_samples), FfmpegFilterGraph.asetpts()],
            [ahead_label],
        )
        audio_source = ahead_label
    aout_label = graph.label("aout")
    graph.chain(
        [audio_source],
        [
            FfmpegFilterGraph.apad(whole_duration=target_seconds),
            FfmpegFilterGraph.atrim(duration=target_seconds),
            FfmpegFilterGraph.asetpts(),
        ],
        [aout_label],
    )
    graph.validate(terminal=[vout_label, aout_label])

    args += [
        "-filter_complex", str(graph),
        "-map", f"[{vout_label}]",
        "-map", f"[{aout_label}]",
        # The filter graph produced exactly ``target_frames`` frames at the target
        # rate already, so the muxer must not re-time them: ``cfr`` would duplicate
        # the tail frame to cover the fractionally longer AAC stream and the chunk
        # would silently gain frames.
        "-fps_mode", "passthrough",
        "-frames:v", str(target_frames),
        "-c:v", "libx264",
        "-preset", X264_PRESET,
        "-crf", str(X264_CRF),
        "-pix_fmt", VIDEO_PIX_FMT,
        "-video_track_timescale", "1000000",
        "-c:a", AUDIO_CODEC,
        "-b:a", AUDIO_BITRATE,
        "-ar", str(sample_rate),
        "-ac", "2",
        "-movflags", "+faststart",
        str(output_path),
    ]
    return FfmpegCommand(
        args=tuple(args),
        purpose="CHUNK",
        chunk_no=int(chunk.chunk_no),
        note=(
            f"分块 {chunk.chunk_no} 核心 [{chunk.start_frame},{chunk.end_frame_exclusive}) "
            f"handle ({chunk.handle_in_frames},{chunk.handle_out_frames}) 输出 {target_frames} 帧"
        ),
    )


def _concat_entry_line(path: Path) -> str:
    canonical = str(path).replace("\\", "/")
    return f"file {escape_concat_quote(canonical)}"


def build_concat_command(
    *,
    chunk_paths: Sequence[Path],
    work_dir: Path,
    output_path: Path,
    signature: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None,
    list_name: str | None = None,
) -> FfmpegCommand:
    """Join encoded chunks, stream-copying only when compatibility is proven.

    ``signature`` is either one signature shared by every chunk or one
    signature per chunk.  ``-c copy`` is used only when
    :func:`concat_compatibility` returns ``{"compatible": True}`` for every
    adjacent pair; otherwise the command re-encodes with the same frozen encoder
    settings used for the chunks, and the reason is carried in ``note``.
    """

    if not chunk_paths:
        raise _domain_error("SCHEMA_INVALID", "concat 至少需要一个分块文件")
    signatures: list[Mapping[str, Any] | None]
    if signature is None:
        signatures = [None] * len(chunk_paths)
    elif isinstance(signature, Mapping):
        if "compatible" in signature and "differences" in signature:
            signatures = [signature] * len(chunk_paths)
        else:
            signatures = [signature] * len(chunk_paths)
    else:
        signatures = list(signature)
        if len(signatures) != len(chunk_paths):
            raise _domain_error(
                "SCHEMA_INVALID",
                "signature 数量必须与分块数量一致",
                {"chunks": len(chunk_paths), "signatures": len(signatures)},
            )

    differences: list[dict[str, Any]] = []
    with_audio = True
    for index in range(len(signatures) - 1):
        verdict = concat_compatibility(signatures[index], signatures[index + 1])
        if not verdict["compatible"]:
            differences.extend(verdict["differences"])
    if signatures and signatures[0] is None:
        differences.append({"field": "signature", "left": None, "right": None})
    if signatures and any(sig is not None for sig in signatures):
        for sig in signatures:
            if sig is not None and sig.get("audio_codec") in (None, "", "none"):
                with_audio = False
                break

    list_path = work_dir / (list_name or "concat.txt")
    list_text = "\n".join(_concat_entry_line(Path(path)) for path in chunk_paths) + "\n"
    note: str | None = None
    args: list[str] = [
        *_base_args(),
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
        "-map", "0:v:0",
    ]
    if with_audio:
        args += ["-map", "0:a:0"]
    if differences:
        note = "编解码参数不一致，已显式重编码而不是流复制"
        args += [
            "-c:v", "libx264",
            "-preset", X264_PRESET,
            "-crf", str(X264_CRF),
            "-pix_fmt", VIDEO_PIX_FMT,
            "-fps_mode", "passthrough",
        ]
        if with_audio:
            args += ["-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE, "-ar", "48000", "-ac", "2"]
    else:
        note = "所有分块的编解码签名一致，允许流复制"
        args += ["-c", "copy"]
    args += ["-movflags", "+faststart", str(output_path)]

    sidecar = {
        "path": str(list_path),
        "text": list_text,
        "sha256": hashlib.sha256(list_text.encode("utf-8")).hexdigest(),
        "differences": differences,
        "reencode_required": bool(differences),
    }
    return FfmpegCommand(
        args=tuple(args),
        purpose="CONCAT",
        chunk_no=None,
        note=note,
        options={"sidecar": sidecar},
    )


#: Tracks the mixer is allowed to consume.  A picture track never reaches the
#: audio graph and an audio track never reaches the picture graph (design §7.2).
MIXABLE_AUDIO_TRACKS: frozenset[str] = frozenset({"NARRATION", "BGM", "SFX"})


def manifest_audio_placements(manifest: RenderManifest) -> list[ManifestClip]:
    """The audio clips the manifest declares, in declared order.

    The manifest is the only time authority: it carries each clip's source window
    and its absolute ``sample_start``/``sample_end_exclusive``, so the mixer must
    read it rather than concatenate whatever files the caller happens to pass in
    order.
    """

    return [
        clip
        for clip in manifest.clips
        if str(clip.track).upper() in MIXABLE_AUDIO_TRACKS
    ]


def _us_to_samples(value_us: int, sample_rate: int) -> int:
    """Microseconds to samples, rounded once (half up) against the output rate."""

    return int((int(value_us) * int(sample_rate) + 500_000) // 1_000_000)


def _audio_placement_chain(clip: ManifestClip, sample_rate: int) -> list[str]:
    """Trim one declared clip to its source window and place it absolutely."""

    filters: list[str] = []
    start_us = int(clip.source_in_us or 0)
    start_sample = _us_to_samples(start_us, sample_rate)
    if clip.source_out_us is None:
        # No declared window: use the whole source from the declared in-point.
        if start_sample:
            filters.append(FfmpegFilterGraph.atrim(start_sample=start_sample))
    else:
        end_sample = _us_to_samples(int(clip.source_out_us), sample_rate)
        # ``atrim`` rejects ``sample_count`` together with ``start_sample``; a count
        # must be expressed as the exclusive end sample.
        filters.append(
            FfmpegFilterGraph.atrim(
                start_sample=start_sample, end_sample=max(start_sample + 1, end_sample)
            )
        )
    filters.append(FfmpegFilterGraph.asetpts())
    filters.append(FfmpegFilterGraph.aformat(sample_rates=sample_rate))
    placement = int(clip.sample_start or 0)
    if placement > 0:
        # Sample-exact, because a millisecond is 48 samples at 48 kHz and the
        # manifest's layout is declared in samples.
        filters.append(FfmpegFilterGraph.adelay(samples=placement))
    return filters


def _legacy_audio_inputs(
    *,
    narration_paths: Sequence[Path],
    bgm_path: Path | None,
    sfx_paths: Sequence[Path],
    sample_rate: int,
    duck_db: float,
    args: list[str],
    graph: FfmpegFilterGraph,
) -> tuple[list[str], list[str]]:
    """The pre-manifest path: caller-ordered whole files, concatenated in order.

    Kept for the callers that hand over resolved narration files without a
    manifest audio declaration.  It cannot express sentence pauses or absolute
    placement, so a manifest that declares audio never uses it.
    """

    del duck_db
    narration_labels: list[str] = []
    for index, path in enumerate(narration_paths):
        args += ["-i", str(path)]
        label = graph.label(f"n{index}")
        graph.chain(
            [f"{index}:a:0"],
            [FfmpegFilterGraph.aformat(sample_rates=sample_rate), FfmpegFilterGraph.asetpts()],
            [label],
        )
        narration_labels.append(label)
    bed_labels: list[str] = []
    for index, path in enumerate(sfx_paths):
        args += ["-i", str(path)]
        label = graph.label(f"s{index}")
        graph.chain(
            [f"{len(narration_labels) + index}:a:0"],
            [
                FfmpegFilterGraph.aformat(sample_rates=sample_rate),
                FfmpegFilterGraph.volume(gain_db=0.0),
                FfmpegFilterGraph.asetpts(),
            ],
            [label],
        )
        bed_labels.append(label)
    if bgm_path is not None:
        args += ["-i", str(bgm_path)]
        bgm_label = graph.label("bgm")
        graph.chain(
            [f"{len(narration_labels) + len(sfx_paths)}:a:0"],
            [
                FfmpegFilterGraph.aformat(sample_rates=sample_rate),
                FfmpegFilterGraph.volume(gain_db=0.0),
                FfmpegFilterGraph.asetpts(),
            ],
            [bgm_label],
        )
        bed_labels.append(bgm_label)
    return narration_labels, bed_labels


def _manifest_audio_inputs(
    *,
    placements: Sequence[ManifestClip],
    audio_sources: Mapping[str, Path],
    sample_rate: int,
    args: list[str],
    graph: FfmpegFilterGraph,
) -> dict[str, list[str]]:
    """Add one input per distinct source and return labels grouped by track."""

    keys: list[str] = []
    for clip in placements:
        key = str(clip.media_version_id or clip.clip_id)
        if key not in keys:
            keys.append(key)
    input_index: dict[str, int] = {}
    for key in keys:
        source = audio_sources.get(key)
        if source is None:
            raise _domain_error(
                "AUDIO_SOURCE_MISSING",
                "manifest 声明了音频条目，但调用方没有提供该媒体版本的路径",
                {"media_version_id": key, "provided": sorted(audio_sources)},
            )
        args += ["-i", str(source)]
        input_index[key] = len(input_index)
    grouped: dict[str, list[str]] = {track: [] for track in ("NARRATION", "BGM", "SFX")}
    for clip in placements:
        track = str(clip.track).upper()
        key = str(clip.media_version_id or clip.clip_id)
        label = graph.label(f"{track.lower()}_{len(grouped[track])}")
        graph.chain(
            [f"{input_index[key]}:a:0"],
            _audio_placement_chain(clip, sample_rate),
            [label],
        )
        grouped[track].append(label)
    return grouped


def build_mix_command(
    *,
    manifest: RenderManifest,
    narration_paths: Sequence[Path],
    bgm_path: Path | None,
    sfx_paths: Sequence[Path],
    output_path: Path,
    duck_db: float = -12.0,
    work_dir: Path | None = None,
    loudness_target_lufs: float = DEFAULT_LOUDNESS_TARGET_LUFS,
    true_peak_dbtp: float = DEFAULT_TRUE_PEAK_DBTP,
    loudness_lra: float = DEFAULT_LOUDNESS_LRA,
    narration_start_sample: int = 0,
    audio_sources: Mapping[str, Path] | None = None,
) -> FfmpegCommand:
    """Build the deliberate audio mix, including sidechain ducking.

    The drama timeline's mixer has no ducking; this one does (design §11.4): the
    BGM/SFX bed is compressed by the concatenated narration as its sidechain
    control, the bed is attenuated at the same time, and the narration itself is
    summed back in afterwards so the voice is never processed by its own ducker.
    A final ``loudnorm`` walks the mix to the documented product default band;
    the note on the command states plainly that the band is a *product* default,
    not a platform standard.

    No ``-shortest`` is ever appended: a narration that is shorter than the film
    must be diagnosed, and legal silence must be declared in the manifest.
    """

    if not isinstance(manifest, RenderManifest):
        raise TypeError("build_mix_command 需要 RenderManifest")
    if int(narration_start_sample) != 0:
        # The parameter used to be written into the command note and otherwise
        # ignored, so a caller asking for a delayed start silently got audio from
        # sample 0.  A declared-but-unimplemented time offset is refused instead:
        # accepting a parameter and not applying it is the bug, not a feature.
        raise _domain_error(
            "NARRATION_START_NOT_SUPPORTED",
            "narration_start_sample 目前只支持 0；非零偏移必须由 manifest 的采样布局表达",
            {"narration_start_sample": int(narration_start_sample)},
        )
    sample_rate = int(manifest.audio_sample_rate_hz)
    total_samples = int(manifest.total_samples)
    total_seconds = total_samples / sample_rate

    args: list[str] = [*_base_args()]
    graph = FfmpegFilterGraph()

    narration_labels: list[str] = []
    bed_labels: list[str] = []
    declared_audio = manifest_audio_placements(manifest)
    if declared_audio:
        # Design §7.2: when the manifest declares audio, the manifest is the only
        # time authority.  Every narration sentence, music bed and effect is trimmed
        # to its own source window and placed at its declared absolute sample
        # position, so sentence pauses and effects are exactly where the plan put
        # them.  Caller-ordered files cannot express that: concatenating them
        # silently dropped both the pauses and every declared placement, and it let
        # a narration WAV be read as if it were the film's whole audio.
        if audio_sources is None:
            raise _domain_error(
                "AUDIO_SOURCES_REQUIRED",
                "manifest 声明了音频条目时必须提供媒体版本到路径的映射",
                {"declared_audio_clips": len(declared_audio)},
            )
        grouped = _manifest_audio_inputs(
            placements=declared_audio,
            audio_sources=audio_sources,
            sample_rate=sample_rate,
            args=args,
            graph=graph,
        )
        narration_labels = grouped["NARRATION"]
        bed_labels = grouped["BGM"] + grouped["SFX"]
        if narration_paths or bgm_path is not None or sfx_paths:
            # The manifest is the only time authority once it declares audio: a
            # caller that also hands over the old ordered path lists is describing a
            # second, contradictory layout, and silently preferring one of them is
            # how a declared pause disappears (design §7.2).
            raise _domain_error(
                "AUDIO_DECLARATION_MISMATCH",
                "manifest 已声明音频条目时不得再传旧式路径列表；两者不一致必须阻塞",
                {
                    "declared_audio_clips": len(declared_audio),
                    "narration_paths": len(narration_paths),
                    "bgm_path": bgm_path is not None,
                    "sfx_paths": len(sfx_paths),
                },
            )
    else:
        narration_labels, bed_labels = _legacy_audio_inputs(
            narration_paths=narration_paths,
            bgm_path=bgm_path,
            sfx_paths=sfx_paths,
            sample_rate=sample_rate,
            duck_db=duck_db,
            args=args,
            graph=graph,
        )
    voice_label: str | None = None
    if len(narration_labels) > 1:
        voice_label = graph.label("voice")
        if declared_audio:
            # Every declared clip already carries its own absolute placement
            # (``adelay`` to ``sample_start``), so the placed tracks must be
            # *summed*.  Concatenating them re-applied each clip's absolute offset
            # on top of the previous clips' spans: measured on a real 406 s film,
            # only 9 of 52 narration takes were audible and 82 % of the film was
            # digital silence although all 52 source WAVs carried continuous
            # speech.  ``amix`` keeps each take at its declared sample position and
            # ``duration=longest`` keeps the tail of the film.
            graph.chain(
                narration_labels,
                [FfmpegFilterGraph.amix(inputs=len(narration_labels), duration="longest", normalize=False)],
                [voice_label],
            )
        else:
            # The legacy path hands over unplaced whole files, one per sentence, so
            # their order *is* their layout and concatenation is correct.
            graph.chain(
                narration_labels,
                [FfmpegFilterGraph.concat(inputs=len(narration_labels), video=False, audio=True)],
                [voice_label],
            )
    elif narration_labels:
        # A single narration input is used directly; an empty chain would not be
        # a valid filtergraph.
        voice_label = narration_labels[0]

    bed_label: str | None = None
    if bed_labels:
        if len(bed_labels) == 1:
            bed_label = bed_labels[0]
        else:
            bed_label = graph.label("bed")
            graph.chain(
                bed_labels,
                [FfmpegFilterGraph.amix(inputs=len(bed_labels), duration="longest", normalize=False)],
                [bed_label],
            )

    duck_ratio = 10 ** (float(duck_db) / 20.0)
    mix_inputs: list[str] = []
    if bed_label is not None and voice_label is not None:
        # The narration drives the ducker through a dedicated second copy so the
        # voice that reaches the final mix is never processed by its own ducking.
        voice_mix = graph.label("voice_mix")
        voice_sc_raw = graph.label("voice_sc_raw")
        voice_sc = graph.label("voice_sc")
        bed_scaled = graph.label("bed_scaled")
        bed_ducked = graph.label("bed_ducked")
        graph.chain([voice_label], [FfmpegFilterGraph.asplit(outputs=2)], [voice_mix, voice_sc_raw])
        # The control track is padded to the whole film: ``sidechaincompress`` stops
        # at the shorter of its two inputs, so a voice-length sidechain truncated the
        # bed — a declared effect or music tail after the last sentence was silently
        # lost (design §7.2: the mix follows the manifest, including its尾声).
        graph.chain(
            [voice_sc_raw],
            [
                FfmpegFilterGraph.apad(whole_duration=total_seconds),
                FfmpegFilterGraph.atrim(duration=total_seconds),
            ],
            [voice_sc],
        )
        graph.chain(
            [bed_label],
            [FfmpegFilterGraph.volume(factor=duck_ratio)],
            [bed_scaled],
        )
        graph.chain(
            [bed_scaled, voice_sc],
            [FfmpegFilterGraph.sidechaincompress(threshold=0.05, ratio=8.0, attack_ms=20.0, release_ms=300.0)],
            [bed_ducked],
        )
        mix_inputs += [bed_ducked, voice_mix]
    elif bed_label is not None:
        mix_inputs.append(bed_label)
    elif voice_label is not None:
        mix_inputs.append(voice_label)
    else:
        # No declared audio at all: emit an explicit, legal silent bed rather
        # than relying on -shortest to paper over the missing stream.  The input
        # index is counted from the inputs actually added, so it stays correct
        # whether the mixer consumed the manifest or the legacy path list.
        args += ["-f", "lavfi", "-i", f"anullsrc=channel_layout={AUDIO_CHANNEL_LAYOUT}:sample_rate={sample_rate}"]
        silence_label = graph.label("silence")
        graph.chain(
            [f"{sum(1 for token in args if token == '-i') - 1}:a"],
            [FfmpegFilterGraph.aformat(sample_rates=sample_rate)],
            [silence_label],
        )
        mix_inputs.append(silence_label)

    # ``amix`` needs at least two inputs; one label is normalized directly.
    if len(mix_inputs) > 1:
        mixed_label = graph.label("mixed")
        graph.chain(
            mix_inputs,
            [FfmpegFilterGraph.amix(inputs=len(mix_inputs), duration="longest", normalize=False)],
            [mixed_label],
        )
        mixed_source = mixed_label
    else:
        mixed_source = mix_inputs[0]
    mixout_label = graph.label("mixout")
    # ``loudnorm`` bounds its own output to the true-peak target, but the lossy
    # encode that follows adds inter-sample peaks: the delivered 1080p film measured
    # -0.7 dBTP against the declared <= -1.0 dBTP ceiling, on a mix whose loudnorm
    # target was exactly -1.0.  The margin is applied to the normaliser only, so the
    # documented ceiling is what the *delivered* file satisfies rather than what the
    # intermediate PCM satisfied.  The mix command is PCM, so the margin is only
    # needed when the artefact will be encoded lossily — which every delivery is.
    graph.chain(
        [mixed_source],
        [
            FfmpegFilterGraph.apad(whole_duration=total_seconds),
            FfmpegFilterGraph.atrim(duration=total_seconds),
            FfmpegFilterGraph.loudnorm(
                i=loudness_target_lufs,
                tp=float(true_peak_dbtp) - ENCODE_TRUE_PEAK_MARGIN_DB,
                lra=loudness_lra,
            ),
            FfmpegFilterGraph.aformat(sample_rates=sample_rate),
        ],
        [mixout_label],
    )
    graph.validate(terminal=[mixout_label])

    args += [
        "-filter_complex", str(graph),
        "-map", f"[{mixout_label}]",
        "-t", _fmt_number(total_seconds),
        "-c:a", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", "2",
        str(output_path),
    ]
    note = (
        f"{LOUDNESS_NOTE}；duck 目标 {float(duck_db):.1f} dB（系数 {duck_ratio:.6f}）；"
        f"起点 0 采样（非零偏移不受支持，会被拒绝）；禁止 -shortest"
    )
    return FfmpegCommand(args=tuple(args), purpose="MIX", chunk_no=None, note=note)


def build_loudness_measure_command(
    *,
    input_path: Path,
    target_lufs: float,
    true_peak_dbtp: float,
    lra: float,
) -> FfmpegCommand:
    """Analyse one audio file with ``loudnorm`` and print its measurement as JSON.

    ``loudnorm`` in single-pass mode is a dynamic normaliser: on this project's
    125 s mix it delivered -18.0 LUFS against a -16.0 target, outside the -16 +/-1
    LUFS band the product declares and the QC layer checks.  The measurement pass
    exists so the *delivered* file can be brought onto the band with a second,
    linear pass instead of hoping the dynamic one lands there.
    """

    return FfmpegCommand(
        args=(
            *_base_args(),
            "-i", str(input_path),
            "-af", FfmpegFilterGraph.loudnorm(
                i=float(target_lufs),
                tp=float(true_peak_dbtp),
                lra=float(lra),
                print_format="json",
            ),
            "-f", "null",
            "-",
        ),
        purpose="LOUDNESS_MEASURE",
        chunk_no=None,
        note="只测量不改写：读取 loudnorm 的输入响度、真峰值与 LRA，供线性校正使用",
    )


def build_loudness_normalise_command(
    *,
    input_path: Path,
    output_path: Path,
    target_lufs: float,
    true_peak_dbtp: float,
    lra: float,
    measured: Mapping[str, Any],
    duration_seconds: float,
    sample_rate: int,
) -> FfmpegCommand:
    """Apply the measured ``loudnorm`` values as a *linear* gain correction.

    Linear mode computes one constant gain (plus a limiter only when the required
    gain would push the true peak past the ceiling), so the film's dynamics are
    preserved and the integrated loudness lands on the declared target.
    """

    required = ("measured_I", "measured_TP", "measured_LRA", "measured_thresh", "offset")
    missing = [key for key in required if measured.get(key) in (None, "")]
    if missing:
        raise _domain_error(
            "SCHEMA_INVALID",
            "线性响度校正缺少 loudnorm 测量值",
            {"missing": missing, "measured": dict(measured)},
        )
    parts = [
        FfmpegFilterGraph.loudnorm(
            i=float(target_lufs),
            tp=float(true_peak_dbtp),
            lra=float(lra),
            print_format="summary",
        ),
        f"measured_I={_fmt_number(float(measured['measured_I']))}",
        f"measured_TP={_fmt_number(float(measured['measured_TP']))}",
        f"measured_LRA={_fmt_number(float(measured['measured_LRA']))}",
        f"measured_thresh={_fmt_number(float(measured['measured_thresh']))}",
        f"offset={_fmt_number(float(measured['offset']))}",
        "linear=true",
    ]
    filter_value = ":".join(parts)
    return FfmpegCommand(
        args=(
            *_base_args(),
            "-i", str(input_path),
            "-af", filter_value,
            "-t", _fmt_number(float(duration_seconds)),
            "-c:a", "pcm_s16le",
            "-ar", str(int(sample_rate)),
            "-ac", "2",
            str(output_path),
        ),
        purpose="LOUDNESS_NORMALISE",
        chunk_no=None,
        note="按测量值做线性响度校正：保持动态，只施加常数增益，长度与采样率不变",
    )


def parse_loudness_measurement(stderr: str) -> dict[str, Any]:
    """The ``loudnorm ... print_format=json`` block at the end of FFmpeg's stderr.

    Returns ``{}`` when the block is absent *or* incomplete: a partial block cannot
    drive the linear correction, and an unparsable measurement must never be
    replaced by an invented one.
    """

    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    text = str(stderr or "")
    start = text.rfind("{")
    while start != -1:
        candidate = text[start:]
        end = candidate.rfind("}")
        if end != -1:
            try:
                payload = json.loads(candidate[: end + 1])
            except ValueError:
                payload = None
            if isinstance(payload, Mapping) and all(payload.get(key) is not None for key in required):
                return {
                    "measured_I": payload.get("input_i"),
                    "measured_TP": payload.get("input_tp"),
                    "measured_LRA": payload.get("input_lra"),
                    "measured_thresh": payload.get("input_thresh"),
                    "offset": payload.get("target_offset"),
                    "input_i": payload.get("input_i"),
                    "input_tp": payload.get("input_tp"),
                    "input_lra": payload.get("input_lra"),
                    "normalization_type": payload.get("normalization_type"),
                }
        start = text.rfind("{", 0, start)
    return {}


def build_subtitle_burn_command(
    *,
    input_path: Path,
    subtitle_path: Path,
    output_path: Path,
    style: Mapping[str, Any] | None = None,
    work_dir: Path | None = None,
) -> FfmpegCommand:
    """Burn one subtitle file into the picture with ``subtitles=``.

    FFmpeg's ``subtitles`` filter cannot round-trip its own escaping: a path with
    a ``:`` needs ``\\:`` inside a single-quoted run, and a path with a ``'``
    cannot be expressed at all.  The subtitle file is therefore staged into
    ``work_dir`` (or the output's directory) under a generated, metacharacter-free
    name, and FFmpeg is run with that directory as its working directory so the
    filter only ever sees a bare name such as ``subtitles=filename='sub-<hash>.srt'``.
    The staged copy is declared as the command's sidecar, so the runner writes it
    before FFmpeg starts and ``to_argv()`` stays free of any file body.
    """

    source = Path(subtitle_path)
    if not source.is_file():
        raise _domain_error(
            "SUBTITLE_FILE_MISSING",
            "字幕文件不存在，无法烧录",
            {"subtitle_path": str(source)},
        )
    staging_dir = Path(work_dir) if work_dir is not None else Path(output_path).parent
    suffix = source.suffix.lower() or ".srt"
    if suffix not in {".srt", ".ass", ".ssa", ".vtt"}:
        raise _domain_error(
            "SUBTITLE_FORMAT_UNSUPPORTED",
            "字幕格式必须是 srt、ass、ssa 或 vtt",
            {"suffix": suffix},
        )
    try:
        content = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise _domain_error(
            "SUBTITLE_FILE_UNREADABLE",
            "字幕文件无法按 UTF-8 读取",
            {"subtitle_path": str(source), "reason": type(error).__name__},
        ) from error
    safe_name = f"sub-{hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]}{suffix}"

    style_text = None
    if style:
        parts = []
        for key in ("FontName", "FontSize", "PrimaryColour", "OutlineColour", "BorderStyle", "Outline", "Shadow", "Alignment", "MarginV"):
            value = style.get(key)
            if value is None:
                continue
            parts.append(f"{key}={_validate_primitive(value, field_name=key)}")
        if parts:
            style_text = ",".join(parts)
    filter_spec = FfmpegFilterGraph.subtitles(safe_name, force_style=style_text)
    args: list[str] = [
        *_base_args(),
        "-i", str(input_path),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-vf", filter_spec,
        "-c:v", "libx264",
        "-preset", X264_PRESET,
        "-crf", str(X264_CRF),
        "-pix_fmt", VIDEO_PIX_FMT,
        "-c:a", "copy",
        "-fps_mode", "passthrough",
        "-movflags", "+faststart",
        str(output_path),
    ]
    sidecar = {
        "path": str(staging_dir / safe_name),
        "text": content,
        "source_path": str(source),
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "reencode_required": True,
    }
    return FfmpegCommand(
        args=tuple(args),
        purpose="SUBTITLE_BURN",
        chunk_no=None,
        note=(
            "字幕文件已暂存为无特殊字符的文件名并以该目录为工作目录，"
            "避免 FFmpeg subtitles 滤镜无法回转义路径中的冒号与单引号"
        ),
        options={"sidecar": sidecar, "work_dir": str(staging_dir)},
    )


# --------------------------------------------------------------------------- #
# process classification
# --------------------------------------------------------------------------- #
def classify_process_failure(
    *,
    returncode: int | None,
    stderr: str = "",
    stdout: str = "",
    output_path: Path | None = None,
    cancelled: bool = False,
) -> dict[str, Any]:
    """Classify a non-zero process exit, deciding whether a retry is legitimate.

    Categories, in the order they are decided:

    ``USER_CANCELLED``
        the caller asked for the stop, so nothing may be retried automatically;
    ``DISK_FULL`` / ``RESOURCE_EXHAUSTED``
        recoverable, but only after the caller performs a recovery action;
    ``TOOL_MISSING`` / ``INPUT_MISSING`` / ``PERMISSION_DENIED``
        environment or input problems with a concrete next step;
    ``FILTER_INVALID`` / ``ARGUMENT_INVALID``
        the command itself is wrong.  These are checked *before* the
        "no output was produced" fallback on purpose: a deterministic filtergraph
        error also produces no file, and the old ordering classified exactly that
        case as a retryable ``NO_OUTPUT`` — so a graph that can never succeed was
        retried until the budget ran out;
    ``TIMEOUT`` / ``PROCESS_KILLED``
        recoverable technical failures;
    ``NO_OUTPUT``
        recoverable only when nothing more specific is visible in the log;
    ``PROCESS_FAILED``
        unknown non-zero exit with output present.

    ``produced_output`` is reported for every branch but is never used as proof
    of success: a half-written file must not become a "recoverable" reason.
    """

    combined = f"{stdout}\n{stderr}"
    lowered = combined.lower()
    produced_output = bool(output_path is not None and Path(output_path).exists() and Path(output_path).stat().st_size > 0)
    code = None if returncode is None else int(returncode)

    def result(
        status: str,
        reason: str,
        recoverable: bool,
        retry_hint: str,
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "reason": reason,
            "recoverable": bool(recoverable),
            "retry_hint": retry_hint,
            "returncode": code,
            "produced_output": produced_output,
            **extra,
        }

    if cancelled or code in {_SIGINT, _SIGTERM} or any(marker in lowered for marker in _CANCEL_STDERR_MARKERS):
        return result(
            "CANCELLED",
            REASON_USER_CANCELLED,
            False,
            "已按请求停止，不自动重试；需要时由用户重新发起",
        )
    if any(marker in lowered for marker in _RECOVERABLE_STDERR_MARKERS):
        return result(
            "RECOVERABLE_FAILED",
            REASON_DISK_FULL,
            True,
            "清理工作目录/释放磁盘后重试；半成品不会登记为完成",
        )
    for marker, reason in _RESOURCE_STDERR_MARKERS:
        if marker in lowered:
            return result(
                "RECOVERABLE_FAILED",
                reason,
                True,
                "释放内存或降低并发后重试",
            )
    for marker, reason in _PERMANENT_STDERR_MARKERS:
        if marker in lowered:
            return result(
                "FAILED",
                reason,
                False,
                "这是确定的输入/参数/滤镜错误，重复同一命令不会恢复；请修正输入或命令",
            )
    if any(marker in lowered for marker in ("not found", "no such file")) and "ffmpeg" in lowered:
        return result(
            "UNAVAILABLE",
            REASON_TOOL_MISSING,
            False,
            "未找到 ffmpeg/ffprobe 可执行文件，需先安装或修正路径",
        )
    if code is not None and code < 0:
        return result(
            "RECOVERABLE_FAILED",
            REASON_PROCESS_KILLED,
            True,
            "进程被终止（可能由资源压力触发），清理后可重试",
        )
    if not produced_output:
        return result(
            "RECOVERABLE_FAILED",
            REASON_NO_OUTPUT,
            True,
            "进程未产出任何输出，且日志中没有确定的配置错误；清理后可重试一次",
        )
    return result(
        "FAILED",
        REASON_PROCESS_FAILED,
        False,
        "编码失败且已产出部分文件，需按日志检查输入与参数，不得当作成功",
    )


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
def decode_signature(probe: Mapping[str, Any]) -> dict[str, Any]:
    """Project an ffprobe result onto the fields that decide concat legality."""

    return {
        "video_codec": probe.get("video_codec") or probe.get("codec"),
        "video_codec_tag": probe.get("video_codec_tag"),
        "profile": probe.get("profile"),
        "pix_fmt": probe.get("pix_fmt"),
        "time_base": probe.get("video_time_base") or probe.get("time_base"),
        "extradata_hash": probe.get("extradata_hash"),
        "sample_rate_hz": probe.get("sample_rate_hz"),
        "audio_codec": probe.get("audio_codec"),
        "channel_layout": probe.get("channel_layout"),
        "sample_fmt": probe.get("sample_fmt"),
    }


def _parse_fraction(value: Any) -> tuple[int | None, int | None]:
    text = str(value or "").strip()
    if "/" in text:
        num_text, _, den_text = text.partition("/")
        try:
            num, den = int(num_text), int(den_text)
        except ValueError:
            return (None, None)
        if num > 0 and den > 0:
            return (num, den)
        return (None, None)
    try:
        parsed = float(text)
    except ValueError:
        return (None, None)
    if parsed <= 0:
        return (None, None)
    if abs(parsed - round(parsed)) < 1e-9:
        return (int(round(parsed)), 1)
    return (round(parsed * 1001), 1001)


def _unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "UNAVAILABLE", "reason": reason, "measured": False, **extra}


def _tail(text: str, limit: int) -> str:
    """Bounded tail of a log stream, so a report can never carry a whole log."""

    if limit <= 0:
        return ""
    return text[-limit:]


def _command_output_path(command: FfmpegCommand) -> Path | None:
    """The file a command writes: its last positional argument.

    ``FfmpegCommand`` builders always append the destination last, so this is the
    path the classifier must probe to decide whether a half file exists.
    """

    for value in reversed(tuple(command.args)):
        if value.startswith("-"):
            continue
        return Path(value)
    return None


_DECODE_ERROR_RE = None


def _default_process_runner(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Run one process with no shell, streaming its output through bounded tails.

    ``subprocess.run(capture_output=True)`` is *not* used: it returns the whole
    stream, so a long FFmpeg run with verbose logging can accumulate hundreds of
    megabytes in memory before the caller truncates the string.  Instead the
    pipes are drained incrementally; each stream keeps a bounded in-memory tail
    (:data:`DEFAULT_LOG_TAIL_BYTES`), the total byte count is recorded, and — when
    ``LOUDNESS_LOG_DIR`` (or ``DSH_FFMPEG_LOG_DIR``) is set — the full stream is
    appended to a per-run log file so bounding memory never loses the diagnosis.
    A timeout kills the whole process tree, not just the parent.
    """

    command = list(argv)
    tail_limit = _log_tail_bytes()
    log_dir_text = os.environ.get("LOUDNESS_LOG_DIR") or os.environ.get("DSH_FFMPEG_LOG_DIR")
    log_path: Path | None = None
    log_handle = None
    if log_dir_text:
        try:
            log_dir = Path(log_dir_text)
            log_dir.mkdir(parents=True, exist_ok=True)
            name = hashlib.sha256(" ".join(command).encode("utf-8", errors="replace")).hexdigest()[:16]
            log_path = log_dir / f"ffmpeg-{name}.log"
            log_handle = log_path.open("ab")
        except OSError:
            log_path = None
            log_handle = None

    state = {"stdout": bytearray(), "stderr": bytearray()}
    totals = {"stdout": 0, "stderr": 0}
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except OSError as error:
        if log_handle is not None:
            log_handle.close()
        return {
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "error": type(error).__name__,
            "errno": getattr(error, "errno", None),
            "log_path": None if log_path is None else str(log_path),
            "log_bytes": 0,
            "truncated": False,
        }

    def _pump(stream: Any, key: str) -> None:
        buffer = state[key]
        while True:
            block = stream.read(4096)
            if not block:
                break
            totals[key] += len(block)
            buffer.extend(block)
            if len(buffer) > tail_limit:
                del buffer[: len(buffer) - tail_limit]
            if log_handle is not None:
                log_handle.write(block)
        stream.close()

    timed_out = False
    try:
        threads = [
            threading.Thread(target=_pump, args=(process.stdout, "stdout"), daemon=True),
            threading.Thread(target=_pump, args=(process.stderr, "stderr"), daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            process.wait(timeout=float(timeout_seconds))
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process_tree(process)
            try:
                process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10.0)
        for thread in threads:
            thread.join(timeout=10.0)
    finally:
        if log_handle is not None:
            log_handle.close()

    def _decode(buffer: bytearray) -> str:
        return bytes(buffer).decode("utf-8", errors="replace")

    return {
        "returncode": None if timed_out else process.returncode,
        "stdout": _decode(state["stdout"]),
        "stderr": _decode(state["stderr"]),
        "timed_out": timed_out,
        "error": "TIMEOUT" if timed_out else None,
        "log_path": None if log_path is None else str(log_path),
        "log_bytes": int(totals["stdout"] + totals["stderr"]),
        "stdout_bytes": int(totals["stdout"]),
        "stderr_bytes": int(totals["stderr"]),
        "truncated": bool(totals["stdout"] + totals["stderr"] > tail_limit),
        "tail_limit_bytes": int(tail_limit),
    }


def _log_tail_bytes() -> int:
    raw = os.environ.get("DSH_FFMPEG_LOG_TAIL_BYTES")
    if raw is None:
        return DEFAULT_LOG_TAIL_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_LOG_TAIL_BYTES
    return max(4096, value)


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    """Stop a child and everything it started, without ever using a shell."""

    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
            )
            return
        except OSError:
            pass
    try:
        process.terminate()
    except OSError:
        pass


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


class FfmpegRunner:
    """Injected-process FFmpeg/ffprobe runner.

    ``runner`` is the only way a process is started; when a test supplies one,
    no executable is ever looked up and no subprocess is ever spawned.  The
    default runner uses ``subprocess.run`` with an explicit argument vector — a
    shell string is never constructed, so no quoting layer can be attacked.
    """

    def __init__(
        self,
        *,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        timeout_seconds: float = 3600.0,
        runner: Callable[..., Any] | None = None,
        which: Callable[[str], str | None] | None = None,
        tail_bytes: int = DEFAULT_LOG_TAIL_BYTES,
    ) -> None:
        self.ffmpeg = str(ffmpeg)
        self.ffprobe = str(ffprobe)
        self.timeout_seconds = float(timeout_seconds)
        self.tail_bytes = max(0, int(tail_bytes))
        self._runner = runner
        self._which = which or shutil.which
        self.calls: list[dict[str, Any]] = []

    # -------------------------------------------------------------- plumbing
    @property
    def has_injected_runner(self) -> bool:
        return self._runner is not None

    def _resolve(self, executable: str) -> str | None:
        if self._runner is not None:
            # An injected runner owns execution entirely; never probe the host.
            return executable
        if os.path.sep in executable or (os.path.altsep and os.path.altsep in executable):
            return executable if Path(executable).is_file() else None
        found = self._which(executable)
        return found if found else None

    def _invoke(self, argv: Sequence[str], *, cwd: Path | None = None) -> dict[str, Any]:
        if self._runner is not None:
            result = self._runner(list(argv), timeout_seconds=self.timeout_seconds, cwd=cwd)
            if not isinstance(result, Mapping):
                raise TypeError("注入的 runner 必须返回映射")
            return dict(result)
        return _default_process_runner(argv, timeout_seconds=self.timeout_seconds, cwd=cwd)

    # ------------------------------------------------------------------- run
    def run(self, command: FfmpegCommand) -> dict[str, Any]:
        """Execute one :class:`FfmpegCommand` and report a structured outcome."""

        if not isinstance(command, FfmpegCommand):
            raise TypeError("run 需要 FfmpegCommand")
        executable = self._resolve(self.ffmpeg)
        if executable is None:
            return _unavailable(
                "FFMPEG_NOT_FOUND",
                executable=self.ffmpeg,
                purpose=command.purpose,
                chunk_no=command.chunk_no,
                argv=command.to_argv(),
            )
        sidecar = command.sidecar
        if sidecar.get("path") and sidecar.get("text") is not None:
            path = Path(str(sidecar["path"]))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(sidecar["text"]), encoding="utf-8", newline="")
        work_dir = command.options.get("work_dir")
        cwd = Path(str(work_dir)) if work_dir else None
        argv = [executable, *command.to_argv()]
        outcome = self._invoke(argv, cwd=cwd)
        self.calls.append({"argv": argv, "purpose": command.purpose, "chunk_no": command.chunk_no})
        if outcome.get("error") == "TIMEOUT" or outcome.get("timed_out"):
            return {
                "status": "TIMEOUT",
                "purpose": command.purpose,
                "chunk_no": command.chunk_no,
                "returncode": None,
                "recoverable": True,
                "argv": argv,
            }
        if outcome.get("error") and outcome.get("returncode") is None:
            return {
                "status": "UNAVAILABLE" if outcome.get("errno") in (2, 3) else "FAILED",
                "reason": str(outcome.get("error")),
                "purpose": command.purpose,
                "chunk_no": command.chunk_no,
                "errno": outcome.get("errno"),
                "recoverable": True,
                "argv": argv,
            }
        returncode = outcome.get("returncode")
        if returncode == 0:
            return {
                "status": "SUCCEEDED",
                "purpose": command.purpose,
                "chunk_no": command.chunk_no,
                "returncode": 0,
                "argv": argv,
                "stderr_tail": _tail(_as_text(outcome.get("stderr")), self.tail_bytes),
                "log_path": outcome.get("log_path"),
                "log_bytes": outcome.get("log_bytes"),
                "log_truncated": outcome.get("truncated"),
            }
        # The output path is what the process was writing: without it the
        # classifier cannot tell "produced a half file" from "produced nothing",
        # and a deterministic filter error was therefore reported as a retryable
        # NO_OUTPUT.
        output_path = _command_output_path(command)
        classified = classify_process_failure(
            returncode=returncode if isinstance(returncode, int) else None,
            stderr=_as_text(outcome.get("stderr")),
            stdout=_as_text(outcome.get("stdout")),
            output_path=output_path,
            cancelled=bool(outcome.get("cancelled")),
        )
        return {
            **classified,
            "purpose": command.purpose,
            "chunk_no": command.chunk_no,
            "argv": argv,
            "stderr_tail": _tail(_as_text(outcome.get("stderr")), self.tail_bytes),
            "log_path": outcome.get("log_path"),
            "log_bytes": outcome.get("log_bytes"),
            "log_truncated": outcome.get("truncated"),
        }

    # -------------------------------------------------------- loudness pass
    def measure_loudness(
        self,
        path: Path,
        *,
        target_lufs: float = DEFAULT_LOUDNESS_TARGET_LUFS,
        true_peak_dbtp: float = DEFAULT_TRUE_PEAK_DBTP,
        lra: float = DEFAULT_LOUDNESS_LRA,
    ) -> dict[str, Any]:
        """Measure one file's integrated loudness, true peak and LRA.

        The numbers come from ``loudnorm``'s own analysis pass on this machine; an
        unparsable pass is reported as ``UNMEASURED`` and never replaced by an
        invented value.
        """

        command = build_loudness_measure_command(
            input_path=Path(path),
            target_lufs=float(target_lufs),
            true_peak_dbtp=float(true_peak_dbtp),
            lra=float(lra),
        )
        executable = self._resolve(self.ffmpeg)
        if executable is None:
            return _unavailable("FFMPEG_NOT_FOUND", executable=self.ffmpeg, purpose=command.purpose)
        outcome = self._invoke([executable, *command.to_argv()], cwd=None)
        self.calls.append({"argv": [executable, *command.to_argv()], "purpose": command.purpose})
        measured = parse_loudness_measurement(_as_text(outcome.get("stderr")))
        if outcome.get("returncode") != 0 or not measured:
            return {
                "status": "UNMEASURED",
                "reason": "LOUDNESS_MEASUREMENT_UNAVAILABLE",
                "measured": measured or None,
                "returncode": outcome.get("returncode"),
                "stderr_tail": _tail(_as_text(outcome.get("stderr")), self.tail_bytes),
            }
        return {"status": "OK", "measured": measured}

    def normalise_loudness(
        self,
        *,
        input_path: Path,
        output_path: Path,
        target_lufs: float = DEFAULT_LOUDNESS_TARGET_LUFS,
        true_peak_dbtp: float = DEFAULT_TRUE_PEAK_DBTP,
        lra: float = DEFAULT_LOUDNESS_LRA,
        duration_seconds: float,
        sample_rate: int,
        tolerance_lu: float = 0.5,
    ) -> dict[str, Any]:
        """Bring a mixed PCM file onto the declared loudness band, in two passes.

        The first pass only measures; the second applies the measurement as a
        linear gain.  When the mix already sits inside ``tolerance_lu`` of the
        target the second pass is skipped and ``input_path`` is returned as the
        deliverable, so an already-correct mix never pays for a rewrite.
        """

        measurement = self.measure_loudness(
            Path(input_path),
            target_lufs=float(target_lufs),
            true_peak_dbtp=float(true_peak_dbtp),
            lra=float(lra),
        )
        if str(measurement.get("status")) != "OK":
            return {**measurement, "reason": measurement.get("reason") or "LOUDNESS_MEASUREMENT_UNAVAILABLE"}
        measured = dict(measurement["measured"])
        try:
            integrated = float(measured["measured_I"])
        except (KeyError, TypeError, ValueError):
            return {"status": "UNMEASURED", "reason": "LOUDNESS_MEASUREMENT_UNPARSABLE", "measured": measured}
        if abs(integrated - float(target_lufs)) <= float(tolerance_lu):
            return {
                "status": "WITHIN_TOLERANCE",
                "measured": measured,
                "output_path": str(input_path),
                "target_lufs": float(target_lufs),
                "delta_lu": round(integrated - float(target_lufs), 3),
            }
        normalise_command = build_loudness_normalise_command(
            input_path=Path(input_path),
            output_path=Path(output_path),
            target_lufs=float(target_lufs),
            true_peak_dbtp=float(true_peak_dbtp),
            lra=float(lra),
            measured=measured,
            duration_seconds=float(duration_seconds),
            sample_rate=int(sample_rate),
        )
        correction = self.run(normalise_command)
        if str(correction.get("status")) != "SUCCEEDED":
            return {
                "status": "FAILED",
                "reason": str(correction.get("reason") or "LOUDNESS_CORRECTION_FAILED"),
                "measured": measured,
                "detail": correction,
            }
        return {
            "status": "CORRECTED",
            "measured": measured,
            "output_path": str(output_path),
            "target_lufs": float(target_lufs),
            "before_lufs": integrated,
            "delta_lu": round(integrated - float(target_lufs), 3),
            "argv": correction.get("argv"),
        }

    # ----------------------------------------------------------------- probe
    def probe(self, path: Path) -> dict[str, Any]:
        """Probe a file: frames, fps, duration, sample rate, codec, pix_fmt, timebase.

        Returns ``{"status": "UNAVAILABLE", ...}`` when ffprobe cannot be used and
        never fabricates a measurement; a missing file is reported as
        ``MISSING_FILE`` rather than as a crashed probe.
        """

        target = Path(path)
        if not target.exists():
            return _unavailable("MISSING_FILE", path=str(target))
        executable = self._resolve(self.ffprobe)
        if executable is None:
            return _unavailable("FFPROBE_NOT_FOUND", executable=self.ffprobe, path=str(target))
        argv = [
            executable,
            "-v", "error",
            "-show_format",
            "-show_streams",
            "-count_packets",
            "-of", "json",
            str(target),
        ]
        outcome = self._invoke(argv, cwd=None)
        if outcome.get("error") == "TIMEOUT" or outcome.get("timed_out"):
            return {"status": "TIMEOUT", "path": str(target), "argv": argv}
        if outcome.get("error") and outcome.get("returncode") is None:
            return {
                "status": "UNAVAILABLE",
                "reason": str(outcome.get("error")),
                "path": str(target),
                "errno": outcome.get("errno"),
            }
        returncode = outcome.get("returncode")
        if returncode != 0:
            return {
                "status": "PROBE_FAILED",
                "path": str(target),
                "returncode": returncode,
                "stderr_tail": _as_text(outcome.get("stderr"))[-1200:],
                "argv": argv,
            }
        import json as _json

        try:
            payload = _json.loads(_as_text(outcome.get("stdout")) or "{}")
        except ValueError:
            return {"status": "PROBE_INVALID_JSON", "path": str(target), "argv": argv}
        return {"status": "OK", "path": str(target), **self._summarise_probe(payload)}

    @staticmethod
    def _summarise_probe(payload: Mapping[str, Any]) -> dict[str, Any]:
        streams = payload.get("streams") or []
        format_data = payload.get("format") or {}
        video = next((s for s in streams if str(s.get("codec_type")) == "video"), None)
        audio = next((s for s in streams if str(s.get("codec_type")) == "audio"), None)
        duration_seconds = 0.0
        try:
            duration_seconds = float(format_data.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration_seconds = 0.0
        if duration_seconds <= 0:
            for stream in streams:
                try:
                    duration_seconds = max(duration_seconds, float(stream.get("duration") or 0.0))
                except (TypeError, ValueError):
                    continue
        fps_num, fps_den = (None, None)
        frame_count: int | None = None
        if video is not None:
            fps_num, fps_den = _parse_fraction(video.get("avg_frame_rate") or video.get("r_frame_rate"))
            raw_frames = video.get("nb_read_packets") or video.get("nb_frames")
            try:
                frame_count = int(raw_frames) if raw_frames is not None else None
            except (TypeError, ValueError):
                frame_count = None
        sample_rate: int | None = None
        if audio is not None:
            try:
                sample_rate = int(audio.get("sample_rate")) if audio.get("sample_rate") else None
            except (TypeError, ValueError):
                sample_rate = None
        return {
            "format_name": format_data.get("format_name"),
            "duration_seconds": duration_seconds,
            "duration_ms": int(round(duration_seconds * 1000)),
            "frame_count": frame_count,
            "fps_num": fps_num,
            "fps_den": fps_den,
            "video_codec": (video or {}).get("codec_name"),
            "video_codec_tag": (video or {}).get("codec_tag_string"),
            "profile": (video or {}).get("profile"),
            "pix_fmt": (video or {}).get("pix_fmt"),
            "width": (video or {}).get("width"),
            "height": (video or {}).get("height"),
            "video_time_base": (video or {}).get("time_base"),
            "nb_frames_reported": (video or {}).get("nb_frames"),
            "audio_codec": (audio or {}).get("codec_name"),
            "sample_rate_hz": sample_rate,
            "channel_layout": (audio or {}).get("channel_layout"),
            "sample_fmt": (audio or {}).get("sample_fmt"),
            "audio_time_base": (audio or {}).get("time_base"),
            "bit_rate": format_data.get("bit_rate"),
        }

    # -------------------------------------------------------- full decode
    def full_decode_check(self, path: Path) -> dict[str, Any]:
        """Decode every frame to ``null`` and report the decoded count.

        The run uses ``-xerror`` so a structural problem stops the decode, and
        ``-v info`` (with a fast stats period) so the final ``frame=`` progress
        line is available: a decode that reports no frame count at all is a
        failed check, not a pass.  Any error-flavoured log line is returned with
        its timecode so a truncated or corrupt file is never mistaken for a
        complete render.
        """

        target = Path(path)
        if not target.exists():
            return _unavailable("MISSING_FILE", path=str(target))
        executable = self._resolve(self.ffmpeg)
        if executable is None:
            return _unavailable("FFMPEG_NOT_FOUND", executable=self.ffmpeg, path=str(target))
        argv = [
            executable,
            "-v", "info",
            "-xerror",
            "-stats_period", "0.2",
            "-i", str(target),
            "-f", "null",
            "-",
        ]
        outcome = self._invoke(argv, cwd=None)
        if outcome.get("error") == "TIMEOUT" or outcome.get("timed_out"):
            return {"status": "TIMEOUT", "path": str(target), "argv": argv, "decoded_frames": None}
        if outcome.get("error") and outcome.get("returncode") is None:
            return {
                "status": "UNAVAILABLE",
                "reason": str(outcome.get("error")),
                "path": str(target),
                "decoded_frames": None,
            }
        stderr = _as_text(outcome.get("stderr"))
        decoded_frames = self._extract_decoded_frames(stderr)
        errors = self._extract_decode_errors(stderr)
        returncode = outcome.get("returncode")
        # A decode that reports no frame count did not prove anything, so it is a
        # failed check rather than a silent pass.
        ok = bool(returncode == 0 and errors == [] and decoded_frames is not None and decoded_frames > 0)
        return {
            "status": "OK" if ok else "DECODE_FAILED",
            "path": str(target),
            "returncode": returncode,
            "decoded_frames": decoded_frames,
            "errors": errors,
            "fatal": bool(errors),
            "frame_count_measured": decoded_frames is not None,
            "stderr_tail": stderr[-2000:],
            "argv": argv,
        }

    @staticmethod
    def _extract_decoded_frames(stderr: str) -> int | None:
        """The last ``frame=`` value on the log, which is the decoded total."""

        import re

        matches = re.findall(r"frame=\s*(\d+)", stderr)
        return int(matches[-1]) if matches else None

    @staticmethod
    def _extract_decode_errors(stderr: str) -> list[dict[str, Any]]:
        import re

        errors: list[dict[str, Any]] = []
        for line in stderr.splitlines():
            text = line.strip()
            if not text:
                continue
            if not re.search(r"(?i)\b(error|invalid|corrupt|missing|failed|truncated)\b", text):
                continue
            timecode_match = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", text)
            timecode = None
            if timecode_match:
                hours, minutes, seconds = timecode_match.groups()
                timecode = f"{int(hours):02d}:{int(minutes):02d}:{float(seconds):06.3f}"
            errors.append({"message": text, "timecode": timecode})
        return errors

    # ------------------------------------------------------------- conv enience
    def probe_error(self, probe: Mapping[str, Any]) -> str | None:
        status = str(probe.get("status") or "")
        if status == "OK":
            return None
        return status or "PROBE_FAILED"


# --------------------------------------------------------------------------- #
# publication
# --------------------------------------------------------------------------- #
def publish_atomically(
    *,
    temp_path: Path,
    final_path: Path,
    expected_sha256: str,
) -> dict[str, Any]:
    """Hash the temporary file, then ``os.replace`` it into place.

    The hash is verified against ``expected_sha256`` *before* the rename, so a
    half-written or corrupted file can never appear at the final path.  On any
    failure the temporary file is left where it is and reported, which keeps the
    failure inspectable and retryable.
    """

    temp = Path(temp_path)
    final = Path(final_path)
    if not temp.exists():
        return {
            "status": "FAILED",
            "reason": "TEMP_MISSING",
            "temp_path": str(temp),
            "final_path": str(final),
            "published": False,
            "recoverable": True,
        }
    try:
        digest = _hash_file(temp)
    except OSError as error:
        return {
            "status": "FAILED",
            "reason": "HASH_FAILED",
            "temp_path": str(temp),
            "final_path": str(final),
            "published": False,
            "recoverable": True,
            "errno": getattr(error, "errno", None),
            "detail": type(error).__name__,
        }
    if expected_sha256 and digest != str(expected_sha256):
        return {
            "status": "FAILED",
            "reason": "HASH_MISMATCH",
            "temp_path": str(temp),
            "final_path": str(final),
            "expected_sha256": str(expected_sha256),
            "actual_sha256": digest,
            "published": False,
            "recoverable": False,
        }
    try:
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp, final)
    except OSError as error:
        return {
            "status": "FAILED",
            "reason": "DISK_FULL" if getattr(error, "errno", None) in _RECOVERABLE_ERRNO else "REPLACE_FAILED",
            "temp_path": str(temp),
            "final_path": str(final),
            "sha256": digest,
            "published": False,
            "recoverable": getattr(error, "errno", None) in _RECOVERABLE_ERRNO,
            "errno": getattr(error, "errno", None),
        }
    size = final.stat().st_size
    return {
        "status": "PUBLISHED",
        "published": True,
        "temp_path": str(temp),
        "final_path": str(final),
        "sha256": digest,
        "byte_size": size,
        "half_file_at_final_path": False,
    }


def _hash_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def _empty_totals() -> dict[str, int]:
    return {"chunks_rendered": 0, "chunks_missing_media": 0, "published": 0, "failed": 0, "degraded": 0}


@dataclass
class AtomicRenderState:
    """Mutable bookkeeping for one :func:`atomic_render` attempt."""

    steps: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=_empty_totals)


def atomic_render(
    *,
    manifest: RenderManifest,
    temp_dir: Path,
    final_path: Path,
    work_dir: Path | None = None,
    run_command: Callable[[FfmpegCommand], Mapping[str, Any]],
    probe: Callable[[Path], Mapping[str, Any]],
    decode_check: Callable[[Path], Mapping[str, Any]],
    register: Callable[..., Any] | None = None,
    media_path_resolver: Callable[[str, str | None], str | os.PathLike[str]] | None = None,
    subtitle_paths: Sequence[Path] = (),
    subtitle_style: Mapping[str, Any] | None = None,
    narration_paths: Sequence[Path] = (),
    bgm_path: Path | None = None,
    sfx_paths: Sequence[Path] = (),
    audio_sources: Mapping[str, Path] | None = None,
    expected_sha256: str | None = None,
    keep_temp_on_failure: bool = True,
    has_audio_lookup: Mapping[str, bool] | None = None,
    chunk_builder: Callable[..., FfmpegCommand] = build_chunk_command,
    concat_builder: Callable[..., FfmpegCommand] = build_concat_command,
    signature: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    loudness_normaliser: Callable[..., Mapping[str, Any]] | None = None,
    loudness_target_lufs: float = DEFAULT_LOUDNESS_TARGET_LUFS,
) -> dict[str, Any]:
    """Render every chunk, validate each, concat, probe, full-decode, hash, publish.

    Every side effect goes through an injected callable, so the whole
    orchestration is exercised in tests without FFmpeg.  Recovery contract:

    * the render is written into ``temp_dir`` first and is never registered as
      complete at a partial path;
    * a failed chunk or a failed concat returns ``RECOVERABLE_FAILED`` for
      ``ENOSPC`` / killed-process / no-output, and ``FAILED`` otherwise;
    * the published file is hashed before ``os.replace``, so a half MP4 can never
      be listed as finished.
    """

    if not isinstance(manifest, RenderManifest):
        raise TypeError("atomic_render 需要 RenderManifest")
    temp_dir = Path(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(work_dir) if work_dir is not None else temp_dir / "work"
    staging_dir.mkdir(parents=True, exist_ok=True)

    state = AtomicRenderState()
    chunks = manifest.chunks
    if not chunks:
        return {
            "status": "FAILED",
            "reason": "NO_CHUNKS",
            "recoverable": False,
            "message": "清单没有分块计划，无法渲染",
            "steps": [],
            **state.totals,
        }

    chunk_results: list[dict[str, Any]] = []
    for chunk in chunks:
        chunk_path = staging_dir / f"chunk-{chunk.chunk_no:04d}.mp4"
        command = chunk_builder(
            manifest=manifest,
            chunk=chunk,
            output_path=chunk_path,
            work_dir=staging_dir,
            media_path_resolver=media_path_resolver,
            has_audio_lookup=has_audio_lookup,
        )
        outcome = dict(run_command(command))
        status = str(outcome.get("status") or "")
        if status == "UNAVAILABLE" and str(outcome.get("reason") or "") == "MEDIA_MISSING":
            state.totals["chunks_missing_media"] += 1
        if status != "SUCCEEDED":
            state.totals["failed"] += 1
            state.steps.append({"stage": "chunk", "chunk_no": chunk.chunk_no, **outcome})
            return {
                "status": "RECOVERABLE_FAILED" if outcome.get("recoverable") else "FAILED",
                "reason": outcome.get("reason") or status or "CHUNK_FAILED",
                "recoverable": bool(outcome.get("recoverable")),
                "chunk_no": chunk.chunk_no,
                "steps": state.steps,
                "temp_kept": bool(keep_temp_on_failure),
                **state.totals,
            }
        chunk_probe = dict(probe(chunk_path))
        if chunk_probe.get("status") != "OK":
            state.totals["failed"] += 1
            state.steps.append({"stage": "chunk-probe", "chunk_no": chunk.chunk_no, **chunk_probe})
            return {
                "status": "FAILED",
                "reason": "CHUNK_PROBE_FAILED",
                "recoverable": False,
                "chunk_no": chunk.chunk_no,
                "steps": state.steps,
                **state.totals,
            }
        expected_chunk_frames = chunk.output_frame_count(int(manifest.total_frames))
        actual_frames = chunk_probe.get("frame_count")
        if isinstance(actual_frames, int) and actual_frames != expected_chunk_frames:
            state.totals["failed"] += 1
            state.steps.append(
                {
                    "stage": "chunk-frame-check",
                    "chunk_no": chunk.chunk_no,
                    "expected_frames": expected_chunk_frames,
                    "actual_frames": actual_frames,
                }
            )
            return {
                "status": "FAILED",
                "reason": "CHUNK_FRAME_COUNT_MISMATCH",
                "recoverable": False,
                "chunk_no": chunk.chunk_no,
                "expected_frames": expected_chunk_frames,
                "actual_frames": actual_frames,
                "steps": state.steps,
                **state.totals,
            }
        state.totals["chunks_rendered"] += 1
        chunk_results.append(
            {
                "chunk_no": chunk.chunk_no,
                "path": str(chunk_path),
                "probe": chunk_probe,
                "signature": decode_signature(chunk_probe),
                "command": command.as_dict(),
            }
        )
        state.steps.append({"stage": "chunk", "chunk_no": chunk.chunk_no, "status": "SUCCEEDED"})

    chunk_paths = [Path(result["path"]) for result in chunk_results]
    final_signatures: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None
    if signature is not None:
        final_signatures = signature
    else:
        final_signatures = [result["signature"] for result in chunk_results]
    concat_command = concat_builder(
        chunk_paths=chunk_paths,
        work_dir=staging_dir,
        output_path=temp_dir / "render.mp4",
        signature=final_signatures,
    )
    concat_outcome = dict(run_command(concat_command))
    if str(concat_outcome.get("status") or "") != "SUCCEEDED":
        state.totals["failed"] += 1
        state.steps.append({"stage": "concat", **concat_outcome})
        return {
            "status": "RECOVERABLE_FAILED" if concat_outcome.get("recoverable") else "FAILED",
            "reason": concat_outcome.get("reason") or "CONCAT_FAILED",
            "recoverable": bool(concat_outcome.get("recoverable")),
            "steps": state.steps,
            **state.totals,
        }
    state.steps.append(
        {
            "stage": "concat",
            "status": "SUCCEEDED",
            "reencode_required": bool(concat_command.sidecar.get("reencode_required")),
        }
    )

    rendered = temp_dir / "render.mp4"
    if subtitle_paths:
        for index, subtitle_path in enumerate(subtitle_paths):
            burn_out = temp_dir / f"subtitled-{index:02d}.mp4"
            burn_command = build_subtitle_burn_command(
                input_path=rendered,
                subtitle_path=Path(subtitle_path),
                output_path=burn_out,
                style=subtitle_style,
            )
            burn_outcome = dict(run_command(burn_command))
            if str(burn_outcome.get("status") or "") != "SUCCEEDED":
                state.totals["failed"] += 1
                state.steps.append({"stage": "subtitle-burn", "index": index, **burn_outcome})
                return {
                    "status": "RECOVERABLE_FAILED" if burn_outcome.get("recoverable") else "FAILED",
                    "reason": burn_outcome.get("reason") or "SUBTITLE_BURN_FAILED",
                    "recoverable": bool(burn_outcome.get("recoverable")),
                    "steps": state.steps,
                    **state.totals,
                }
            rendered = burn_out
            state.steps.append({"stage": "subtitle-burn", "index": index, "status": "SUCCEEDED"})

    if narration_paths or bgm_path is not None or sfx_paths or manifest_audio_placements(manifest):
        mixed = temp_dir / "mix.wav"
        mix_command = build_mix_command(
            manifest=manifest,
            narration_paths=narration_paths,
            bgm_path=bgm_path,
            sfx_paths=sfx_paths,
            output_path=mixed,
            audio_sources=audio_sources,
        )
        mix_outcome = dict(run_command(mix_command))
        if str(mix_outcome.get("status") or "") != "SUCCEEDED":
            state.totals["failed"] += 1
            state.steps.append({"stage": "mix", **mix_outcome})
            return {
                "status": "RECOVERABLE_FAILED" if mix_outcome.get("recoverable") else "FAILED",
                "reason": mix_outcome.get("reason") or "MIX_FAILED",
                "recoverable": bool(mix_outcome.get("recoverable")),
                "steps": state.steps,
                **state.totals,
            }
        # Single-pass ``loudnorm`` is a dynamic normaliser and does not land on its
        # target: the delivered 1962 film measured -18.0 LUFS against a -16.0
        # target, one full LU outside the declared band.  The mix is therefore
        # measured and, when it misses, corrected with a second *linear* pass.  The
        # same true-peak head-room the mix pass used is applied here, because the
        # file being corrected is that mix and the delivered encode adds
        # inter-sample peaks on top of it.
        audio_for_mux = mixed
        if loudness_normaliser is not None:
            corrected = dict(
                loudness_normaliser(
                    input_path=mixed,
                    output_path=temp_dir / "mix-normalized.wav",
                    target_lufs=float(loudness_target_lufs),
                    true_peak_dbtp=float(DEFAULT_TRUE_PEAK_DBTP) - ENCODE_TRUE_PEAK_MARGIN_DB,
                    lra=float(DEFAULT_LOUDNESS_LRA),
                    duration_seconds=float(manifest.total_samples) / max(1, int(manifest.audio_sample_rate_hz)),
                    sample_rate=int(manifest.audio_sample_rate_hz),
                )
            )
            state.steps.append({"stage": "loudness", **corrected})
            if str(corrected.get("status")) == "CORRECTED" and corrected.get("output_path"):
                audio_for_mux = Path(str(corrected["output_path"]))
            elif str(corrected.get("status")) == "FAILED":
                # The mix is already loudnorm-normalised, so a failed *correction*
                # must not throw away a finished film — but it is recorded as a
                # degraded step, never as a pass, and the QC stage measures the
                # delivered file independently.
                state.totals["degraded"] = int(state.totals.get("degraded", 0)) + 1
        muxed = temp_dir / "muxed.mp4"
        mux_command = FfmpegCommand(
            args=(
                *_base_args(),
                "-i", str(rendered),
                "-i", str(audio_for_mux),
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", AUDIO_CODEC,
                "-b:a", AUDIO_BITRATE,
                "-movflags", "+faststart",
                str(muxed),
            ),
            purpose="MUX",
            note="视频流复制；音频来自本次混音，不使用 -shortest",
        )
        mux_outcome = dict(run_command(mux_command))
        if str(mux_outcome.get("status") or "") != "SUCCEEDED":
            state.totals["failed"] += 1
            state.steps.append({"stage": "mux", **mux_outcome})
            return {
                "status": "RECOVERABLE_FAILED" if mux_outcome.get("recoverable") else "FAILED",
                "reason": mux_outcome.get("reason") or "MUX_FAILED",
                "recoverable": bool(mux_outcome.get("recoverable")),
                "steps": state.steps,
                **state.totals,
            }
        rendered = muxed
        state.steps.append({"stage": "mux", "status": "SUCCEEDED"})

    final_probe = dict(probe(rendered))
    if final_probe.get("status") != "OK":
        state.totals["failed"] += 1
        state.steps.append({"stage": "probe", **final_probe})
        return {
            "status": "FAILED",
            "reason": "FINAL_PROBE_FAILED",
            "recoverable": False,
            "steps": state.steps,
            **state.totals,
        }
    probed_frames = final_probe.get("frame_count")
    if isinstance(probed_frames, int) and probed_frames != int(manifest.total_frames):
        state.totals["failed"] += 1
        state.steps.append(
            {
                "stage": "frame-check",
                "expected_frames": int(manifest.total_frames),
                "actual_frames": probed_frames,
            }
        )
        return {
            "status": "FAILED",
            "reason": "TOTAL_FRAME_COUNT_MISMATCH",
            "recoverable": False,
            "expected_frames": int(manifest.total_frames),
            "actual_frames": probed_frames,
            "steps": state.steps,
            **state.totals,
        }

    decoded = dict(decode_check(rendered))
    if str(decoded.get("status") or "") != "OK":
        state.totals["failed"] += 1
        state.steps.append({"stage": "full-decode", **decoded})
        return {
            "status": "FAILED",
            "reason": "FULL_DECODE_FAILED",
            "recoverable": False,
            "steps": state.steps,
            "decode": decoded,
            **state.totals,
        }
    decoded_frames = decoded.get("decoded_frames")
    if isinstance(decoded_frames, int) and decoded_frames != int(manifest.total_frames):
        state.totals["failed"] += 1
        state.steps.append(
            {
                "stage": "decode-frame-check",
                "expected_frames": int(manifest.total_frames),
                "decoded_frames": decoded_frames,
            }
        )
        return {
            "status": "FAILED",
            "reason": "DECODED_FRAME_COUNT_MISMATCH",
            "recoverable": False,
            "expected_frames": int(manifest.total_frames),
            "decoded_frames": decoded_frames,
            "steps": state.steps,
            **state.totals,
        }
    state.steps.append({"stage": "full-decode", "status": "OK", "decoded_frames": decoded_frames})

    digest = _hash_file(rendered)
    if expected_sha256 and digest != str(expected_sha256):
        state.totals["failed"] += 1
        return {
            "status": "FAILED",
            "reason": "HASH_MISMATCH",
            "recoverable": False,
            "expected_sha256": str(expected_sha256),
            "actual_sha256": digest,
            "steps": state.steps,
            **state.totals,
        }

    registration: Any = None
    if register is not None:
        registration = register(
            manifest_hash=manifest.manifest_hash,
            sha256=digest,
            frame_count=int(manifest.total_frames),
            duration_ms=int(round(float(final_probe.get("duration_seconds") or 0.0) * 1000)),
            probe=final_probe,
            chunks=chunk_results,
        )
        if isinstance(registration, Mapping) and str(registration.get("status") or "") in {"FAILED", "BLOCKED"}:
            state.totals["failed"] += 1
            state.steps.append({"stage": "register", **dict(registration)})
            return {
                "status": "FAILED",
                "reason": "REGISTRATION_FAILED",
                "recoverable": False,
                "sha256": digest,
                "registration": dict(registration),
                "steps": state.steps,
                **state.totals,
            }
        state.steps.append({"stage": "register", "status": "SUCCEEDED"})

    publication = publish_atomically(temp_path=rendered, final_path=Path(final_path), expected_sha256=digest)
    if not publication.get("published"):
        state.totals["failed"] += 1
        state.steps.append({"stage": "publish", **publication})
        return {
            "status": "RECOVERABLE_FAILED" if publication.get("recoverable") else "FAILED",
            "reason": publication.get("reason") or "PUBLISH_FAILED",
            "recoverable": bool(publication.get("recoverable")),
            "sha256": digest,
            "publication": publication,
            "steps": state.steps,
            **state.totals,
        }
    state.totals["published"] = 1
    state.steps.append({"stage": "publish", "status": "PUBLISHED"})
    return {
        "status": "SUCCEEDED",
        "reason": "PUBLISHED",
        "recoverable": False,
        "final_path": str(final_path),
        "sha256": digest,
        "frame_count": int(manifest.total_frames),
        "manifest_hash": manifest.manifest_hash,
        "probe": final_probe,
        "decode": decoded,
        "chunks": chunk_results,
        "registration": registration,
        "publication": publication,
        "steps": state.steps,
        **state.totals,
    }
