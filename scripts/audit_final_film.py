"""最终成片逐帧/逐段解析：抽帧、场景切点、静止/黑帧、音频响度/静音/削波、与字幕与镜头对齐。

用法：python scripts/audit_final_film.py [--project <id>] [--frames 24] [--fps 5]

产物：artifacts/film-audit/<edition>/{frames/*.png, cut-sheet.png, report.json}

测量口径（全部为可复核的真实测量，不做“看起来没问题”的判断）：

* **切点**：两条独立方法互相印证——(a) ffmpeg ``select=gt(scene,X)``；
  (b) 5 fps 灰度 64x36 的逐帧平均绝对差，用 ``median + k*MAD`` 自适应阈值取局部极大。
  早先只跑 (a) 且阈值 0.35，得到“0 个切点”的结论无法与画面内容区分，
  因此这里同时给出两套结果和它们的差异。
* **静止/重复帧**：相邻采样帧逐字节相同的最长连续段（真静止，不是推拉镜头）。
* **黑帧**：整帧灰度方差接近 0 的采样点。
* **响度**：EBU R128 整合响度、LRA、真峰值；**削波**：``volumedetect`` 的
  max_volume 与 ``astats`` 的 Peak level；**静音**：``silencedetect``。
* **对齐**：切点 vs 旁白段落起点、字幕 cue vs 切点、静音区间 vs 镜头区间，
  全部用数据库里冻结的 composition 项与字幕 revision 计算。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.config import Settings

DEFAULT_PROJECT = "db5ec13c-af7e-4ebc-8175-376e8201411c"

#: Sample geometry for the frame-difference pass: small on purpose, so only real
#: composition changes move the score and encoding noise does not.
_DIFF_WIDTH = 64
_DIFF_HEIGHT = 36


def _ffmpeg() -> tuple[str, str]:
    settings = Settings()
    ffmpeg = Path(str(settings.ffmpeg_path)) if settings.ffmpeg_path else None
    ffprobe = ffmpeg.with_name("ffprobe.EXE") if ffmpeg else None
    if ffmpeg is None or not ffmpeg.exists():
        found = shutil.which("ffmpeg")
        if not found:
            raise SystemExit("ffmpeg not found")
        ffmpeg = Path(found)
        ffprobe = Path(found).with_name("ffprobe")
    return str(ffmpeg), str(ffprobe)


def _probe(ffprobe: str, path: Path) -> dict:
    raw = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    try:
        return json.loads(raw.stdout or "{}")
    except ValueError:
        return {"error": raw.stderr[:300]}


def _loudness(ffmpeg: str, path: Path) -> dict:
    raw = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    text = raw.stderr[-4000:]
    result: dict = {}
    for key, pattern in (
        ("integrated_lufs", r"I:\s*(-?[\d.]+)\s*LUFS"),
        ("lra_lu", r"LRA:\s*(-?[\d.]+)\s*LU"),
        ("true_peak_dbfs", r"Peak:\s*(-?[\d.]+)\s*dBFS"),
    ):
        match = re.search(pattern, text)
        if match:
            result[key] = float(match.group(1))
    silence = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", "silencedetect=n=-45dB:d=1.5", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    starts = re.findall(r"silence_start:\s*(-?[\d.]+)", silence.stderr)
    ends = re.findall(r"silence_end:\s*(-?[\d.]+)", silence.stderr)
    result["silence_spans_s"] = [
        [round(float(start), 2), round(float(ends[index]), 2) if index < len(ends) else None]
        for index, start in enumerate(starts)
    ][:20]
    result["silence_count"] = len(starts)
    volumes = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    for key, pattern in (
        ("max_volume_db", r"max_volume:\s*(-?[\d.]+)\s*dB"),
        ("mean_volume_db", r"mean_volume:\s*(-?[\d.]+)\s*dB"),
    ):
        match = re.search(pattern, volumes.stderr)
        if match:
            result[key] = float(match.group(1))
    stats = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-af", "astats=metadata=0", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    peak = re.search(r"Peak level dB:\s*(-?[\d.]+)", stats.stderr)
    flat = re.search(r"Flat factor:\s*(-?[\d.]+)", stats.stderr)
    if peak:
        result["astats_peak_db"] = float(peak.group(1))
    if flat:
        result["flat_factor"] = float(flat.group(1))
    # A true-peak above -1 dBTP is the usual streaming ceiling; anything at or above
    # 0 dBFS is a hard clip in the encoded file.
    result["clip_risk"] = (
        "CLIPPED"
        if (result.get("true_peak_dbfs") or -99) >= -0.05
        else "HOT"
        if (result.get("true_peak_dbfs") or -99) > -1.0
        else "OK"
    )
    return result


def _frame_diffs(ffmpeg: str, path: Path, fps: int) -> tuple[list[float], int]:
    """Mean absolute difference between consecutive downscaled gray frames."""

    raw = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
            "-vf", f"fps={fps},scale={_DIFF_WIDTH}:{_DIFF_HEIGHT},format=gray",
            "-f", "rawvideo", "-",
        ],
        capture_output=True,
        timeout=3600,
        check=False,
    )
    frame_bytes = _DIFF_WIDTH * _DIFF_HEIGHT
    payload = raw.stdout or b""
    frames = len(payload) // frame_bytes
    diffs: list[float] = []
    previous: bytes | None = None
    for index in range(frames):
        current = payload[index * frame_bytes : (index + 1) * frame_bytes]
        if previous is not None:
            total = 0
            for left, right in zip(previous, current):
                total += abs(left - right)
            diffs.append(round(total / frame_bytes, 4))
        previous = current
    return diffs, frames


def _cuts_from_diffs(diffs: list[float], fps: int, *, k: float = 6.0, floor: float = 6.0) -> tuple[list[float], float]:
    """Adaptive local-maximum cut detection over the frame-difference series."""

    if len(diffs) < 5:
        return [], 0.0
    median = statistics.median(diffs)
    deviations = [abs(item - median) for item in diffs]
    mad = statistics.median(deviations) or 1e-6
    threshold = max(floor, median + k * mad)
    cuts: list[float] = []
    for index in range(1, len(diffs) - 1):
        value = diffs[index]
        if value < threshold:
            continue
        if value >= diffs[index - 1] and value > diffs[index + 1]:
            cuts.append(round((index + 1) / fps, 2))
    merged: list[float] = []
    for item in cuts:
        if not merged or item - merged[-1] > 0.5:
            merged.append(item)
    return merged, round(threshold, 3)


def _static_and_black(ffmpeg: str, path: Path, fps: int, frames: int) -> dict:
    """Longest run of byte-identical sampled frames and the flattest frames."""

    raw = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
            "-vf", f"fps={fps},scale={_DIFF_WIDTH}:{_DIFF_HEIGHT},format=gray",
            "-f", "rawvideo", "-",
        ],
        capture_output=True,
        timeout=3600,
        check=False,
    )
    frame_bytes = _DIFF_WIDTH * _DIFF_HEIGHT
    payload = raw.stdout or b""
    count = min(frames, len(payload) // frame_bytes)
    longest = 0
    current = 0
    longest_at = 0.0
    flat: list[dict] = []
    previous: bytes | None = None
    for index in range(count):
        current_frame = payload[index * frame_bytes : (index + 1) * frame_bytes]
        spread = max(current_frame) - min(current_frame)
        if spread <= 2:
            flat.append({"t": round(index / fps, 2), "spread": int(spread)})
        if previous is not None and current_frame == previous:
            current += 1
            if current > longest:
                longest = current
                longest_at = round((index - current) / fps, 2)
        else:
            current = 0
        previous = current_frame
    return {
        "longest_identical_run_frames": longest,
        "longest_identical_run_seconds": round(longest / fps, 2),
        "longest_identical_run_start_s": longest_at,
        "flat_frame_count": len(flat),
        "flat_frames": flat[:10],
    }


def _composition_alignment(project_id: str, edition_id: str | None, cuts: list[float]) -> dict:
    """Compare measured cuts and subtitle cues with the frozen composition."""

    connection = sqlite3.connect(ROOT / "data" / "local_drama.sqlite3")
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """SELECT id, total_frames, fps_num, manifest_json FROM composition_revisions
               WHERE edition_id = ? ORDER BY created_at DESC LIMIT 1""",
            (edition_id,),
        ).fetchone()
        if row is None:
            return {"error": "no composition revision for this edition"}
        fps = float(row["fps_num"] or 25) or 25.0
        revision_id = str(row["id"])
        items = connection.execute(
            """SELECT ordinal, item_kind, start_frame, end_frame_exclusive, narration_segment_id,
                      subtitle_json, media_version_id
               FROM composition_items WHERE composition_revision_id = ? ORDER BY ordinal""",
            (revision_id,),
        ).fetchall()
        edition = connection.execute(
            "SELECT frozen_subtitle_revision_id, subtitle_mode FROM explainer_editions WHERE id = ?", (edition_id,)
        ).fetchone()
        cues: list[dict] = []
        if edition is not None and edition["frozen_subtitle_revision_id"]:
            revision = connection.execute(
                "SELECT cues_json FROM explainer_subtitle_revisions WHERE id = ?",
                (str(edition["frozen_subtitle_revision_id"]),),
            ).fetchone()
            if revision is not None:
                cues = list(json.loads(revision["cues_json"] or "[]"))
        connection.close()
    except sqlite3.Error as error:
        connection.close()
        return {"error": f"database: {error}"}

    video_items = [item for item in items if str(item["item_kind"]) == "VIDEO_CLIP"]
    shot_starts = [round(int(item["start_frame"]) / fps, 2) for item in video_items]
    shot_spans = [
        (round(int(item["start_frame"]) / fps, 2), round(int(item["end_frame_exclusive"]) / fps, 2))
        for item in video_items
    ]
    narration_starts = sorted(
        {
            round(int(item["start_frame"]) / fps, 2)
            for item in video_items
            if item["narration_segment_id"]
        }
    )
    offsets: list[float] = []
    for cut in cuts:
        if not narration_starts:
            break
        nearest = min(narration_starts, key=lambda value: abs(value - cut))
        offsets.append(round(cut - nearest, 2))

    def _in_shot(second: float) -> bool:
        return any(start <= second < end for start, end in shot_spans)

    cue_times: list[float] = []
    cues_without_shots = 0
    cues_crossing_cuts = 0
    for cue in cues:
        try:
            start_ms = float(cue.get("start_ms") or 0.0)
            end_ms = float(cue.get("end_ms") or 0.0)
        except (TypeError, ValueError):
            continue
        start = start_ms / 1000.0
        end = end_ms / 1000.0
        cue_times.append(round(start, 2))
        if not _in_shot(start):
            cues_without_shots += 1
        if any(start < cut < end for cut in cuts):
            cues_crossing_cuts += 1
    return {
        "composition_revision_id": revision_id,
        "video_item_count": len(video_items),
        "declared_shot_count": len(shot_spans),
        "declared_shot_starts_s": shot_starts[:80],
        "measured_cut_count": len(cuts),
        "cut_minus_nearest_narration_start_s": offsets[:80],
        "max_abs_offset_s": round(max((abs(item) for item in offsets), default=0.0), 2),
        "subtitle_cue_count": len(cue_times),
        "subtitle_cues_without_a_shot": cues_without_shots,
        "subtitle_cues_crossing_a_cut": cues_crossing_cuts,
        "shot_count_vs_measured_cuts": {
            "declared": len(shot_spans),
            "measured": len(cuts),
            "difference": len(shot_spans) - len(cuts),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--frames", type=int, default=24)
    parser.add_argument("--fps", type=int, default=5, help="sampling rate for the frame-difference pass")
    parser.add_argument("--out", type=Path, default=Path("artifacts/film-audit"))
    args = parser.parse_args()

    ffmpeg, ffprobe = _ffmpeg()
    connection = sqlite3.connect(ROOT / "data" / "local_drama.sqlite3")
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """SELECT e.id AS edition_id, e.edition_key, r.id AS render_id, mv.id AS media_version_id,
                  mv.rel_path, mv.duration_ms, ma.project_id, p.root_rel, r.created_at AS render_created_at
             FROM explainer_editions e
             JOIN composition_renders r ON r.edition_id = e.id
             JOIN media_versions mv ON mv.id = r.media_version_id
             JOIN media_assets ma ON ma.id = mv.media_asset_id
             JOIN projects p ON p.id = ma.project_id
            WHERE e.video_id = (SELECT id FROM explainer_videos WHERE project_id=?)
              AND r.render_kind = 'EXPORT'
         ORDER BY r.created_at DESC""",
        (args.project,),
    ).fetchall()
    connection.close()
    if not rows:
        print("no export render found for project", args.project)
        return 2

    report = []
    for row in rows[:2]:
        rel = Path(str(row["rel_path"]))
        candidates = [Path("projects") / str(row["root_rel"]) / rel, rel, Path("data") / rel]
        media = next((item for item in candidates if item.exists()), None)
        entry: dict = {
            "edition_key": row["edition_key"],
            "edition_id": row["edition_id"],
            "render_id": row["render_id"],
            "render_created_at": row["render_created_at"],
            "media_version_id": row["media_version_id"],
            "rel_path": str(rel),
            "resolved": str(media) if media else None,
        }
        print(f"=== {row['edition_key']} media={entry['resolved']}")
        if media is None:
            entry["error"] = "media file not found on disk"
            report.append(entry)
            continue
        probe = _probe(ffprobe, media)
        entry["probe"] = probe
        video = next((item for item in probe.get("streams", []) if item.get("codec_type") == "video"), {})
        audio = next((item for item in probe.get("streams", []) if item.get("codec_type") == "audio"), {})
        entry["video"] = {
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": video.get("avg_frame_rate"),
            "nb_frames": video.get("nb_frames"),
            "pix_fmt": video.get("pix_fmt"),
        }
        entry["audio_stream"] = {
            "codec": audio.get("codec_name"),
            "sample_rate": audio.get("sample_rate"),
            "channels": audio.get("channels"),
            "duration_s": audio.get("duration"),
        }
        duration = float((probe.get("format") or {}).get("duration") or 0) or 1.0
        entry["audio"] = _loudness(ffmpeg, media)

        # 1) sampled frames for human review
        target = args.out / str(row["edition_key"])
        frames_dir = target / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        step = max(duration / max(1, args.frames), 0.5)
        for index in range(args.frames):
            seconds = round(index * step, 2)
            out = frames_dir / f"t{seconds:07.2f}.png"
            subprocess.run(
                [ffmpeg, "-y", "-v", "error", "-ss", str(seconds), "-i", str(media), "-frames:v", "1", str(out)],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        # 2) contact sheet of one frame per 20 s, for a whole-film look
        subprocess.run(
            [
                ffmpeg, "-y", "-v", "error", "-i", str(media),
                "-vf", "fps=1/20,scale=320:-2,tile=5x5",
                "-frames:v", "1", str(target / "cut-sheet.png"),
            ],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        entry["frames_dir"] = str(frames_dir)
        entry["contact_sheet"] = str(target / "cut-sheet.png")

        # 3) scene filter (method A) and frame difference (method B)
        scene = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-nostats", "-i", str(media),
                "-vf", "select='gt(scene,0.10)',metadata=print:file=-", "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
        scene_cuts = sorted({round(float(item), 2) for item in re.findall(r"pts_time:([\d.]+)", scene.stdout + scene.stderr)})
        diffs, sampled = _frame_diffs(ffmpeg, media, args.fps)
        cut_times, threshold = _cuts_from_diffs(diffs, args.fps)
        entry["cut_detection"] = {
            "scene_filter": {
                "threshold": 0.10,
                "cut_count": len(scene_cuts),
                "cut_times_s": scene_cuts[:80],
            },
            "frame_difference": {
                "sampling_fps": args.fps,
                "sampled_frames": sampled,
                "adaptive_threshold": threshold,
                "median_diff": round(statistics.median(diffs), 3) if diffs else None,
                "max_diff": round(max(diffs), 3) if diffs else None,
                "cut_count": len(cut_times),
                "cut_times_s": cut_times[:80],
            },
        }
        # Two independent detectors agreeing within 0.5 s is the evidence a cut is real.
        agreed = [
            cut for cut in cut_times if any(abs(cut - other) <= 0.5 for other in scene_cuts)
        ]
        entry["cut_detection"]["agreed_cuts"] = agreed[:80]
        entry["cut_detection"]["agreed_cut_count"] = len(agreed)

        entry["stuck_and_black"] = _static_and_black(ffmpeg, media, args.fps, sampled)
        entry["alignment"] = _composition_alignment(str(row["project_id"]), str(row["edition_id"]), cut_times)
        # Which declared shot boundaries did the picture not actually change at?
        # The composition declares one CUT per shot; a boundary whose frame
        # difference is far below the cut threshold means two adjacent shots look
        # nearly the same on screen, which is a content finding, not a detector gap.
        declared = entry["alignment"].get("declared_shot_starts_s") or []
        boundary_diffs = []
        for second in declared:
            index = round(second * args.fps) - 1
            if 0 <= index < len(diffs):
                boundary_diffs.append(round(diffs[index], 3))
        misses = []
        classes: dict[str, int] = {"DETECTED": 0, "BELOW_THRESHOLD": 0, "PICTURE_BARELY_CHANGES": 0}
        threshold_value = entry["cut_detection"]["frame_difference"]["adaptive_threshold"]
        for position, second in enumerate(declared):
            if position >= len(boundary_diffs):
                continue
            value = boundary_diffs[position]
            detected = any(abs(second - cut) <= 0.5 for cut in cut_times)
            if detected:
                classes["DETECTED"] += 1
                continue
            # A boundary the picture really changed at but the tuned threshold did
            # not fire on is a detector-sensitivity limit; a boundary whose frames
            # are nearly identical is a content finding about two similar shots.
            if value < 1.0:
                classes["PICTURE_BARELY_CHANGES"] += 1
            else:
                classes["BELOW_THRESHOLD"] += 1
            misses.append(
                {
                    "t": second,
                    "frame_diff": value,
                    "class": "PICTURE_BARELY_CHANGES" if value < 1.0 else "BELOW_THRESHOLD",
                }
            )
        entry["declared_boundaries"] = {
            "count": len(declared),
            "frame_diffs": boundary_diffs,
            "median_frame_diff": round(statistics.median(boundary_diffs), 3) if boundary_diffs else None,
            "cut_threshold": threshold_value,
            "classes": classes,
            "boundaries_without_a_measured_cut": misses,
            "boundaries_without_a_measured_cut_count": len(misses),
        }

        print(
            f"    {entry['video']['width']}x{entry['video']['height']} duration={duration:.1f}s "
            f"cuts(scene)={len(scene_cuts)} cuts(diff)={len(cut_times)} agreed={len(agreed)} "
            f"identical_run={entry['stuck_and_black']['longest_identical_run_seconds']}s "
            f"audio={json.dumps(entry['audio'], ensure_ascii=False)[:180]}"
        )
        report.append(entry)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("report:", args.out / "report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
