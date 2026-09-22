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
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from local_drama.application.composition.manifest import (
    ManifestChunkSpec,
    ManifestClip,
    RenderManifest,
)
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
    "VIDEO_PIX_FMT",
    "VIDEO_TIMEBASE",
    "X264_CRF",
    "X264_PRESET",
    "atomic_render",
    "build_chunk_command",
    "build_concat_command",
    "build_mix_command",
    "build_subtitle_burn_command",
    "classify_process_failure",
    "decode_signature",
    "escape_concat_quote",
    "escape_drawtext_text",
    "escape_filter_value",
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
    def trim(*, start: float | None = None, duration: float | None = None, end_frame: int | None = None) -> str:
        parts: list[str] = []
        if start is not None:
            parts.append(f"start={_fmt_number(start)}")
        if duration is not None:
            parts.append(f"duration={_fmt_number(duration)}")
        if end_frame is not None:
            parts.append(f"end_frame={int(end_frame)}")
        if not parts:
            raise _domain_error("SCHEMA_INVALID", "trim 至少需要一个参数")
        return "trim=" + ":".join(parts)

    @staticmethod
    def atrim(*, start: float | None = None, duration: float | None = None, sample_count: int | None = None) -> str:
        parts: list[str] = []
        if start is not None:
            parts.append(f"start={_fmt_number(start)}")
        if duration is not None:
            parts.append(f"duration={_fmt_number(duration)}")
        if sample_count is not None:
            parts.append(f"sample_count={int(sample_count)}")
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
    def adelay(*, milliseconds: int, all_channels: bool = True) -> str:
        value = max(0, int(milliseconds))
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
    }
)


