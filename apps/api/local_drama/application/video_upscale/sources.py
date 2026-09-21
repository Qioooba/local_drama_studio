from __future__ import annotations

import hashlib
import json
from typing import Any

from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.application.video_upscale.geometry import resolve_upscale_geometry
from local_drama.domain.errors import DomainRuleError

_VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm", ".m4v")


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _video_stream(probe_json: str | None) -> dict[str, Any]:
    try:
        probe = json.loads(str(probe_json or "{}"))
    except json.JSONDecodeError:
        return {}
    streams = probe.get("streams") if isinstance(probe, dict) else None
    if not isinstance(streams, list):
        return {}
    return next(
        (dict(stream) for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"),
        {},
    )


def _object_json(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


class EpisodeDeliverySourceService:
    def __init__(self, database: DatabaseUnitOfWork) -> None:
        self.database = database

    def _project_exists(self, connection: Any, project_id: str) -> bool:
        return connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is not None

    def list_episodes(
        self,
        project_id: str,
        *,
        limit: int = 50,
        cursor: int = 0,
        search: str | None = None,
        season_id: str | None = None,
        episode_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 200))
        bounded_cursor = max(0, int(cursor))
        normalized_search = str(search or "").strip()
        selected_ids = list(episode_ids or [])
        if len(selected_ids) > 200:
            raise DomainRuleError("UPSCALE_SELECTION_TOO_LARGE", "单次最多选择 200 集")
        clauses = ["s.project_id=?"]
        params: list[object] = [project_id]
        if season_id:
            clauses.append("s.id=?")
            params.append(season_id)
        if normalized_search:
            clauses.append("(e.code LIKE ? ESCAPE '\\' OR COALESCE(e.title,'') LIKE ? ESCAPE '\\')")
            escaped = normalized_search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.extend((f"%{escaped}%", f"%{escaped}%"))
        if selected_ids:
            placeholders = ",".join("?" for _ in selected_ids)
            clauses.append(f"e.id IN ({placeholders})")
            params.extend(selected_ids)
        where = " AND ".join(clauses)
        with self.database.connect() as connection:
            if not self._project_exists(connection, project_id):
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE {where}",
                    tuple(params),
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""WITH latest_compose AS (
                    SELECT erv.*,
                    ROW_NUMBER() OVER (PARTITION BY erv.episode_id ORDER BY erv.created_at DESC,erv.id DESC) AS rn
                    FROM episode_render_versions erv WHERE erv.render_kind='COMPOSE'
                ), latest_review AS (
                    SELECT rd.*,
                    ROW_NUMBER() OVER (PARTITION BY rd.subject_id ORDER BY rd.created_at DESC,rd.id DESC) AS rn
                    FROM review_decisions rd WHERE rd.subject_type='EPISODE_RENDER_VERSION'
                ), latest_package AS (
                    SELECT dp.*,
                    ROW_NUMBER() OVER (PARTITION BY dp.episode_render_version_id ORDER BY dp.created_at DESC,dp.id DESC) AS rn
                    FROM delivery_packages dp WHERE dp.status='VERIFIED' AND dp.withdrawn_reason IS NULL
                ), derived_counts AS (
                    SELECT episode_id,COUNT(*) AS count FROM episode_render_versions
                    WHERE render_kind='SUPER_RESOLUTION' GROUP BY episode_id
                )
                SELECT e.id AS episode_id,e.code AS episode_code,e.title AS episode_title,e.number AS episode_number,
                e.display_order AS episode_order,s.id AS season_id,s.number AS season_number,s.title AS season_title,
                compose.id AS compose_id,compose.timeline_revision_id,compose.rel_path AS compose_rel_path,
                compose.sha256 AS compose_sha256,compose.probe_json,compose.integrity_status,
                compose.duration_ms,compose.mime_type,compose.revision AS compose_revision,compose.created_at AS compose_created_at,
                compose.input_snapshot_json AS compose_input_snapshot_json,
                review.id AS approval_id,review.decision AS approval_decision,review.is_stale AS approval_stale,
                review.subject_revision AS approval_subject_revision,
                package.id AS delivery_package_id,package.rel_path AS delivery_package_rel_path,
                package.manifest_sha256,package.human_review_status AS delivery_human_review_status,
                package.watermark_profile_id AS delivery_watermark_profile_id,
                package_target.target_spec_json AS delivery_target_spec_json,
                package_watermark.code AS delivery_watermark_code,
                package_watermark.version_no AS delivery_watermark_version_no,
                package_watermark.config_json AS delivery_watermark_config_json,
                COALESCE(derived.count,0) AS derived_count
                FROM episodes e JOIN seasons s ON s.id=e.season_id
                LEFT JOIN latest_compose compose ON compose.episode_id=e.id AND compose.rn=1
                LEFT JOIN latest_review review ON review.subject_id=compose.id AND review.rn=1
                LEFT JOIN latest_package package ON package.episode_render_version_id=compose.id AND package.rn=1
                LEFT JOIN delivery_target_versions package_target ON package_target.id=package.target_version_id
                LEFT JOIN watermark_profiles package_watermark ON package_watermark.id=package.watermark_profile_id
                LEFT JOIN derived_counts derived ON derived.episode_id=e.id
                WHERE {where}
                ORDER BY s.display_order,s.number,s.id,e.display_order,e.number,e.id LIMIT ? OFFSET ?""",
                tuple([*params, bounded_limit, bounded_cursor]),
            ).fetchall()
            package_ids = [str(row["delivery_package_id"]) for row in rows if row["delivery_package_id"]]
            files_by_package: dict[str, list[dict[str, Any]]] = {}
            if package_ids:
                placeholders = ",".join("?" for _ in package_ids)
                files = connection.execute(
                    f"SELECT * FROM delivery_files WHERE delivery_package_id IN ({placeholders}) ORDER BY rel_path,id",
                    tuple(package_ids),
                ).fetchall()
                for file_row in files:
                    files_by_package.setdefault(str(file_row["delivery_package_id"]), []).append(dict(file_row))
            episode_page_ids = [str(row["episode_id"]) for row in rows]
            selections: dict[str, list[dict[str, Any]]] = {}
            if episode_page_ids:
                placeholders = ",".join("?" for _ in episode_page_ids)
                selection_rows = connection.execute(
                    f"""SELECT target_slot,episode_id,selected_render_id,root_compose_render_id,approval_id,revision
                    FROM episode_delivery_selections WHERE episode_id IN ({placeholders}) ORDER BY target_slot""",
                    tuple(episode_page_ids),
                ).fetchall()
                for selection in selection_rows:
                    selections.setdefault(str(selection["episode_id"]), []).append(dict(selection))
        items = [self._project_row(row, files_by_package, selections) for row in rows]
        return {
            "project_id": project_id,
            "items": items,
            "page": {"limit": bounded_limit, "cursor": bounded_cursor, "total": total, "next_cursor": bounded_cursor + len(items) if bounded_cursor + len(items) < total else None},
            "filters": {"search": normalized_search or None, "season_id": season_id},
            "read_only": True,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    def _project_row(
        self,
        row: Any,
        files_by_package: dict[str, list[dict[str, Any]]],
        selections: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        item = dict(row)
        episode_id = str(item["episode_id"])
        blockers: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        source_choices: list[dict[str, Any]] = []
        stream = _video_stream(item.get("probe_json"))
        compose_snapshot = _object_json(item.get("compose_input_snapshot_json"))
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        compose_id = item.get("compose_id")
        approval_valid = bool(
            compose_id
            and item.get("approval_decision") == "APPROVED"
            and int(item.get("approval_stale") or 0) == 0
            and int(item.get("approval_subject_revision") or 0) == int(item.get("compose_revision") or 0)
        )
        if not compose_id:
            blockers.append({"code": "UPSCALE_SOURCE_COMPOSE_REQUIRED", "message": "本集还没有最终合成成片"})
        elif item.get("integrity_status") != "VERIFIED":
            blockers.append({"code": "UPSCALE_SOURCE_INTEGRITY_INVALID", "message": "当前合成成片未通过完整性校验"})
        elif not approval_valid:
            blockers.append({"code": "UPSCALE_SOURCE_APPROVAL_REQUIRED", "message": "当前最新合成成片尚未批准或批准已过期"})
        if compose_id and width > 0 and height > 0:
            geometry = resolve_upscale_geometry(source_width=width, source_height=height)
            if width >= int(geometry["target"]["width"]) and height >= int(geometry["target"]["height"]):
                warnings.append({"code": "SOURCE_ALREADY_TARGET_GEOMETRY", "message": "源视频已达到或超过默认目标尺寸"})
            source_choices.append(
                {
                    "kind": "EPISODE_RENDER",
                    "render_id": str(compose_id),
                    "root_compose_render_id": str(compose_id),
                    "render_revision": int(item["compose_revision"]),
                    "source_sha256": str(item["compose_sha256"]),
                    "source_approval_id": str(item["approval_id"]) if item.get("approval_id") else None,
                    "rel_path": str(item["compose_rel_path"]),
                    "probe": {"width": width, "height": height, "stream": stream},
                    "geometry": geometry,
                    "applied_effects": {
                        "subtitle_burned": bool(compose_snapshot.get("subtitle_burned_in", False)),
                        "subtitle_revision_id": compose_snapshot.get("subtitle_revision_id"),
                        "watermark_profile_snapshot": None,
                        "evidence_source": "COMPOSE_RENDER_SNAPSHOT",
                    },
                }
            )
        elif compose_id:
            blockers.append({"code": "UPSCALE_SOURCE_PROBE_INCOMPLETE", "message": "当前成片缺少可用的视频宽高证据"})

        recommended_source: dict[str, Any] | None = source_choices[0] if source_choices else None
        package_id = item.get("delivery_package_id")
        if package_id and item.get("delivery_human_review_status") == "APPROVED":
            video_files = [
                file_item
                for file_item in files_by_package.get(str(package_id), [])
                if str(file_item["rel_path"]).lower().endswith(_VIDEO_SUFFIXES)
            ]
            if len(video_files) == 1:
                delivery_file = video_files[0]
                delivery_spec = _object_json(item.get("delivery_target_spec_json"))
                watermark_snapshot = None
                if item.get("delivery_watermark_profile_id"):
                    watermark_snapshot = {
                        "id": str(item["delivery_watermark_profile_id"]),
                        "code": str(item.get("delivery_watermark_code") or ""),
                        "version_no": int(item.get("delivery_watermark_version_no") or 0),
                        "config": _object_json(item.get("delivery_watermark_config_json")),
                    }
                subtitle_mode = str(delivery_spec.get("subtitles") or "NONE").upper()
                delivery_source = {
                    "kind": "DELIVERY_FILE",
                    "episode_id": episode_id,
                    "root_compose_render_id": str(compose_id),
                    "render_revision": int(item["compose_revision"]),
                    "delivery_package_id": str(package_id),
                    "delivery_file_id": str(delivery_file["id"]),
                    "source_sha256": str(delivery_file["sha256"]),
                    "source_byte_size": int(delivery_file["byte_size"]),
                    "source_approval_id": str(item["approval_id"]) if item.get("approval_id") else None,
                    "source_manifest_hash": str(item["manifest_sha256"]),
                    "delivery_human_review_status": "APPROVED",
                    "rel_path": str(delivery_file["rel_path"]),
                    "requires_preflight_probe": True,
                    "applied_effects": {
                        "subtitle_burned": subtitle_mode in {"BURN_IN", "BOTH"},
                        "subtitle_revision_id": compose_snapshot.get("subtitle_revision_id"),
                        "delivery_subtitle_mode": subtitle_mode,
                        "watermark_profile_snapshot": watermark_snapshot,
                        "evidence_source": "APPROVED_DELIVERY_PACKAGE",
                    },
                }
                source_choices.insert(0, delivery_source)
                recommended_source = delivery_source
            elif len(video_files) > 1:
                warnings.append({"code": "SOURCE_DELIVERY_VIDEO_AMBIGUOUS", "message": "最新交付包包含多个视频，请显式选择来源"})
        elif package_id:
            warnings.append(
                {
                    "code": "SOURCE_DELIVERY_HUMAN_APPROVAL_REQUIRED",
                    "message": "最新交付包尚未完成人工批准，本次仍使用已批准的合成成片",
                }
            )

        return {
            "episode": {
                "id": episode_id,
                "code": str(item["episode_code"]),
                "title": str(item["episode_title"] or ""),
                "number": int(item["episode_number"]),
                "season_id": str(item["season_id"]),
                "season_number": int(item["season_number"]),
                "season_title": str(item["season_title"] or ""),
            },
            "compose": ({
                "id": str(compose_id),
                "revision": int(item["compose_revision"]),
                "integrity_status": str(item["integrity_status"]),
                "sha256": str(item["compose_sha256"]),
                "duration_ms": item.get("duration_ms"),
                "width": width or None,
                "height": height or None,
                "approval": {
                    "id": str(item["approval_id"]) if item.get("approval_id") else None,
                    "decision": item.get("approval_decision"),
                    "is_stale": bool(item.get("approval_stale")),
                    "valid": approval_valid,
                },
            } if compose_id else None),
            "source_choices": source_choices,
            "recommended_source": recommended_source,
            "selectable": not blockers and recommended_source is not None,
            "blockers": blockers,
            "warnings": warnings,
            "derived_version_count": int(item["derived_count"]),
            "delivery_selections": selections.get(episode_id, []),
        }

    def resolve_selection(
        self,
        project_id: str,
        *,
        mode: str,
        episode_ids: list[str],
        search: str | None,
        season_id: str | None,
        source_policy: str = "PREFER_FINAL_DELIVERY",
    ) -> dict[str, Any]:
        if mode == "EXPLICIT":
            page = self.list_episodes(project_id, limit=200, episode_ids=episode_ids)
            found = {str(item["episode"]["id"]) for item in page["items"]}
            missing = sorted(set(episode_ids) - found)
            if missing:
                raise DomainRuleError("UPSCALE_SELECTION_PROJECT_MISMATCH", "选择中包含不属于当前项目的分集", {"episode_ids": missing})
            ordered = sorted(page["items"], key=lambda item: episode_ids.index(str(item["episode"]["id"])))
        else:
            page = self.list_episodes(project_id, limit=200, search=search, season_id=season_id)
            if int(page["page"]["total"]) > 200:
                raise DomainRuleError("UPSCALE_SELECTION_TOO_LARGE", "单次最多选择 200 集")
            ordered = list(page["items"])
        if source_policy not in {"PREFER_FINAL_DELIVERY", "APPROVED_COMPOSE"}:
            raise DomainRuleError("UPSCALE_SOURCE_POLICY_UNSUPPORTED", "不支持的超分来源策略")
        selected_rows: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        for item in ordered:
            source = item["recommended_source"]
            if source_policy == "APPROVED_COMPOSE":
                source = next(
                    (candidate for candidate in item["source_choices"] if candidate.get("kind") == "EPISODE_RENDER"),
                    None,
                )
            selected_rows.append((item, source))
        blocked = [
            {"episode_id": item["episode"]["id"], "blockers": item["blockers"]}
            for item, source in selected_rows
            if not item["selectable"] or source is None
        ]
        resolved = [
            {"episode_id": item["episode"]["id"], "source": source}
            for item, source in selected_rows
            if item["selectable"] and source is not None
        ]
        snapshot = {
            "schema_version": "localdrama.video-upscale-selection.v1",
            "project_id": project_id,
            "source_policy": source_policy,
            "items": resolved,
        }
        return {
            **snapshot,
            "selection_hash": _canonical_hash(snapshot),
            "count": len(resolved),
            "blocked": blocked,
            "mutated": False,
            "runtime_contacted": False,
        }
