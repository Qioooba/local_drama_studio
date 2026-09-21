from __future__ import annotations

import hashlib
import hmac
import json
import re
import shutil
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from local_drama.api.schemas.video_upscale import UpscaleModelOptions, UpscalePipelineOptions
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.application.video_upscale.geometry import resolve_sdr_color_pipeline, resolve_upscale_geometry
from local_drama.application.video_upscale.sources import EpisodeDeliverySourceService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.atomic import write_atomic
from local_drama.infrastructure.filesystem.path_policy import controlled_path
from local_drama.model_platform.application.capability_resolution import CapabilityScopeContext
from local_drama.model_platform.application.execution_planning import ExecutionPlanningService, ExecutionPreviewRequest
from local_drama.model_platform.application.production_execution_registry import production_execution_handlers


def _now() -> datetime:
    return datetime.now(UTC)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _deep_merge(base: dict[str, Any], patch: dict[str, object]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _merge_model_options(base: dict[str, Any], patch: dict[str, object]) -> dict[str, Any]:
    merged = _deep_merge(base, patch)
    if "tile_size" in patch and "tile_fallback_sizes" not in patch:
        merged.pop("tile_fallback_sizes", None)
    return merged


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _video_stream(probe: dict[str, Any]) -> dict[str, Any]:
    streams = probe.get("streams")
    if not isinstance(streams, list):
        return {}
    return next(
        (dict(stream) for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"),
        {},
    )


def _cfr_timestamp_evidence(ffprobe: Path, source: Path, fps: str) -> dict[str, Any]:
    try:
        rate = Fraction(fps)
    except (ValueError, ZeroDivisionError) as error:
        raise DomainRuleError("UPSCALE_SOURCE_FRAME_RATE_INVALID", "源视频帧率证据无效") from error
    if rate <= 0:
        raise DomainRuleError("UPSCALE_SOURCE_FRAME_RATE_INVALID", "源视频帧率证据无效")
    try:
        completed = subprocess.run(
            [
                str(ffprobe),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "frame=best_effort_timestamp_time",
                "-of",
                "csv=p=0",
                str(source),
            ],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DomainRuleError("UPSCALE_SOURCE_PTS_PROBE_FAILED", "无法逐帧验证源视频时间戳") from error
    if completed.returncode != 0:
        raise DomainRuleError("UPSCALE_SOURCE_PTS_PROBE_FAILED", "无法逐帧验证源视频时间戳")
    timestamps: list[float] = []
    for line in completed.stdout.splitlines():
        value = line.strip().split(",", 1)[0]
        if not value:
            continue
        try:
            timestamps.append(float(value))
        except ValueError:
            continue
    if not timestamps:
        raise DomainRuleError("UPSCALE_SOURCE_PTS_PROBE_FAILED", "源视频没有可验证的逐帧时间戳")
    expected_step = float(1 / rate)
    tolerance = max(0.001, expected_step * 0.03)
    deltas = [right - left for left, right in zip(timestamps, timestamps[1:], strict=False)]
    invalid = [delta for delta in deltas if delta <= 0 or abs(delta - expected_step) > tolerance]
    return {
        "frame_count": len(timestamps),
        "first_pts": timestamps[0],
        "last_pts": timestamps[-1],
        "expected_step": expected_step,
        "tolerance": tolerance,
        "min_step": min(deltas) if deltas else None,
        "max_step": max(deltas) if deltas else None,
        "constant": not invalid,
        "irregular_step_count": len(invalid),
    }


def _border_evidence(ffmpeg: Path, source: Path, width: int, height: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                str(ffmpeg),
                "-hide_banner",
                "-v",
                "info",
                "-i",
                str(source),
                "-t",
                "30",
                "-vf",
                "fps=1,cropdetect=24:16:0",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "UNAVAILABLE", "reason": "CROPDETECT_FAILED"}
    crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", completed.stderr)
    if not crops:
        return {"status": "INSUFFICIENT_EVIDENCE", "sample_count": 0}
    counts: dict[tuple[int, int, int, int], int] = {}
    for raw in crops:
        crop = (int(raw[0]), int(raw[1]), int(raw[2]), int(raw[3]))
        counts[crop] = counts.get(crop, 0) + 1
    content, count = max(counts.items(), key=lambda value: (value[1], value[0]))
    content_width, content_height, x, y = content
    area_ratio = content_width * content_height / max(1, width * height)
    stability = count / len(crops)
    return {
        "status": "DETECTED" if len(crops) >= 3 and stability >= 0.7 else "INSUFFICIENT_EVIDENCE",
        "sample_count": len(crops),
        "stable_sample_count": count,
        "stability": round(stability, 4),
        "content_rect": {"x": x, "y": y, "width": content_width, "height": content_height},
        "content_area_ratio": round(area_ratio, 4),
        "large_borders": len(crops) >= 3 and stability >= 0.7 and area_ratio < 0.75,
        "auto_crop_applied": False,
    }


class VideoUpscalePlanService:
    def __init__(
        self,
        database: DatabaseUnitOfWork,
        settings: Settings,
        *,
        jobs_factory: Callable[[Any, Settings], JobService] = JobService,
        sources_factory: Callable[[Any], EpisodeDeliverySourceService] = EpisodeDeliverySourceService,
        media_factory: Callable[[Any, Settings], MediaService] = MediaService,
        planning_factory: Callable[[Any], ExecutionPlanningService] = ExecutionPlanningService,
    ) -> None:
        self.database = database
        self.settings = settings
        self.jobs = jobs_factory(database, settings)
        self.sources = sources_factory(database)
        self._media_factory = media_factory
        self._planning_factory = planning_factory

    def create(
        self,
        project_id: str,
        *,
        selection_hash: str,
        episode_ids: list[str],
        preset_version_id: str,
        execution_profile_version_id: str | None,
        batch_pipeline_overrides: dict[str, object] | None = None,
        batch_model_overrides: dict[str, object] | None = None,
        item_overrides: list[dict[str, object]] | None = None,
        existing_result_policy: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            preset = connection.execute(
                """SELECT pv.*,p.project_id,p.status AS preset_status
                FROM video_upscale_preset_versions pv JOIN video_upscale_presets p ON p.id=pv.preset_id
                WHERE pv.id=? AND (p.project_id IS NULL OR p.project_id=?)""",
                (preset_version_id, project_id),
            ).fetchone()
            if preset is None or str(preset["preset_status"]) != "ACTIVE":
                raise DomainRuleError("UPSCALE_PRESET_VERSION_NOT_FOUND", "超分预设版本不存在或不可用")
            pipeline_value = json.loads(str(preset["pipeline_options_json"]))
            model_value = json.loads(str(preset["model_options_json"]))
            settings = connection.execute(
                "SELECT preset_version_id,overrides_json FROM project_upscale_settings WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if settings is not None and str(settings["preset_version_id"]) == preset_version_id:
                project_overrides = json.loads(str(settings["overrides_json"] or "{}"))
                pipeline_value = _deep_merge(pipeline_value, dict(project_overrides.get("pipeline") or {}))
                model_value = _merge_model_options(model_value, dict(project_overrides.get("model") or {}))
            pipeline_value = _deep_merge(pipeline_value, dict(batch_pipeline_overrides or {}))
            model_value = _merge_model_options(model_value, dict(batch_model_overrides or {}))
            try:
                pipeline = UpscalePipelineOptions.model_validate(pipeline_value)
                model = UpscaleModelOptions.model_validate(model_value)
            except ValueError as error:
                raise DomainRuleError(
                    "UPSCALE_PARAMETER_UNSUPPORTED",
                    "本批超分参数不符合当前合同",
                    {"validation_error": str(error)},
                ) from error
            selection = self.sources.resolve_selection(
                project_id,
                mode="EXPLICIT",
                episode_ids=episode_ids,
                search=None,
                season_id=None,
                source_policy=pipeline.source_policy,
            )
            if not hmac.compare_digest(str(selection["selection_hash"]), selection_hash):
                raise DomainRuleError(
                    "UPSCALE_SELECTION_STALE",
                    "分集来源选择或来源策略已变化，请重新确认",
                    {"expected": selection_hash, "actual": selection["selection_hash"]},
                )
            if selection["blocked"] or int(selection["count"]) != len(episode_ids):
                raise DomainRuleError(
                    "UPSCALE_SELECTION_BLOCKED",
                    "所选分集中存在不可处理的来源",
                    {"blocked": selection["blocked"]},
                )
            override_map: dict[str, dict[str, object]] = {}
            for raw_override in item_overrides or []:
                extra_keys = set(raw_override) - {"episode_id", "pipeline", "model"}
                if extra_keys:
                    raise DomainRuleError(
                        "UPSCALE_PARAMETER_UNSUPPORTED",
                        "逐集覆盖包含不支持的字段",
                        {"fields": sorted(extra_keys)},
                    )
                episode_id = str(raw_override.get("episode_id") or "")
                if episode_id not in episode_ids or episode_id in override_map:
                    raise DomainRuleError("UPSCALE_ITEM_OVERRIDE_INVALID", "逐集覆盖必须唯一且属于本次选择")
                override_map[episode_id] = raw_override
            profile = None
            resolved_profile_id = execution_profile_version_id or preset["profile_version_id"]
            if resolved_profile_id:
                profile = connection.execute(
                    """SELECT version.id,version.payload_hash,runtime.adapter_code,runtime.adapter_version,
                    runtime.fingerprint AS runtime_fingerprint,runtime.status AS runtime_status,
                    publication.status AS publication_status,version.payload_json
                    FROM mp_execution_profile_versions version
                    JOIN mp_capability_definitions capability ON capability.id=version.capability_definition_id
                    JOIN mp_profile_publications publication ON publication.execution_profile_version_id=version.id
                    JOIN mp_runtime_installation_versions runtime ON runtime.id=version.runtime_installation_version_id
                    WHERE version.id=? AND capability.code='UPSCALE_VIDEO'""",
                    (resolved_profile_id,),
                ).fetchone()
                if profile is None or str(profile["publication_status"]) != "PUBLISHED":
                    raise DomainRuleError("UPSCALE_PROFILE_NOT_PUBLISHED", "所选视频超分 Profile 未发布")
                if str(profile["adapter_code"]) != str(model.adapter_code):
                    raise DomainRuleError(
                        "UPSCALE_PROFILE_ADAPTER_MISMATCH",
                        "预设模型与执行 Profile 的适配器不一致",
                    )
                profile_payload: object = None
                try:
                    profile_payload = json.loads(str(profile["payload_json"]))
                    verified_model_name = profile_payload.get("model_name") if isinstance(profile_payload, dict) else None
                    verified_scales = profile_payload.get("verified_native_scales") if isinstance(profile_payload, dict) else None
                except (TypeError, ValueError):
                    verified_model_name, verified_scales = None, None
                if isinstance(profile_payload, dict) and profile_payload.get("template") == "ncnn.video-upscale.profile.v1":
                    if verified_model_name != model.model_name or not isinstance(verified_scales, list) or not verified_scales:
                        raise DomainRuleError(
                            "UPSCALE_PROFILE_MODEL_MISMATCH",
                            "所选 Profile 没有验证当前 Real-ESRGAN 模型与倍率。",
                        )
                    try:
                        model = model.model_copy(update={"native_scales": [int(value) for value in verified_scales]})
                    except (TypeError, ValueError) as error:
                        raise DomainRuleError("UPSCALE_PROFILE_MODEL_MISMATCH", "Profile 的已验证倍率合同无效。") from error
        request_snapshot = {
            "schema_version": "localdrama.video-upscale-plan-request.v1",
            "project_id": project_id,
            "selection_hash": selection_hash,
            "selection": selection["items"],
            "preset_version_id": preset_version_id,
            "preset_content_hash": str(preset["content_hash"]),
            "execution_profile_version_id": str(resolved_profile_id) if resolved_profile_id else None,
            "profile_payload_hash": str(profile["payload_hash"]) if profile else None,
            "profile_runtime_status": str(profile["runtime_status"]) if profile else None,
            "profile_adapter_code": str(profile["adapter_code"]) if profile else None,
            "pipeline_options": pipeline.model_dump(mode="json"),
            "model_options": model.model_dump(mode="json"),
            "item_overrides": override_map,
            "existing_result_policy": existing_result_policy,
        }
        request_hash = _hash(request_snapshot)
        plan_id = str(uuid.uuid4())
        now = _now()
        expires_at = now + timedelta(minutes=30)
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT id,check_job_id FROM video_upscale_plans WHERE project_id=? AND request_hash=?",
                (project_id, request_hash),
            ).fetchone()
            if existing is not None:
                return {
                    "plan": self.get(str(existing["id"])),
                    "job": self.jobs.get_job(str(existing["check_job_id"])),
                    "idempotent_replay": True,
                }
            connection.execute(
                """INSERT INTO video_upscale_plans
                (id,project_id,status,request_hash,selection_snapshot_json,check_job_id,items_json,
                 plan_hash,expires_at,supersedes_id,error_code,error_detail,
                 created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?, 'CHECKING',?,?,NULL,'[]',NULL,?,NULL,NULL,NULL,?,?,?,1,'video-upscale-plan.v1')""",
                (
                    plan_id,
                    project_id,
                    request_hash,
                    _json(request_snapshot),
                    expires_at.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    actor,
                ),
            )
            job = self.jobs.create_job_in_transaction(
                connection,
                project_id,
                "VIDEO_UPSCALE_PREFLIGHT",
                "VIDEO_UPSCALE_PLAN",
                plan_id,
                "CPU",
                {
                    "schema_version": "localdrama.video-upscale-preflight-job.v1",
                    "plan_id": plan_id,
                    "request_hash": request_hash,
                    "local_only": True,
                    "network_contacted": False,
                },
                f"video-upscale-preflight:{project_id}:{request_hash}",
                priority=25,
                max_attempts=2,
                actor=actor,
                subject_kind="VIDEO_UPSCALE_PLAN",
                scope_kind="PROJECT",
                scope_project_id=project_id,
                stage_code="VIDEO_UPSCALE_PREFLIGHT",
            )
            connection.execute(
                "UPDATE video_upscale_plans SET check_job_id=? WHERE id=?",
                (job["id"], plan_id),
            )
        return {"plan": self.get(plan_id), "job": job, "idempotent_replay": False}

    def get(self, plan_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM video_upscale_plans WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            raise DomainRuleError("UPSCALE_PLAN_NOT_FOUND", "视频超分预检计划不存在", {"plan_id": plan_id})
        item = dict(row)
        item["selection_snapshot"] = json.loads(str(item.pop("selection_snapshot_json")))
        item["items"] = json.loads(str(item.pop("items_json") or "[]"))
        if str(item["status"]) == "READY" and datetime.fromisoformat(str(item["expires_at"])) <= _now():
            item["status"] = "EXPIRED"
        if item.get("check_job_id"):
            item["check_job"] = self.jobs.get_job(str(item["check_job_id"]))
        return item

    def run_preflight(self, plan_id: str, output_root: Path) -> tuple[str, str]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT plan.*,project.root_rel FROM video_upscale_plans plan
                JOIN projects project ON project.id=plan.project_id WHERE plan.id=?""",
                (plan_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("UPSCALE_PLAN_NOT_FOUND", "视频超分预检计划不存在")
        if str(row["status"]) != "CHECKING":
            receipt = output_root / "video-upscale-preflight.json"
            write_atomic(receipt, lambda target: target.write_text(_json({"plan_id": plan_id, "status": row["status"]}), encoding="utf-8"))
            return "VIDEO_UPSCALE_PREFLIGHT", receipt.relative_to(self.settings.work_root).as_posix()
        snapshot = json.loads(str(row["selection_snapshot_json"]))
        pipeline = UpscalePipelineOptions.model_validate(snapshot["pipeline_options"])
        model = UpscaleModelOptions.model_validate(snapshot["model_options"])
        project_root = self.settings.resolve_project_root(str(row["root_rel"]))
        media = self._media_factory(self.database, self.settings)
        planning = self._planning_factory(self.database)
        handlers = production_execution_handlers()
        result_items: list[dict[str, Any]] = []
        for ordinal, selected in enumerate(snapshot["selection"], start=1):
            raw_override = dict(snapshot.get("item_overrides", {}).get(str(selected["episode_id"]), {}))
            try:
                item_pipeline = UpscalePipelineOptions.model_validate(
                    _deep_merge(pipeline.model_dump(mode="json"), dict(raw_override.get("pipeline") or {}))
                )
                item_model = UpscaleModelOptions.model_validate(
                    _merge_model_options(model.model_dump(mode="json"), dict(raw_override.get("model") or {}))
                )
            except ValueError as error:
                raise DomainRuleError(
                    "UPSCALE_PARAMETER_UNSUPPORTED",
                    "逐集超分覆盖不符合当前参数合同",
                    {"episode_id": selected["episode_id"], "validation_error": str(error)},
                ) from error
            source = dict(selected["source"])
            blockers: list[dict[str, Any]] = []
            warnings: list[dict[str, Any]] = []
            actual_sha256: str | None = None
            probe: dict[str, Any] = {}
            source_path: Path | None = None
            try:
                source_path = controlled_path(
                    project_root,
                    str(source["rel_path"]),
                    must_exist=True,
                    require_file=True,
                    code="UPSCALE_SOURCE_FILE_MISSING",
                )
                actual_sha256 = _file_hash(source_path)
                if not hmac.compare_digest(actual_sha256, str(source["source_sha256"])):
                    blockers.append({"code": "UPSCALE_SOURCE_HASH_CHANGED", "message": "源文件内容已变化"})
                probe = media.probe_output(source_path, "VIDEO")
                if probe.get("probe_status") in {"BLOCKED", "FAIL"}:
                    blockers.append({"code": "UPSCALE_SOURCE_PROBE_FAILED", "message": "源视频无法完成技术探测"})
            except DomainRuleError as error:
                blockers.append({"code": error.code, "message": error.message})
            stream = _video_stream(probe)
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
            geometry = None
            border_evidence: dict[str, Any] | None = None
            if width <= 0 or height <= 0:
                blockers.append({"code": "UPSCALE_SOURCE_GEOMETRY_INVALID", "message": "源视频缺少有效宽高"})
            else:
                try:
                    geometry = resolve_upscale_geometry(
                        source_width=width,
                        source_height=height,
                        target_mode=item_pipeline.target.mode,
                        target_width=item_pipeline.target.width,
                        target_height=item_pipeline.target.height,
                        fit=item_pipeline.target.fit,
                        allow_cross_orientation=item_pipeline.target.allow_cross_orientation,
                        native_scales=tuple(item_model.native_scales),
                        explicit_native_scale=item_pipeline.native_scale,
                    )
                    geometry["color_pipeline"] = resolve_sdr_color_pipeline(stream)
                    inferred_color = list(geometry["color_pipeline"]["inferred"])
                    if inferred_color:
                        warnings.append(
                            {
                                "code": "UPSCALE_INPUT_COLOR_INFERRED",
                                "message": "源视频缺少部分 SDR 色彩元数据；已冻结可审计的 SD/HD 推断并转换为 BT.709 limited",
                                "details": {"inferred": inferred_color, "color_pipeline": geometry["color_pipeline"]},
                            }
                        )
                except DomainRuleError as error:
                    blockers.append({"code": error.code, "message": error.message, "details": error.details})
                if source_path is not None and self.settings.ffmpeg_path:
                    border_evidence = _border_evidence(Path(self.settings.ffmpeg_path), source_path, width, height)
                    if border_evidence.get("large_borders"):
                        warnings.append(
                            {
                                "code": "SOURCE_LARGE_BORDERS",
                                "message": "源视频检测到大面积稳定黑边；系统不会自动裁切，请先核对构图",
                                "details": border_evidence,
                            }
                        )
            if not snapshot.get("execution_profile_version_id"):
                blockers.append({"code": "UPSCALE_PROFILE_REQUIRED", "message": "尚未配置已发布的视频超分执行 Profile"})
            else:
                try:
                    preview = planning.preview(
                        ExecutionPreviewRequest(
                            capability_code="UPSCALE_VIDEO",
                            scope=CapabilityScopeContext(
                                project_id=str(row["project_id"]),
                                episode_id=str(selected["episode_id"]),
                            ),
                            semantic_inputs={"upscale_plan_id": plan_id},
                            run_overrides={
                                key: item_model.model_dump(mode="json")[key]
                                for key in ("tile_size", "tta", "load_threads", "proc_threads", "save_threads")
                            },
                            execution_profile_version_id=str(snapshot["execution_profile_version_id"]),
                        )
                    )
                    if not preview.executable or preview.adapter_code is None:
                        blockers.append({"code": "UPSCALE_PROFILE_NOT_READY", "message": "视频超分执行 Profile 当前不可运行", "details": {"blockers": list(preview.blockers)}})
                    else:
                        handlers.resolve("UPSCALE_VIDEO", preview.adapter_code)
                except DomainRuleError as error:
                    blockers.append({"code": error.code, "message": error.message, "details": error.details})
            avg_rate = str(stream.get("avg_frame_rate") or "")
            real_rate = str(stream.get("r_frame_rate") or "")
            cfr_evidence: dict[str, Any] | None = None
            if not avg_rate or not real_rate:
                blockers.append({"code": "UPSCALE_SOURCE_FRAME_RATE_MISSING", "message": "源视频缺少帧率证据"})
            elif source_path is not None and self.settings.ffprobe_path:
                try:
                    cfr_evidence = _cfr_timestamp_evidence(Path(self.settings.ffprobe_path), source_path, avg_rate)
                    if not cfr_evidence["constant"]:
                        blockers.append(
                            {
                                "code": "UPSCALE_INPUT_VFR",
                                "message": "首发不支持可变帧率视频；逐帧时间戳步长不恒定",
                                "details": cfr_evidence,
                            }
                        )
                except DomainRuleError as error:
                    blockers.append({"code": error.code, "message": error.message})
            field_order = str(stream.get("field_order") or "unknown").lower()
            if field_order != "progressive":
                blockers.append(
                    {
                        "code": "UPSCALE_INPUT_INTERLACED" if field_order not in {"unknown", ""} else "UPSCALE_INPUT_SCAN_UNKNOWN",
                        "message": "首发只支持有明确 progressive 证据的逐行扫描视频",
                    }
                )
            transfer = str(stream.get("color_transfer") or "").lower()
            if (
                int(stream.get("bits_per_raw_sample") or 8) > 8
                or str(stream.get("pix_fmt") or "").endswith(("10le", "12le"))
                or transfer in {"smpte2084", "arib-std-b67"}
            ):
                blockers.append({"code": "UPSCALE_INPUT_HDR", "message": "首发只支持 SDR 8bit 输入"})
            sample_aspect_ratio = str(stream.get("sample_aspect_ratio") or "")
            if sample_aspect_ratio not in {"", "1:1", "0:1"}:
                blockers.append({"code": "UPSCALE_INPUT_SAR", "message": "首发只支持方形像素（SAR 1:1）输入"})
            rotation_values = [stream.get("tags", {}).get("rotate") if isinstance(stream.get("tags"), dict) else None]
            rotation_values.extend(
                side.get("rotation")
                for side in stream.get("side_data_list", [])
                if isinstance(side, dict) and side.get("rotation") is not None
            )
            try:
                has_rotation = any(int(float(value)) % 360 != 0 for value in rotation_values if value is not None)
            except (TypeError, ValueError):
                has_rotation = True
            if has_rotation:
                blockers.append({"code": "UPSCALE_INPUT_ROTATION", "message": "首发不支持带非零旋转元数据的视频"})
            video_streams = [value for value in probe.get("streams", []) if value.get("codec_type") == "video"]
            if len(video_streams) != 1:
                blockers.append({"code": "UPSCALE_INPUT_MULTI_VIDEO", "message": "首发只支持单视频流成片"})
            duration_value = stream.get("duration") or probe.get("format", {}).get("duration")
            try:
                duration_ms = round(float(duration_value) * 1000) if duration_value is not None else 0
            except (TypeError, ValueError):
                duration_ms = 0
            estimated_frames = 0
            if duration_ms > 0 and avg_rate and "/" in avg_rate:
                numerator, denominator = avg_rate.split("/", 1)
                try:
                    estimated_frames = round(duration_ms * int(numerator) / (1000 * int(denominator)))
                except (ValueError, ZeroDivisionError):
                    estimated_frames = 0
            disk = shutil.disk_usage(project_root)
            minimum_free_bytes = max(5 * 1024**3, round(disk.total * 0.05))
            if disk.free < minimum_free_bytes:
                blockers.append(
                    {
                        "code": "UPSCALE_DISK_LOW",
                        "message": "项目磁盘可用空间低于视频超分安全阈值",
                        "details": {"free_bytes": disk.free, "minimum_free_bytes": minimum_free_bytes},
                    }
                )
            result_items.append(
                {
                    "ordinal": ordinal,
                    "episode_id": selected["episode_id"],
                    "disposition": "BLOCKED" if blockers else "NEW",
                    "source": {**source, "actual_sha256": actual_sha256},
                    "probe": probe,
                    "geometry": geometry,
                    "border_evidence": border_evidence,
                    "effective_pipeline_options": item_pipeline.model_dump(mode="json"),
                    "effective_model_options": item_model.model_dump(mode="json"),
                    "frame_count": estimated_frames or None,
                    "cfr_evidence": cfr_evidence,
                    "estimated_work": {
                        "input_frames": estimated_frames or None,
                        "chunk_frames": item_pipeline.chunk_frames,
                        "chunk_count": ((estimated_frames + item_pipeline.chunk_frames - 1) // item_pipeline.chunk_frames) if estimated_frames else None,
                    },
                    "disk_budget": {
                        "free_bytes": disk.free,
                        "minimum_free_bytes": minimum_free_bytes,
                    },
                    "blockers": blockers,
                    "warnings": warnings,
                }
            )
        status = "BLOCKED" if any(item["blockers"] for item in result_items) else "READY"
        result_snapshot = {
            "schema_version": "localdrama.video-upscale-plan.v1",
            "request_hash": str(row["request_hash"]),
            "items": result_items,
            "profile_version_id": snapshot.get("execution_profile_version_id"),
            "preset_version_id": snapshot["preset_version_id"],
        }
        plan_hash = _hash(result_snapshot)
        updated_at = _now().isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE video_upscale_plans SET status=?,items_json=?,plan_hash=?,updated_at=?,revision=revision+1
                WHERE id=? AND status='CHECKING'""",
                (status, _json(result_items), plan_hash, updated_at, plan_id),
            )
        receipt = output_root / "video-upscale-preflight.json"
        write_atomic(
            receipt,
            lambda target: target.write_text(
                json.dumps({**result_snapshot, "plan_id": plan_id, "status": status, "plan_hash": plan_hash}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            ),
        )
        return "VIDEO_UPSCALE_PREFLIGHT", receipt.relative_to(self.settings.work_root).as_posix()
