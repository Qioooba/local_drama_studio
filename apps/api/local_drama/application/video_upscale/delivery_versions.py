from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.domain.errors import DomainRuleError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class EpisodeDeliveryVersionService:
    """Read, validate, and atomically adopt immutable episode render versions."""

    def __init__(self, database: DatabaseUnitOfWork) -> None:
        self.database = database

    @staticmethod
    def _project_id(connection: Any, episode_id: str) -> str:
        row = connection.execute(
            """SELECT s.project_id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=?""",
            (episode_id,),
        ).fetchone()
        if row is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "分集不存在")
        return str(row["project_id"])

    def list_versions(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            project_id = self._project_id(connection, episode_id)
            current_root = connection.execute(
                """SELECT * FROM episode_render_versions WHERE episode_id=? AND render_kind='COMPOSE'
                ORDER BY created_at DESC,id DESC LIMIT 1""",
                (episode_id,),
            ).fetchone()
            rows = connection.execute(
                """SELECT render.*,
                review.id AS approval_id,review.decision AS approval_decision,review.is_stale AS approval_stale,
                review.subject_revision AS approval_subject_revision,
                machine.id AS machine_check_run_id,machine.status AS machine_check_status
                FROM episode_render_versions render
                LEFT JOIN review_decisions review ON review.id=(
                  SELECT id FROM review_decisions WHERE subject_type='EPISODE_RENDER_VERSION' AND subject_id=render.id
                  ORDER BY created_at DESC,id DESC LIMIT 1
                )
                LEFT JOIN machine_check_runs machine ON machine.id=(
                  SELECT id FROM machine_check_runs WHERE subject_type='EPISODE_RENDER_VERSION' AND subject_id=render.id
                  ORDER BY created_at DESC,id DESC LIMIT 1
                )
                WHERE render.episode_id=? ORDER BY render.created_at DESC,render.id DESC""",
                (episode_id,),
            ).fetchall()
            selections = connection.execute(
                "SELECT * FROM episode_delivery_selections WHERE episode_id=? ORDER BY target_slot",
                (episode_id,),
            ).fetchall()
        root_id = str(current_root["id"]) if current_root else None
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["probe"] = json.loads(str(item.pop("probe_json") or "{}"))
            item["input_snapshot"] = json.loads(str(item.pop("input_snapshot_json") or "{}"))
            approved = bool(
                item.get("approval_id")
                and item.get("approval_decision") == "APPROVED"
                and int(item.get("approval_stale") or 0) == 0
                and int(item.get("approval_subject_revision") or 0) == int(item["revision"])
            )
            machine_pass = item["render_kind"] == "COMPOSE" or item.get("machine_check_status") == "PASS"
            source_current = item["render_kind"] == "COMPOSE" or str(item.get("parent_render_version_id") or "") == root_id
            item["approved"] = approved
            item["machine_qc_passed"] = machine_pass
            item["source_current"] = source_current
            item["adoptable"] = bool(
                item["integrity_status"] == "VERIFIED" and approved and machine_pass and source_current
            )
            item["stale_reason"] = None if source_current else "ROOT_COMPOSE_CHANGED"
            items.append(item)
        return {
            "episode_id": episode_id,
            "project_id": project_id,
            "current_root_compose_render_id": root_id,
            "items": items,
            "selections": [dict(row) for row in selections],
            "read_only": True,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    def _plan_with_connection(
        self,
        connection: Any,
        project_id: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
        planned: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for raw in items:
            episode_id = str(raw["episode_id"])
            target_slot = str(raw["target_slot"])
            key = (episode_id, target_slot)
            if key in seen:
                raise DomainRuleError("DELIVERY_SELECTION_DUPLICATE", "同一分集和目标档位不可重复采用")
            seen.add(key)
            render = connection.execute(
                """SELECT render.*,season.project_id FROM episode_render_versions render
                JOIN episodes episode ON episode.id=render.episode_id
                JOIN seasons season ON season.id=episode.season_id WHERE render.id=?""",
                (raw["selected_render_id"],),
            ).fetchone()
            if render is None or str(render["episode_id"]) != episode_id or str(render["project_id"]) != project_id:
                raise DomainRuleError("DELIVERY_SELECTION_RENDER_MISMATCH", "采用版本不属于当前项目和分集")
            root = connection.execute(
                """SELECT * FROM episode_render_versions WHERE episode_id=? AND render_kind='COMPOSE'
                ORDER BY created_at DESC,id DESC LIMIT 1""",
                (episode_id,),
            ).fetchone()
            if root is None:
                raise DomainRuleError("UPSCALE_SOURCE_STALE", "本集当前没有可追溯的合成根版本")
            if str(render["render_kind"]) == "SUPER_RESOLUTION" and str(render["parent_render_version_id"]) != str(root["id"]):
                raise DomainRuleError("UPSCALE_SOURCE_STALE", "超分结果的合成根已经更新，请重新处理")
            if str(render["render_kind"]) not in {"COMPOSE", "SUPER_RESOLUTION"}:
                raise DomainRuleError("DELIVERY_SELECTION_RENDER_KIND_INVALID", "该成片类型不能用于正式交付")
            if str(render["integrity_status"]) != "VERIFIED":
                raise DomainRuleError("EPISODE_RENDER_NOT_VERIFIED", "采用版本未通过完整性校验")
            target = connection.execute(
                """SELECT version.target_spec_json,version.status,identity.project_id
                FROM delivery_target_versions version
                JOIN delivery_targets identity ON identity.id=version.delivery_target_id
                WHERE version.id=?""",
                (target_slot,),
            ).fetchone()
            if target is None or str(target["project_id"]) != project_id:
                raise DomainRuleError("DELIVERY_TARGET_VERSION_NOT_FOUND", "采用目标不存在或不属于当前项目")
            target_spec = json.loads(str(target["target_spec_json"] or "{}"))
            render_probe = json.loads(str(render["probe_json"] or "{}"))
            video_stream = next(
                (
                    stream
                    for stream in render_probe.get("streams", [])
                    if isinstance(stream, dict) and stream.get("codec_type") == "video"
                ),
                {},
            )
            target_width = int(target_spec.get("width") or 0)
            target_height = int(target_spec.get("height") or 0)
            if target_width > 0 and target_height > 0 and (
                int(video_stream.get("width") or 0) != target_width
                or int(video_stream.get("height") or 0) != target_height
            ):
                raise DomainRuleError(
                    "DELIVERY_SELECTION_TARGET_GEOMETRY_MISMATCH",
                    "成片像素与交付目标不匹配，不能采用到该档位",
                    {
                        "episode_id": episode_id,
                        "render_width": int(video_stream.get("width") or 0),
                        "render_height": int(video_stream.get("height") or 0),
                        "target_width": target_width,
                        "target_height": target_height,
                    },
                )
            approval = connection.execute(
                """SELECT * FROM review_decisions WHERE subject_type='EPISODE_RENDER_VERSION' AND subject_id=?
                ORDER BY created_at DESC,id DESC LIMIT 1""",
                (render["id"],),
            ).fetchone()
            if (
                approval is None
                or str(approval["decision"]) != "APPROVED"
                or int(approval["is_stale"] or 0) != 0
                or int(approval["subject_revision"]) != int(render["revision"])
            ):
                raise DomainRuleError("EPISODE_RENDER_APPROVAL_REQUIRED", "采用版本必须具有当前有效的人工批准")
            if str(render["render_kind"]) == "SUPER_RESOLUTION":
                machine = connection.execute(
                    """SELECT * FROM machine_check_runs WHERE subject_type='EPISODE_RENDER_VERSION' AND subject_id=?
                    ORDER BY created_at DESC,id DESC LIMIT 1""",
                    (render["id"],),
                ).fetchone()
                if machine is None or str(machine["status"]) != "PASS":
                    raise DomainRuleError("UPSCALE_QC_FAILED", "超分结果尚未通过机器 QC，不能采用")
            existing = connection.execute(
                "SELECT * FROM episode_delivery_selections WHERE episode_id=? AND target_slot=?",
                (episode_id, target_slot),
            ).fetchone()
            current_revision = int(existing["revision"]) if existing else 0
            if current_revision != int(raw["expected_selection_revision"]):
                raise DomainRuleError(
                    "REVISION_CONFLICT",
                    "交付版本选择已变化，请刷新后重试",
                    {"episode_id": episode_id, "target_slot": target_slot, "actual_revision": current_revision},
                )
            planned.append(
                {
                    "episode_id": episode_id,
                    "target_slot": target_slot,
                    "selected_render_id": str(render["id"]),
                    "root_compose_render_id": str(root["id"]),
                    "approval_id": str(approval["id"]),
                    "render_sha256": str(render["sha256"]),
                    "expected_selection_revision": current_revision,
                }
            )
        snapshot = {"schema_version": "localdrama.delivery-selection-plan.v1", "project_id": project_id, "items": planned}
        return {**snapshot, "plan_hash": _hash(snapshot), "status": "READY", "would_mutate": False}

    def plan(self, project_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        with self.database.connect() as connection:
            return self._plan_with_connection(connection, project_id, items)

    def commit(
        self,
        project_id: str,
        items: list[dict[str, Any]],
        *,
        plan_hash: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        now = _now()
        with self.database.transaction() as connection:
            plan = self._plan_with_connection(connection, project_id, items)
            if not hmac.compare_digest(str(plan["plan_hash"]), plan_hash):
                raise DomainRuleError("DELIVERY_SELECTION_PLAN_STALE", "采用计划已经变化，请重新检查")
            for item in plan["items"]:
                existing = connection.execute(
                    "SELECT id,revision,selected_render_id FROM episode_delivery_selections WHERE episode_id=? AND target_slot=?",
                    (item["episode_id"], item["target_slot"]),
                ).fetchone()
                if existing is None:
                    selection_id = str(uuid.uuid4())
                    connection.execute(
                        """INSERT INTO episode_delivery_selections
                        (id,episode_id,target_slot,selected_render_id,root_compose_render_id,approval_id,
                         created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,?,?,?,1,'video-upscale-selection.v1')""",
                        (
                            selection_id,
                            item["episode_id"],
                            item["target_slot"],
                            item["selected_render_id"],
                            item["root_compose_render_id"],
                            item["approval_id"],
                            now,
                            now,
                            actor,
                        ),
                    )
                    before_revision = 0
                    after_revision = 1
                    previous_render_id = None
                else:
                    selection_id = str(existing["id"])
                    before_revision = int(existing["revision"])
                    after_revision = before_revision + 1
                    previous_render_id = str(existing["selected_render_id"])
                    connection.execute(
                        """UPDATE episode_delivery_selections SET selected_render_id=?,root_compose_render_id=?,
                        approval_id=?,updated_at=?,created_by=?,revision=revision+1
                        WHERE id=? AND revision=?""",
                        (
                            item["selected_render_id"],
                            item["root_compose_render_id"],
                            item["approval_id"],
                            now,
                            actor,
                            selection_id,
                            before_revision,
                        ),
                    )
                connection.execute(
                    """INSERT INTO audit_events
                    (actor,role_context,action,subject_type,subject_id,before_revision,after_revision,summary,metadata_redacted_json)
                    VALUES (?,'producer','EPISODE_DELIVERY_VERSION_SELECTED','episode_delivery_selection',?,?,?,?,?)""",
                    (
                        actor,
                        selection_id,
                        before_revision,
                        after_revision,
                        "采用整集交付成片版本",
                        _json(
                            {
                                "episode_id": item["episode_id"],
                                "target_slot": item["target_slot"],
                                "previous_render_id": previous_render_id,
                                "selected_render_id": item["selected_render_id"],
                            }
                        ),
                    ),
                )
        return {
            "status": "COMMITTED",
            "project_id": project_id,
            "count": len(items),
            "plan_hash": plan_hash,
            "selections": [
                selection
                for item in items
                for selection in self.list_versions(str(item["episode_id"]))["selections"]
                if selection["target_slot"] == item["target_slot"]
            ],
        }
