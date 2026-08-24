from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ProductionCanvasService:
    """Read-only business DAG plus independently persisted visual layout."""

    STAGES = ("DIRECT", "KEYFRAME", "PROXY", "FORMAL", "TIMELINE")

    def __init__(self, database: Database) -> None:
        self.database = database

    def _scope(self, scope_type: str, scope_id: str) -> dict[str, Any]:
        normalized = scope_type.upper()
        with self.database.connect() as connection:
            if normalized == "EPISODE":
                row = connection.execute(
                    """SELECT e.id, e.code, e.title, s.project_id FROM episodes e
                    JOIN seasons s ON s.id=e.season_id WHERE e.id=?""",
                    (scope_id,),
                ).fetchone()
            elif normalized == "SHOT":
                row = connection.execute(
                    """SELECT sh.id, sh.code, sh.code AS title, s.project_id, sh.episode_id FROM shots sh
                    JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id
                    WHERE sh.id=? AND sh.archived_at IS NULL""",
                    (scope_id,),
                ).fetchone()
            else:
                raise DomainRuleError("CANVAS_SCOPE_UNSUPPORTED", "业务画布只支持 EPISODE 或 SHOT scope")
        if row is None:
            raise DomainRuleError("CANVAS_SCOPE_NOT_FOUND", "业务画布 scope 不存在")
        return {**dict(row), "scope_type": normalized}

    def _layout(self, scope_type: str, scope_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM canvas_layouts WHERE scope_type=? AND scope_id=?", (scope_type, scope_id)).fetchone()
        if row is None:
            return {"positions": {}, "groups": [], "viewport": {}, "revision": 0, "layout_hash": None}
        return {**json.loads(row["layout_json"]), "revision": row["revision"], "layout_hash": row["layout_hash"]}

    def graph(self, scope_type: str, scope_id: str, *, cursor: int = 0, limit: int = 100) -> dict[str, Any]:
        scope = self._scope(scope_type, scope_id)
        if cursor < 0 or limit < 1 or limit > 300:
            raise DomainRuleError("CANVAS_PAGE_INVALID", "画布 cursor/limit 超出范围")
        episode_id = scope_id if scope["scope_type"] == "EPISODE" else str(scope["episode_id"])
        with self.database.connect() as connection:
            if scope["scope_type"] == "SHOT":
                shots = connection.execute("SELECT * FROM shots WHERE id=? AND archived_at IS NULL", (scope_id,)).fetchall()
                total = len(shots)
            else:
                total = int(connection.execute("SELECT COUNT(*) FROM shots WHERE episode_id=? AND archived_at IS NULL", (episode_id,)).fetchone()[0])
                shots = connection.execute(
                    """SELECT * FROM shots WHERE episode_id=? AND archived_at IS NULL
                    ORDER BY CAST(order_key AS REAL), code LIMIT ? OFFSET ?""",
                    (episode_id, limit, cursor),
                ).fetchall()
            shot_ids = [str(row["id"]) for row in shots]
            data: dict[str, dict[str, Any]] = {
                shot_id: {"media": [], "jobs": [], "logs": [], "variants": [], "experiments": [], "constraints": []}
                for shot_id in shot_ids
            }
            if shot_ids:
                placeholders = ",".join("?" for _ in shot_ids)
                media = connection.execute(
                    f"""SELECT ma.owner_id AS shot_id, ma.purpose, ma.media_kind, ma.selected_version_id, ma.approved_version_id,
                    mv.id AS media_version_id, mv.stage, mv.rel_path FROM media_assets ma
                    JOIN media_versions mv ON mv.media_asset_id=ma.id
                    WHERE ma.owner_type='SHOT' AND ma.owner_id IN ({placeholders}) ORDER BY mv.created_at""",
                    shot_ids,
                ).fetchall()
                for item in media:
                    data[str(item["shot_id"])]["media"].append(dict(item))
                jobs = connection.execute(
                    f"SELECT * FROM jobs WHERE subject_type='SHOT' AND subject_id IN ({placeholders}) ORDER BY created_at DESC",
                    shot_ids,
                ).fetchall()
                for item in jobs:
                    data[str(item["subject_id"])]["jobs"].append(dict(item))
                # Job/outbox events are the durable, redacted execution log for
                # a shot. Keep them read-only and bounded so this remains a lazy
                # graph projection rather than a second job-details API.
                job_ids = [str(item["id"]) for item in jobs]
                if job_ids:
                    job_placeholders = ",".join("?" for _ in job_ids)
                    events = connection.execute(
                        f"""SELECT e.event_id, e.type, e.subject_type, e.subject_id, e.payload_json, e.occurred_at,
                        COALESCE(j.id, ja.job_id) AS job_id
                        FROM outbox_events e
                        LEFT JOIN jobs j ON e.subject_type='JOB' AND e.subject_id=j.id
                        LEFT JOIN job_attempts ja ON e.subject_type='JOB_ATTEMPT' AND e.subject_id=ja.id
                        WHERE (e.subject_type='JOB' AND e.subject_id IN ({job_placeholders}))
                           OR (e.subject_type='JOB_ATTEMPT' AND ja.job_id IN ({job_placeholders}))
                        ORDER BY e.event_id DESC LIMIT 400""",
                        [*job_ids, *job_ids],
                    ).fetchall()
                    shot_by_job = {str(item["id"]): str(item["subject_id"]) for item in jobs}
                    for event in events:
                        shot_id = shot_by_job.get(str(event["job_id"]))
                        if shot_id is None:
                            continue
                        data[shot_id]["logs"].append(
                            {
                                "event_id": int(event["event_id"]),
                                "type": str(event["type"]),
                                "subject_type": str(event["subject_type"]),
                                "subject_id": str(event["subject_id"]),
                                "occurred_at": str(event["occurred_at"]),
                                "payload": json.loads(str(event["payload_json"] or "{}")),
                            }
                        )
                variants = connection.execute(
                    f"""SELECT gi.owner_id AS shot_id, gv.* FROM generation_intents gi
                    JOIN generation_variants gv ON gv.intent_id=gi.id
                    WHERE gi.owner_type='SHOT' AND gi.owner_id IN ({placeholders}) ORDER BY gv.created_at""",
                    shot_ids,
                ).fetchall()
                for item in variants:
                    data[str(item["shot_id"])]["variants"].append(dict(item))
                experiments = connection.execute(
                    f"""SELECT gi.owner_id AS shot_id, ge.id, ge.title, ge.status, ge.cell_count,
                    ge.max_parallel, COUNT(ec.id) AS expanded_count,
                    COALESCE(SUM(CASE WHEN ec.status='SUCCEEDED' THEN 1 ELSE 0 END), 0) AS succeeded_count,
                    COALESCE(SUM(CASE WHEN ec.status IN ('FAILED','NEEDS_ATTENTION') THEN 1 ELSE 0 END), 0) AS failed_count
                    FROM generation_experiments ge JOIN generation_intents gi ON gi.id=ge.intent_id
                    LEFT JOIN experiment_cells ec ON ec.experiment_id=ge.id
                    WHERE gi.owner_type='SHOT' AND gi.owner_id IN ({placeholders})
                    GROUP BY gi.owner_id, ge.id ORDER BY ge.created_at""",
                    shot_ids,
                ).fetchall()
                for item in experiments:
                    data[str(item["shot_id"])]["experiments"].append(dict(item))
                constraints = connection.execute(
                    f"""SELECT * FROM shot_transition_constraints
                    WHERE from_shot_id IN ({placeholders}) OR to_shot_id IN ({placeholders})""",
                    [*shot_ids, *shot_ids],
                ).fetchall()
                for item in constraints:
                    for key in ("from_shot_id", "to_shot_id"):
                        if str(item[key]) in data:
                            data[str(item[key])]["constraints"].append(dict(item))

        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        previous_timeline: str | None = None
        for row in shots:
            shot = dict(row)
            shot_id = str(shot["id"])
            facts = data[shot_id]
            for index, stage in enumerate(self.STAGES):
                node_id = f"shot:{shot_id}:{stage.lower()}"
                node = self._node(node_id, shot, stage, facts)
                nodes.append(node)
                if index:
                    edges.append({"id": f"{shot_id}:{self.STAGES[index - 1]}:{stage}", "source": f"shot:{shot_id}:{self.STAGES[index - 1].lower()}", "target": node_id, "kind": "BUSINESS_DEPENDENCY", "mutable_by_layout": False})
            timeline_id = f"shot:{shot_id}:timeline"
            if previous_timeline:
                edges.append({"id": f"sequence:{previous_timeline}:{timeline_id}", "source": previous_timeline, "target": timeline_id, "kind": "SHOT_SEQUENCE", "mutable_by_layout": False})
            previous_timeline = timeline_id
            for constraint in facts["constraints"]:
                if str(constraint["from_shot_id"]) == shot_id and str(constraint["to_shot_id"]) in data:
                    edges.append({"id": f"constraint:{constraint['id']}", "source": timeline_id, "target": f"shot:{constraint['to_shot_id']}:direct", "kind": "TRANSITION_CONSTRAINT", "status": constraint["compatibility_status"], "mutable_by_layout": False})
        layout = self._layout(scope["scope_type"], scope_id)
        for node in nodes:
            node["position"] = layout["positions"].get(node["id"])
        return {
            "scope": scope,
            "nodes": nodes,
            "edges": edges,
            "layout": layout,
            "page": {"cursor": cursor, "limit": limit, "returned_shots": len(shots), "total_shots": total, "next_cursor": cursor + len(shots) if cursor + len(shots) < total else None},
            "invariants": {"layout_changes_business_dependencies": False, "max_visible_nodes": 300, "lazy": True},
        }

    def _node(self, node_id: str, shot: dict[str, Any], stage: str, facts: dict[str, Any]) -> dict[str, Any]:
        media = [item for item in facts["media"] if self._stage_for_media(item) == stage]
        jobs = facts["jobs"]
        variants = facts["variants"]
        # Prefer a user-selected/approved revision for the visual cue, then
        # fall back to the newest revision in this stage.  The URL is always a
        # derived small thumbnail endpoint; the canvas never exposes source
        # media paths or loads original bytes.
        thumbnail_version_id = next(
            (
                str(item["selected_version_id"])
                for item in reversed(media)
                if item.get("selected_version_id")
            ),
            next(
                (
                    str(item["approved_version_id"])
                    for item in reversed(media)
                    if item.get("approved_version_id")
                ),
                str(media[-1]["media_version_id"]) if media else None,
            ),
        )
        thumbnail_url = (
            f"/api/v1/media-versions/{thumbnail_version_id}/thumbnail?size=small&frame=poster"
            if thumbnail_version_id
            else None
        )
        active = [item for item in jobs if item["state"] in {"QUEUED", "CLAIMED", "RUNNING", "CANCEL_REQUESTED"}]
        failed = [item for item in jobs if item["state"] in {"FAILED", "NEEDS_ATTENTION"}]
        blockers: list[str] = []
        if stage == "DIRECT" and shot["status"] not in {"READY", "PRODUCING", "PROXY_SELECTED", "FORMAL_APPROVED"}:
            blockers.append("SHOT_NOT_PRODUCTION_READY")
        if stage == "KEYFRAME" and not any(item["approved_version_id"] for item in media):
            blockers.append("APPROVED_KEYFRAME_REQUIRED")
        if stage == "PROXY" and not any(item["selected_version_id"] for item in media):
            blockers.append("PROXY_WINNER_REQUIRED")
        if stage == "FORMAL" and not any(item["approved_version_id"] for item in media):
            blockers.append("FORMAL_APPROVAL_REQUIRED")
        state = "RUNNING" if active else "NEEDS_ATTENTION" if failed else "BLOCKED" if blockers else "READY"
        return {
            "id": node_id,
            "type": stage,
            "shot_id": shot["id"],
            "shot_code": shot["code"],
            "label": f"{shot['code']} · {stage}",
            "state": state,
            "blockers": blockers,
            "take_count": len(media),
            "variant_count": len(variants),
            "active_job_count": len(active),
            "thumbnail_media_version_id": thumbnail_version_id,
            "thumbnail_url": thumbnail_url,
            "log_count": len(facts["logs"]),
            "logs": facts["logs"][:8],
            "variant_lineage": [
                {
                    "id": str(item["id"]),
                    "variant_no": int(item["variant_no"]),
                    "variant_type": str(item["variant_type"]),
                    "parent_variant_id": str(item["parent_variant_id"]) if item["parent_variant_id"] else None,
                    "status": str(item["status"]),
                    "is_stale": bool(item.get("is_stale", 0)),
                    "branch_reason": str(item["branch_reason"]),
                }
                for item in sorted(variants, key=lambda value: int(value["variant_no"]))
            ],
            "experiment_progress": [
                {
                    "id": str(item["id"]),
                    "title": str(item["title"]),
                    "status": str(item["status"]),
                    "cell_count": int(item["cell_count"]),
                    "expanded_count": int(item["expanded_count"]),
                    "succeeded_count": int(item["succeeded_count"]),
                    "failed_count": int(item["failed_count"]),
                }
                for item in facts["experiments"]
            ],
            "adjacent_constraints": [
                {
                    "id": str(item["id"]),
                    "from_shot_id": str(item["from_shot_id"]),
                    "to_shot_id": str(item["to_shot_id"]),
                    "constraint_type": str(item["constraint_type"]),
                    "compatibility_status": str(item["compatibility_status"]),
                    "enforcement": str(item["enforcement"]),
                    "is_stale": bool(item.get("is_stale", 0)),
                }
                for item in {str(item["id"]): item for item in facts["constraints"]}.values()
            ],
            "keyboard_action": "OPEN_NODE",
        }

    @staticmethod
    def _stage_for_media(item: dict[str, Any]) -> str:
        purpose = str(item["purpose"]).upper()
        stage = str(item["stage"]).upper()
        if "KEY" in purpose or item["media_kind"] == "IMAGE":
            return "KEYFRAME"
        if stage == "FORMAL" or "FORMAL" in purpose:
            return "FORMAL"
        return "PROXY"

    def save_layout(self, scope_type: str, scope_id: str, layout: dict[str, Any], expected_revision: int | None, actor: str = "local-user") -> dict[str, Any]:
        scope = self._scope(scope_type, scope_id)
        allowed = {node["id"] for node in self.graph(scope["scope_type"], scope_id, limit=300)["nodes"]}
        unknown = sorted(set(layout.get("positions", {})) - allowed)
        if unknown:
            raise DomainRuleError("CANVAS_LAYOUT_NODE_UNKNOWN", "布局包含不属于当前 scope 的节点", {"node_ids": unknown[:20]})
        content = {"positions": layout.get("positions", {}), "groups": layout.get("groups", []), "viewport": layout.get("viewport", {})}
        digest = hashlib.sha256(_json(content).encode()).hexdigest()
        now = _now()
        with self.database.transaction() as connection:
            current = connection.execute("SELECT * FROM canvas_layouts WHERE scope_type=? AND scope_id=?", (scope["scope_type"], scope_id)).fetchone()
            if current is None:
                if expected_revision not in {None, 0}:
                    raise DomainRuleError("REVISION_CONFLICT", "画布布局 revision 冲突")
                layout_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO canvas_layouts
                    (id, project_id, scope_type, scope_id, layout_json, layout_hash, created_at, updated_at, created_by, revision, schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'v2')""",
                    (layout_id, scope["project_id"], scope["scope_type"], scope_id, _json(content), digest, now, now, actor),
                )
                revision = 1
            else:
                if expected_revision != current["revision"]:
                    raise DomainRuleError("REVISION_CONFLICT", "画布布局已被其他视图修改", {"current_revision": current["revision"]})
                connection.execute("UPDATE canvas_layouts SET layout_json=?, layout_hash=?, updated_at=?, revision=revision+1 WHERE id=?", (_json(content), digest, now, current["id"]))
                revision = int(current["revision"]) + 1
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'producer', 'CANVAS_LAYOUT_SAVED', 'canvas_layout', ?, ?, ?)",
                (actor, scope_id, "保存业务画布视觉布局", _json({"layout_hash": digest, "business_dependencies_changed": False})),
            )
        return {**content, "revision": revision, "layout_hash": digest}

    def preflight(self, scope_type: str, scope_id: str, mode: str, from_node_id: str | None, to_node_id: str | None, max_nodes: int, actor: str = "local-user") -> dict[str, Any]:
        graph = self.graph(scope_type, scope_id, limit=300)
        nodes = graph["nodes"]
        ids = [node["id"] for node in nodes]
        normalized = mode.upper()
        if normalized == "NODE":
            selected = [from_node_id] if from_node_id else []
        elif normalized == "FROM":
            if from_node_id not in ids:
                raise DomainRuleError("CANVAS_NODE_NOT_FOUND", "起点节点不存在")
            selected = ids[ids.index(from_node_id) :]
        elif normalized == "TO":
            if to_node_id not in ids:
                raise DomainRuleError("CANVAS_NODE_NOT_FOUND", "终点节点不存在")
            selected = ids[: ids.index(to_node_id) + 1]
        elif normalized == "RANGE":
            if from_node_id not in ids or to_node_id not in ids:
                raise DomainRuleError("CANVAS_NODE_NOT_FOUND", "范围节点不存在")
            start, end = ids.index(from_node_id), ids.index(to_node_id)
            if start > end:
                raise DomainRuleError("CANVAS_RANGE_INVALID", "运行范围起点必须位于终点之前")
            selected = ids[start : end + 1]
        else:
            raise DomainRuleError("CANVAS_RUN_MODE_UNSUPPORTED", "不支持的画布运行模式")
        if not selected or len(selected) > max_nodes:
            raise DomainRuleError("CANVAS_PLAN_LIMIT", "画布执行计划为空或超过节点上限", {"selected": len(selected), "max_nodes": max_nodes})
        by_id = {node["id"]: node for node in nodes}
        blockers = [{"node_id": node_id, "blockers": by_id[node_id]["blockers"]} for node_id in selected if by_id[node_id]["blockers"]]
        estimate = {"node_count": len(selected), "gpu_heavy_nodes": sum(by_id[node_id]["type"] in {"PROXY", "FORMAL"} for node_id in selected), "max_parallel_gpu": 1, "requires_human_gate": any(by_id[node_id]["type"] in {"KEYFRAME", "PROXY", "FORMAL", "TIMELINE"} for node_id in selected)}
        plan_id = str(uuid.uuid4())
        now = _now()
        status = "BLOCKED" if blockers else "READY"
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO canvas_execution_plans
                (id, project_id, scope_type, scope_id, mode, from_node_id, to_node_id, node_ids_json, blockers_json,
                 estimate_json, status, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'v2')""",
                (plan_id, graph["scope"]["project_id"], graph["scope"]["scope_type"], scope_id, normalized, from_node_id, to_node_id, _json(selected), _json(blockers), _json(estimate), status, now, now, actor),
            )
        return {"id": plan_id, "status": status, "mode": normalized, "node_ids": selected, "blockers": blockers, "estimate": estimate, "submitted": False}