def _check_label(label: str) -> None:
    if not label:
        raise _domain_error("SCHEMA_INVALID", "滤镜标签不能为空")
    newline, carriage_return = chr(10), chr(13)
    for forbidden in ("[", "]", ";", ",", newline, carriage_return):
        if forbidden in label:
            raise _domain_error("SCHEMA_INVALID", "滤镜标签包含非法字符", {"label": label})


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
    if clip.media_version_id is None:
        return None
    if media_path_resolver is None:
        raise _domain_error(
            "MEDIA_PATH_RESOLVER_REQUIRED",
            "清单包含媒体条目，但没有提供 media_path_resolver；渲染器不会自行猜测路径",
            {"clip_id": clip.clip_id, "media_version_id": clip.media_version_id},
        )
    resolved = media_path_resolver(str(clip.media_version_id), clip.media_sha256)
    return str(resolved)


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

    clips = [
        clip
        for clip in manifest.clips
        if int(clip.end_frame_exclusive) > decode_start and int(clip.start_frame) < decode_end
    ]
    if not clips:
        raise _domain_error(
            "CHUNK_HAS_NO_ITEMS",
            "分块内没有任何条目，无法渲染",
            {"chunk_no": chunk.chunk_no, "decode_start_frame": decode_start, "decode_end_frame": decode_end},
        )

    fps = manifest.fps
    # A block's *output* is exactly its core tile ``[start_frame,
    # end_frame_exclusive)``.  ``handle_in`` / ``handle_out`` only widen the
    # decoded window so a seam has material on both sides; the incoming handle is
    # trimmed off the head and the outgoing handle off the tail, which subtracts
    # the seam overlap exactly once and keeps ``sum(block output) ==
    # total_frames``.
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
    handle_in_seconds = fps.seconds_for_frames(handle_in_frames)
    # The material a block must have on hand, in output frames, before its head
    # trim and tail trim are applied.
    decode_span_frames = decode_end - decode_start
    decode_span_seconds = fps.seconds_for_frames(decode_span_frames)
    width, height = int(manifest.width), int(manifest.height)
    sample_rate = int(manifest.audio_sample_rate_hz)

    args: list[str] = [*_base_args()]
    graph = FfmpegFilterGraph()
    # Per clip: the video input index, the audio input index, and whether that
    # audio is a generated ``anullsrc`` stream.  A clip whose media carries no
    # usable audio gets a real silent stream of the same length, so every block
    # has one stable layout; the silence is explicit and declared, never hidden
    # with shortest-selection semantics.
    inputs: list[dict[str, Any]] = []
    input_cursor = 0
    for clip in clips:
        path = _clip_media_path(clip, media_path_resolver)
        declared_audio = True if has_audio_lookup is None else bool(
            has_audio_lookup.get(str(clip.media_version_id), True)
        )
        if path is None:
            video_index = input_cursor
            args += ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps.num}/{fps.den}"]
            audio_index = input_cursor + 1
            args += [
                "-f", "lavfi", "-i",
                f"anullsrc=channel_layout={AUDIO_CHANNEL_LAYOUT}:sample_rate={sample_rate}",
            ]
            input_cursor += 2
            inputs.append(
                {"video": video_index, "audio": audio_index, "silent": True, "clip": clip}
            )
            continue
        video_index = input_cursor
        args += ["-i", path]
        input_cursor += 1
        if declared_audio:
            inputs.append({"video": video_index, "audio": video_index, "silent": False, "clip": clip})
        else:
            audio_index = input_cursor
            args += [
                "-f", "lavfi", "-i",
                f"anullsrc=channel_layout={AUDIO_CHANNEL_LAYOUT}:sample_rate={sample_rate}",
            ]
            input_cursor += 1
            inputs.append({"video": video_index, "audio": audio_index, "silent": True, "clip": clip})

    video_labels: list[str] = []
    audio_labels: list[str] = []
    for index, entry in enumerate(inputs):
        clip = entry["clip"]
        source_in_seconds = (int(clip.source_in_us or 0)) / 1_000_000
        # The last contributing clip is the one that must cover the block's
        # decode window: an under-length tail (or an intentionally silent clip)
        # is padded with a cloned last frame, so the block always has exactly
        # ``decode_span_frames`` frames to trim from.  That padding is part of the
        # declared design, never a hidden -shortest substitution.
        last_contributing = index == len(clips) - 1
        clip_end_seconds = source_in_seconds + fps.seconds_for_frames(clip.frames)
        if last_contributing and clip_end_seconds < source_in_seconds + decode_span_seconds:
            clip_end_seconds = source_in_seconds + decode_span_seconds
        clip_span_seconds = clip_end_seconds - source_in_seconds
        fit = str((clip.transform or {}).get("fit") or "LETTERBOX").upper()
        video_out = f"v{index}"
        video_filters = [
            FfmpegFilterGraph.trim(start=source_in_seconds),
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
        if last_contributing:
            video_filters.append(FfmpegFilterGraph.tpad(stop_mode="clone", stop_duration=clip_span_seconds))
        video_filters += [
            FfmpegFilterGraph.trim(end_frame=max(1, round(clip_span_seconds * fps.value))),
            FfmpegFilterGraph.setpts(),
            FfmpegFilterGraph.format(VIDEO_PIX_FMT),
        ]
        graph.chain([f"{entry['video']}:v:0"], video_filters, [video_out])
        video_labels.append(video_out)

        audio_out = f"a{index}"
        # A clip without usable audio gets a real, declared silent stream of the
        # same length, so concat always sees one stable layout.  That silence is
        # explicit here; it is never hidden with shortest-selection semantics.
        # ``anullsrc`` carries a single audio stream, so it is referenced as
        # ``[n:a]``; a media input uses the explicit ``[n:a:0]`` specifier.
        audio_input_label = f"{entry['audio']}:a" if entry["silent"] else f"{entry['audio']}:a:0"
        audio_filters = [
            FfmpegFilterGraph.atrim(start=source_in_seconds),
            FfmpegFilterGraph.asetpts(),
            FfmpegFilterGraph.aformat(sample_rates=sample_rate),
            FfmpegFilterGraph.apad(whole_duration=clip_span_seconds),
            FfmpegFilterGraph.atrim(duration=clip_span_seconds),
            FfmpegFilterGraph.asetpts(),
        ]
        graph.chain([audio_input_label], audio_filters, [audio_out])
        audio_labels.append(audio_out)

    # ``concat`` needs at least two inputs.  A single label is used directly:
    # an empty filter chain (``[in][out]``) is not a valid filtergraph, so the
    # pass-through must not be emitted as a chain at all.
    if len(video_labels) > 1:
        graph.chain(video_labels, [FfmpegFilterGraph.concat(inputs=len(video_labels), video=True, audio=False)], ["vcat"])
        video_source = "vcat"
    else:
        video_source = video_labels[0]
    if handle_in_frames > 0:
        graph.chain(
            [video_source],
            [FfmpegFilterGraph.trim(start=handle_in_seconds), FfmpegFilterGraph.setpts()],
            ["vhead"],
        )
        video_source = "vhead"
    graph.chain(
        [video_source],
        [FfmpegFilterGraph.trim(end_frame=target_frames), FfmpegFilterGraph.setpts()],
        ["vout"],
    )

    if len(audio_labels) > 1:
        graph.chain(audio_labels, [FfmpegFilterGraph.concat(inputs=len(audio_labels), video=False, audio=True)], ["acat"])
        audio_source = "acat"
    else:
        audio_source = audio_labels[0]
    if handle_in_frames > 0:
        graph.chain(
            [audio_source],
            [FfmpegFilterGraph.atrim(start=handle_in_seconds), FfmpegFilterGraph.asetpts()],
            ["ahead"],
        )
        audio_source = "ahead"
    graph.chain(
        [audio_source],
        [
            FfmpegFilterGraph.apad(whole_duration=target_seconds),
            FfmpegFilterGraph.atrim(duration=target_seconds),
            FfmpegFilterGraph.asetpts(),
        ],
        ["aout"],
    )

    args += [
        "-filter_complex", str(graph),
        "-map", "[vout]",
        "-map", "[aout]",
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
    sample_rate = int(manifest.audio_sample_rate_hz)
    total_samples = int(manifest.total_samples)
    total_seconds = total_samples / sample_rate

    args: list[str] = [*_base_args()]
    graph = FfmpegFilterGraph()

    narration_labels: list[str] = []
    for index, path in enumerate(narration_paths):
        args += ["-i", str(path)]
        label = f"n{index}"
        graph.chain(
            [f"{index}:a:0"],
            [FfmpegFilterGraph.aformat(sample_rates=sample_rate), FfmpegFilterGraph.asetpts()],
            [label],
        )
        narration_labels.append(label)
    voice_label: str | None = None
    if len(narration_labels) > 1:
        graph.chain(
            narration_labels,
            [FfmpegFilterGraph.concat(inputs=len(narration_labels), video=False, audio=True)],
            ["voice_raw"],
        )
        voice_label = "voice"
    elif narration_labels:
        # A single narration input is used directly; an empty chain would not be
        # a valid filtergraph.
        voice_label = narration_labels[0]

    bed_labels: list[str] = []
    for index, path in enumerate(sfx_paths):
        args += ["-i", str(path)]
        label = f"s{index}"
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
        graph.chain(
            [f"{len(narration_labels) + len(sfx_paths)}:a:0"],
            [
                FfmpegFilterGraph.aformat(sample_rates=sample_rate),
                FfmpegFilterGraph.volume(gain_db=0.0),
                FfmpegFilterGraph.asetpts(),
            ],
            ["bgm"],
        )
        bed_labels.append("bgm")

    bed_label: str | None = None
    if bed_labels:
        if len(bed_labels) == 1:
            bed_label = bed_labels[0]
        else:
            graph.chain(
                bed_labels,
                [FfmpegFilterGraph.amix(inputs=len(bed_labels), duration="longest", normalize=False)],
                ["bed"],
            )
            bed_label = "bed"

    duck_ratio = 10 ** (float(duck_db) / 20.0)
    mix_inputs: list[str] = []
    if bed_label is not None and voice_label is not None:
        # The narration drives the ducker through a dedicated second copy so the
        # voice that reaches the final mix is never processed by its own ducking.
        graph.chain([voice_label], [FfmpegFilterGraph.asplit(outputs=2)], ["voice_mix", "voice_sc"])
        graph.chain(
            [bed_label],
            [FfmpegFilterGraph.volume(factor=duck_ratio)],
            ["bed_scaled"],
        )
        graph.chain(
            ["bed_scaled", "voice_sc"],
            [FfmpegFilterGraph.sidechaincompress(threshold=0.05, ratio=8.0, attack_ms=20.0, release_ms=300.0)],
            ["bed_ducked"],
        )
        mix_inputs += ["bed_ducked", "voice_mix"]
    elif bed_label is not None:
        mix_inputs.append(bed_label)
    elif voice_label is not None:
        mix_inputs.append(voice_label)
    else:
        # No declared audio at all: emit an explicit, legal silent bed rather
        # than relying on -shortest to paper over the missing stream.
        args += ["-f", "lavfi", "-i", f"anullsrc=channel_layout={AUDIO_CHANNEL_LAYOUT}:sample_rate={sample_rate}"]
        graph.chain(
            [f"{len(narration_paths) + len(sfx_paths) + (1 if bgm_path is not None else 0)}:a"],
            [FfmpegFilterGraph.aformat(sample_rates=sample_rate)],
            ["silence"],
        )
        mix_inputs.append("silence")

    # ``amix`` needs at least two inputs; one label is normalized directly.
    if len(mix_inputs) > 1:
        graph.chain(
            mix_inputs,
            [FfmpegFilterGraph.amix(inputs=len(mix_inputs), duration="longest", normalize=False)],
            ["mixed"],
        )
        mixed_source = "mixed"
    else:
        mixed_source = mix_inputs[0]
    graph.chain(
        [mixed_source],
        [
            FfmpegFilterGraph.apad(whole_duration=total_seconds),
            FfmpegFilterGraph.atrim(duration=total_seconds),
            FfmpegFilterGraph.loudnorm(i=loudness_target_lufs, tp=true_peak_dbtp, lra=loudness_lra),
            FfmpegFilterGraph.aformat(sample_rates=sample_rate),
        ],
        ["mixout"],
    )

    args += [
        "-filter_complex", str(graph),
        "-map", "[mixout]",
        "-t", _fmt_number(total_seconds),
        "-c:a", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", "2",
        str(output_path),
    ]
    note = (
        f"{LOUDNESS_NOTE}；duck 目标 {float(duck_db):.1f} dB（系数 {duck_ratio:.6f}）；"
        f"起点 {int(narration_start_sample)} 采样；禁止 -shortest"
    )
    return FfmpegCommand(args=tuple(args), purpose="MIX", chunk_no=None, note=note)


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
) -> dict[str, Any]:
    """Classify a non-zero process exit as recoverable or not.

    Disk-full (``ENOSPC``) and a process that died without producing output are
    *recoverable*: the caller may clean the work directory and retry.  Everything
    else is reported with its own code so the caller cannot mistake a real
    encoding failure for a transient one.
    """

    combined = f"{stdout}\n{stderr}".lower()
    produced_output = bool(output_path is not None and output_path.exists() and output_path.stat().st_size > 0)
    if any(marker in combined for marker in _RECOVERABLE_STDERR_MARKERS):
        return {
            "status": "RECOVERABLE_FAILED",
            "reason": "DISK_FULL",
            "recoverable": True,
            "retry_hint": "清理工作目录后重试；半成品不会登记为完成",
            "returncode": returncode,
            "produced_output": produced_output,
        }
    if returncode is not None and int(returncode) < 0:
        return {
            "status": "RECOVERABLE_FAILED",
            "reason": "PROCESS_KILLED",
            "recoverable": True,
            "retry_hint": "进程被终止（可能由取消或资源压力触发），清理后可重试",
            "returncode": returncode,
            "produced_output": produced_output,
        }
    if not produced_output:
        return {
            "status": "RECOVERABLE_FAILED",
            "reason": "NO_OUTPUT",
            "recoverable": True,
            "retry_hint": "进程未产出任何输出，清理后可重试",
            "returncode": returncode,
            "produced_output": False,
        }
    return {
        "status": "FAILED",
        "reason": "PROCESS_FAILED",
        "recoverable": False,
        "retry_hint": "编码失败，需检查输入与参数",
        "returncode": returncode,
        "produced_output": produced_output,
    }


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


_DECODE_ERROR_RE = None


def _default_process_runner(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
    cwd: Path | None = None,
) -> dict[str, Any]:
    """Run one process with no shell and capture bounded output tails."""

    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=float(timeout_seconds),
            cwd=str(cwd) if cwd is not None else None,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "returncode": None,
            "stdout": _as_text(error.stdout),
            "stderr": _as_text(error.stderr),
            "timed_out": True,
            "error": "TIMEOUT",
        }
    except OSError as error:
        return {
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "error": type(error).__name__,
            "errno": getattr(error, "errno", None),
        }
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
        "timed_out": False,
        "error": None,
    }


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
    ) -> None:
        self.ffmpeg = str(ffmpeg)
        self.ffprobe = str(ffprobe)
        self.timeout_seconds = float(timeout_seconds)
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
                "stderr_tail": _as_text(outcome.get("stderr"))[-4000:],
            }
        classified = classify_process_failure(
            returncode=returncode if isinstance(returncode, int) else None,
            stderr=_as_text(outcome.get("stderr")),
            stdout=_as_text(outcome.get("stdout")),
        )
        return {
            **classified,
            "purpose": command.purpose,
            "chunk_no": command.chunk_no,
            "argv": argv,
            "stderr_tail": _as_text(outcome.get("stderr"))[-4000:],
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
    return {"chunks_rendered": 0, "chunks_missing_media": 0, "published": 0, "failed": 0}


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
    expected_sha256: str | None = None,
    keep_temp_on_failure: bool = True,
    chunk_builder: Callable[..., FfmpegCommand] = build_chunk_command,
    concat_builder: Callable[..., FfmpegCommand] = build_concat_command,
    signature: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
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

    if narration_paths or bgm_path is not None or sfx_paths:
        mixed = temp_dir / "mix.wav"
        mix_command = build_mix_command(
            manifest=manifest,
            narration_paths=narration_paths,
            bgm_path=bgm_path,
            sfx_paths=sfx_paths,
            output_path=mixed,
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
        muxed = temp_dir / "muxed.mp4"
        mux_command = FfmpegCommand(
            args=(
                *_base_args(),
                "-i", str(rendered),
                "-i", str(mixed),
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
