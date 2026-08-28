from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ShotEditingService:
    """Server-authoritative semantic reorder and non-destructive split."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def context(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            self._episode(connection, episode_id)
            rows = self._active_shots(connection, episode_id)
            return {
                "episode_id": episode_id,
                "ordering_token": self._ordering_token(rows),
                "items": [self._snapshot_item(row) for row in rows],
            }

    def plan(self, episode_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.database.connect() as connection:
            return self._build_plan(connection, episode_id, payload)

    def commit(
        self, episode_id: str, payload: dict[str, Any], expected_plan_hash: str, *, actor: str = "local-user",
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            plan = self._build_plan(connection, episode_id, payload)
            if plan["plan_hash"] != expected_plan_hash:
                raise DomainRuleError("SHOT_EDIT_PLAN_STALE", "镜头编辑计划已变化，请重新预览")
            if not plan["valid"]:
                raise DomainRuleError("SHOT_EDIT_PLAN_INVALID", "镜头编辑计划含校验问题", {"issues": plan["issues"]})
            now = _now()
            active_ids = list(plan["ordered_shot_ids"])
            created: list[dict[str, str]] = []
            for split in plan["splits"]:
                source_id = str(split["shot_id"])
                source = connection.execute(
                    "SELECT * FROM shots WHERE id=? AND episode_id=? AND archived_at IS NULL",
                    (source_id, episode_id),
                ).fetchone()
                if source is None or int(source["revision"]) != int(split["expected_revision"]):
                    raise DomainRuleError("SHOT_REVISION_CONFLICT", "待拆分镜头已变化，请重新预览", {"shot_id": source_id})
                source_revision = connection.execute(
                    "SELECT fields_json,is_frozen FROM shot_revisions WHERE id=?", (source["current_revision_id"],),
                ).fetchone()
                try:
                    source_fields = json.loads(str(source_revision["fields_json"] or "{}")) if source_revision else {}
                except json.JSONDecodeError:
                    source_fields = {}
                if not isinstance(source_fields, dict):
                    source_fields = {}
                child_ids: list[str] = []
                for segment, code, duration in (
                    ("A", str(split["first_code"]), int(split["first_duration_ms"])),
                    ("B", str(split["second_code"]), int(split["second_duration_ms"])),
                ):
                    child_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
                    child_fields = dict(source_fields)
                    child_fields["split_lineage"] = {
                        "source_shot_id": source_id, "segment": segment,
                        "source_revision_id": str(source["current_revision_id"]),
                        "source_revision": int(source["revision"]),
                    }
                    connection.execute(
                        """INSERT INTO shots
                        (id,episode_id,code,order_key,target_duration_ms,shot_type,status,current_revision_id,
                        scene_id,source_shot_id,archived_at,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,?, ?,?,?,NULL,?,?,?,1,'v2')""",
                        (child_id, episode_id, code, "0", duration, source["shot_type"],
                         "DIRECTED" if source_revision else "DRAFT", revision_id, source["scene_id"], source_id,
                         now, now, actor),
                    )
                    connection.execute(
                        """INSERT INTO shot_revisions
                        (id,shot_id,revision_no,fields_json,is_frozen,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,1,?,?,?, ?,?,1,'v2')""",
                        (revision_id, child_id, _json(child_fields), int(source_revision["is_frozen"]) if source_revision else 0,
                         now, now, actor),
                    )
                    self._copy_asset_bindings(connection, source_id, child_id, now, actor)
                    child_ids.append(child_id)
                    created.append({"id": child_id, "code": code, "source_shot_id": source_id, "segment": segment})
                self._replace_group_memberships(connection, source_id, child_ids, now, actor)
                connection.execute("UPDATE shots SET archived_at=?,updated_at=? WHERE id=?", (now, now, source_id))
                position = active_ids.index(source_id)
                active_ids[position:position + 1] = child_ids
                self._audit(connection, actor, "SHOT_SPLIT", "shot", source_id, {
                    "plan_hash": expected_plan_hash, "child_shot_ids": child_ids,
                    "source_revision_unchanged": int(source["revision"]), "asset_binding_policy": "COPY_DECLARATIVE_ONLY",
                    "selected_results_copied": False,
                })

            for ordinal, shot_id in enumerate(active_ids, start=1):
                connection.execute(
                    "UPDATE shots SET order_key=?,updated_at=? WHERE id=? AND archived_at IS NULL",
                    (f"{ordinal * 10:08d}", now, shot_id),
                )
            if plan["reorder"]:
                self._audit(connection, actor, "SHOT_REORDERED", "episode", episode_id, {
                    "plan_hash": expected_plan_hash, "command": plan["reorder"], "ordered_shot_ids": active_ids,
                })
            stale_count = connection.execute(
                "UPDATE timeline_revisions SET status='STALE',updated_at=? WHERE episode_id=? AND status!='STALE'",
                (now, episode_id),
            ).rowcount
            self._audit(connection, actor, "SHOT_EDIT_PLAN_COMMITTED", "episode", episode_id, {
                "plan_hash": expected_plan_hash, "created_shot_ids": [item["id"] for item in created],
                "timeline_revisions_marked_stale": stale_count,
            })
            connection.execute(
                """INSERT INTO outbox_events (type,subject_type,subject_id,payload_json)
                VALUES ('episode.shot_plan.changed','episode',?,?)""",
                (episode_id, _json({"plan_hash": expected_plan_hash, "timeline_stale": bool(stale_count)})),
            )
            rows = self._active_shots(connection, episode_id)
            return {
                "plan_hash": expected_plan_hash,
                "ordering_token": self._ordering_token(rows),
                "ordered_shot_ids": [str(row["id"]) for row in rows],
                "created_shots": created,
                "archived_source_shot_ids": [str(split["shot_id"]) for split in plan["splits"]],
                "timeline_revisions_marked_stale": stale_count,
            }

    def _build_plan(
        self, connection: sqlite3.Connection, episode_id: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._episode(connection, episode_id)
        rows = self._active_shots(connection, episode_id)
        by_id = {str(row["id"]): row for row in rows}
        current_ids = list(by_id)
        actual_token = self._ordering_token(rows)
        supplied_token = str(payload.get("ordering_token", ""))
        issues: list[dict[str, Any]] = []
        if supplied_token != actual_token:
            issues.append({"code": "SHOT_ORDERING_STALE", "message": "镜头顺序已变化，请刷新后重试"})
        ordered_ids = list(current_ids)
        reorder = payload.get("reorder")
        if reorder:
            shot_id = str(reorder.get("shot_id", ""))
            before_id = reorder.get("before_shot_id")
            after_id = reorder.get("after_shot_id")
            anchors = [value for value in (before_id, after_id) if value is not None]
            if shot_id not in by_id:
                issues.append({"code": "SHOT_NOT_IN_EPISODE", "subject_id": shot_id, "message": "待移动镜头不属于当前分集"})
            elif int(reorder.get("expected_revision", 0)) != int(by_id[shot_id]["revision"]):
                issues.append({"code": "SHOT_REVISION_CONFLICT", "subject_id": shot_id, "message": "待移动镜头已变化"})
            if len(anchors) != 1 or str(anchors[0]) == shot_id or str(anchors[0]) not in by_id:
                issues.append({"code": "SHOT_REORDER_ANCHOR_INVALID", "message": "必须提供一个有效的 before 或 after 锚点"})
            if not issues:
                ordered_ids.remove(shot_id)
                anchor_id = str(anchors[0])
                index = ordered_ids.index(anchor_id) + (1 if after_id is not None else 0)
                ordered_ids.insert(index, shot_id)
        splits = list(payload.get("splits", []))
        used_sources: set[str] = set()
        reserved_codes = {str(row["code"]).casefold() for row in rows}
        normalized_splits: list[dict[str, Any]] = []
        for index, raw in enumerate(splits):
            shot_id = str(raw.get("shot_id", ""))
            shot = by_id.get(shot_id)
            first_code, second_code = str(raw.get("first_code", "")).strip(), str(raw.get("second_code", "")).strip()
            first_duration = int(raw.get("first_duration_ms", 0))
            if shot is None or shot_id in used_sources:
                issues.append({"code": "SHOT_SPLIT_SOURCE_INVALID", "subject_id": shot_id, "item_index": index, "message": "拆分来源无效或重复"})
                continue
            used_sources.add(shot_id)
            if int(raw.get("expected_revision", 0)) != int(shot["revision"]):
                issues.append({"code": "SHOT_REVISION_CONFLICT", "subject_id": shot_id, "item_index": index, "message": "待拆分镜头已变化"})
            duration = int(shot["target_duration_ms"])
            if first_duration <= 0 or first_duration >= duration:
                issues.append({"code": "SHOT_SPLIT_DURATION_INVALID", "subject_id": shot_id, "item_index": index, "message": "切分点必须位于镜头时长内部"})
            folded = [first_code.casefold(), second_code.casefold()]
            if not first_code or not second_code or folded[0] == folded[1] or any(code in reserved_codes for code in folded):
                issues.append({"code": "SHOT_CODE_CONFLICT", "subject_id": shot_id, "item_index": index, "message": "拆分后的两个镜头编号必须非空且在当前分集唯一"})
            reserved_codes.update(folded)
            normalized_splits.append({
                "shot_id": shot_id, "expected_revision": int(raw.get("expected_revision", 0)),
                "first_code": first_code, "second_code": second_code,
                "first_duration_ms": first_duration, "second_duration_ms": duration - first_duration,
                "source_duration_ms": duration,
            })
        snapshot = [self._snapshot_item(row) for row in rows]
        canonical = {
            "episode_id": episode_id, "ordering_token": supplied_token, "snapshot": snapshot,
            "reorder": reorder, "splits": normalized_splits,
        }
        plan_hash = hashlib.sha256(_json(canonical).encode()).hexdigest()
        return {
            "episode_id": episode_id, "ordering_token": actual_token, "reorder": reorder,
            "ordered_shot_ids": ordered_ids, "splits": normalized_splits,
            "source_snapshot": snapshot, "plan_hash": plan_hash, "valid": not issues, "issues": issues,
            "summary": {"reordered": bool(reorder), "split": len(normalized_splits)},
            "effects": {"timeline": "MARK_STALE", "selected_results": "NOT_COPIED", "asset_bindings": "COPY"},
        }

    @staticmethod
    def _episode(connection: sqlite3.Connection, episode_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT id,revision FROM episodes WHERE id=?", (episode_id,)).fetchone()
        if not row:
            raise DomainRuleError("EPISODE_NOT_FOUND", "分集不存在", {"episode_id": episode_id})
        return cast(sqlite3.Row, row)

    @staticmethod
    def _active_shots(connection: sqlite3.Connection, episode_id: str) -> list[sqlite3.Row]:
        return connection.execute(
            """SELECT id,episode_id,code,order_key,target_duration_ms,shot_type,status,current_revision_id,
            scene_id,revision FROM shots WHERE episode_id=? AND archived_at IS NULL
            ORDER BY CAST(order_key AS REAL),code,id""", (episode_id,),
        ).fetchall()

    @staticmethod
    def _snapshot_item(row: sqlite3.Row) -> dict[str, Any]:
        return {key: row[key] for key in (
            "id", "code", "order_key", "target_duration_ms", "current_revision_id", "scene_id", "revision",
        )}

    @classmethod
    def _ordering_token(cls, rows: list[sqlite3.Row]) -> str:
        return hashlib.sha256(_json([cls._snapshot_item(row) for row in rows]).encode()).hexdigest()

    @staticmethod
    def _copy_asset_bindings(
        connection: sqlite3.Connection, source_id: str, child_id: str, now: str, actor: str,
    ) -> None:
        rows = connection.execute(
            "SELECT asset_id,role_in_shot FROM shot_asset_bindings WHERE shot_id=?", (source_id,),
        ).fetchall()
        connection.executemany(
            """INSERT INTO shot_asset_bindings
            (id,shot_id,asset_id,role_in_shot,created_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,1,'v2')""",
            [(str(uuid.uuid4()), child_id, row["asset_id"], row["role_in_shot"], now, actor) for row in rows],
        )

    @staticmethod
    def _replace_group_memberships(
        connection: sqlite3.Connection, source_id: str, child_ids: list[str], now: str, actor: str,
    ) -> None:
        memberships = connection.execute(
            "SELECT group_id,order_key FROM shot_group_members WHERE shot_id=?", (source_id,),
        ).fetchall()
        for membership in memberships:
            group_id = str(membership["group_id"])
            rows = connection.execute(
                "SELECT shot_id FROM shot_group_members WHERE group_id=? ORDER BY order_key,shot_id", (group_id,),
            ).fetchall()
            ids = [str(row["shot_id"]) for row in rows]
            position = ids.index(source_id)
            ids[position:position + 1] = child_ids
            connection.execute("DELETE FROM shot_group_members WHERE group_id=?", (group_id,))
            connection.executemany(
                "INSERT INTO shot_group_members (group_id,shot_id,order_key,created_at,created_by) VALUES (?,?,?,?,?)",
                [(group_id, shot_id, f"{index:04d}", now, actor) for index, shot_id in enumerate(ids, 1)],
            )
            connection.execute(
                "UPDATE shot_groups SET revision=revision+1,updated_at=? WHERE id=?", (now, group_id),
            )

    @staticmethod
    def _audit(
        connection: sqlite3.Connection, actor: str, action: str, subject_type: str,
        subject_id: str, metadata: dict[str, Any],
    ) -> None:
        connection.execute(
            """INSERT INTO audit_events
            (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
            VALUES (?,'director',?,?,?,?,?)""",
            (actor, action, subject_type, subject_id, action, _json(metadata)),
        )
