"""Dedicated production read models; these are never used as write authority."""

from __future__ import annotations

import json
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


class ProductionReadModelService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def episode(self, episode_id: str, query: str | None = None, limit: int = 200) -> dict[str, Any]:
        bounded_limit = max(1, min(limit, 1000))
        with self.database.connect() as connection:
            episode = connection.execute(
                """SELECT e.*, se.project_id, se.code AS season_code, p.code AS project_code,
                p.title AS project_title FROM episodes e JOIN seasons se ON se.id=e.season_id
                JOIN projects p ON p.id=se.project_id WHERE e.id=?""",
                (episode_id,),
            ).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
            params: list[Any] = [episode_id]
            filter_sql = ""
            if query:
                filter_sql = " AND (s.code LIKE ? OR sr.fields_json LIKE ?)"
                params.extend([f"%{query}%", f"%{query}%"])
            params.append(bounded_limit)
            rows = connection.execute(
                f"""SELECT s.id, s.code, s.order_key, s.target_duration_ms, s.shot_type, s.status,
                s.current_revision_id, s.revision, sr.revision_no, sr.fields_json,
                (SELECT COUNT(*) FROM media_assets ma WHERE ma.owner_id=s.id) AS media_asset_count,
                (SELECT COUNT(*) FROM generation_intents gi WHERE gi.owner_id=s.id) AS generation_intent_count,
                (SELECT COUNT(*) FROM generation_variants gv JOIN generation_intents gi ON gi.id=gv.intent_id WHERE gi.owner_id=s.id) AS variant_count,
                (SELECT COUNT(*) FROM jobs j WHERE j.subject_id=s.id AND j.state IN ('QUEUED','CLAIMED','RUNNING','CANCEL_REQUESTED')) AS running_job_count,
                (SELECT COUNT(*) FROM selections se JOIN media_assets ma ON ma.id=se.media_asset_id WHERE ma.owner_id=s.id) AS selected_media_count
                FROM shots s LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
                WHERE s.episode_id=? {filter_sql} ORDER BY CAST(s.order_key AS REAL), s.code LIMIT ?""",
                params,
            ).fetchall()
            binding = connection.execute(
                """SELECT
                EXISTS(SELECT 1 FROM project_profile_bindings ppb WHERE ppb.project_id=? AND ppb.status IN ('ACTIVE', 'SELECTED_CANDIDATE')) AS profile_bound,
                EXISTS(SELECT 1 FROM project_plan_bindings ppb WHERE ppb.project_id=?) AS plan_bound,
                EXISTS(SELECT 1 FROM delivery_targets dt WHERE dt.project_id=? AND dt.status='ACTIVE') AS delivery_bound""",
                (episode["project_id"], episode["project_id"], episode["project_id"]),
            ).fetchone()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            fields = json.loads(item.pop("fields_json") or "{}")
            blockers: list[str] = []
            if not binding["profile_bound"]:
                blockers.append("PROFILE_NOT_BOUND")
            if not binding["plan_bound"]:
                blockers.append("PRODUCTION_PLAN_NOT_BOUND")
            if not binding["delivery_bound"]:
                blockers.append("DELIVERY_TARGET_NOT_BOUND")
            if item["status"] != "READY":
                blockers.append("SHOT_NOT_PRODUCTION_READY")
            if item["running_job_count"]:
                next_action = "查看运行中任务"
            elif blockers:
                next_action = "处理阻塞项"
            elif item["selected_media_count"] == 0:
                next_action = "导入或生成候选并选择"
            else:
                next_action = "进入审核"
            item.update(
                {
                    "keywords": fields.get("keywords", []),
                    "prompt_snapshot": fields.get("prompt_snapshot"),
                    "current_revision": fields,
                    "blockers": blockers,
                    "next_action": next_action,
                }
            )
            items.append(item)
        return {
            "episode": dict(episode),
            "items": items,
            "page": {"next_cursor": None, "has_more": False},
            "request_shape": "single_query_read_model",
        }

    def summary(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS shot_count,
                SUM(CASE WHEN status='READY' THEN 1 ELSE 0 END) AS ready_count,
                SUM(CASE WHEN status='READY' THEN 0 ELSE 1 END) AS blocked_count,
                (SELECT COUNT(*) FROM media_assets ma JOIN shots s ON s.id=ma.owner_id WHERE s.episode_id=?) AS media_asset_count,
                (SELECT COUNT(*) FROM jobs j JOIN shots s ON s.id=j.subject_id WHERE s.episode_id=? AND j.state IN ('QUEUED','CLAIMED','RUNNING','CANCEL_REQUESTED')) AS running_job_count
                FROM shots WHERE episode_id=?""",
                (episode_id, episode_id, episode_id),
            ).fetchone()
        return {"episode_id": episode_id, **dict(row)}

    def shot_detail(self, shot_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT s.*, e.code AS episode_code, e.id AS episode_id, sr.revision_no, sr.fields_json,
                (SELECT COUNT(*) FROM media_assets ma WHERE ma.owner_id=s.id) AS media_asset_count,
                (SELECT COUNT(*) FROM generation_variants gv JOIN generation_intents gi ON gi.id=gv.intent_id WHERE gi.owner_id=s.id) AS variant_count
                FROM shots s JOIN episodes e ON e.id=s.episode_id LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
                WHERE s.id=?""",
                (shot_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
        item = dict(row)
        item["current_revision"] = json.loads(item.pop("fields_json") or "{}")
        item["blockers"] = [] if item["status"] == "READY" else ["SHOT_NOT_PRODUCTION_READY"]
        item["next_action"] = "进入审核" if not item["blockers"] else "补全镜头字段并标记 production-ready"
        return item


class SearchService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def rebuild(self) -> int:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM fts_search")
            rows = connection.execute(
                """SELECT p.id AS project_id, 'PROJECT' AS subject_type, p.id AS subject_id, p.code || ' ' || p.title AS content FROM projects p
                UNION ALL SELECT se.project_id, 'SEASON', se.id, se.code || ' ' || COALESCE(se.title, '') FROM seasons se
                UNION ALL SELECT se.project_id, 'EPISODE', e.id, e.code || ' ' || COALESCE(e.title, '') FROM episodes e JOIN seasons se ON se.id=e.season_id
                UNION ALL SELECT se.project_id, 'SHOT', s.id, s.code || ' ' || COALESCE(sr.fields_json, '') FROM shots s JOIN episodes e ON e.id=s.episode_id JOIN seasons se ON se.id=e.season_id LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
                UNION ALL SELECT sd.project_id, 'SOURCE_DOCUMENT', sd.id, sd.code || ' ' || sd.title FROM source_documents sd"""
            ).fetchall()
            connection.executemany("INSERT INTO fts_search (project_id, subject_type, subject_id, content) VALUES (?, ?, ?, ?)", [tuple(row) for row in rows])
        return len(rows)

    def search(self, query: str, project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        with self.database.connect() as connection:
            indexed_count = connection.execute("SELECT COUNT(*) FROM fts_search").fetchone()[0]
        if not indexed_count:
            self.rebuild()
        bounded_limit = max(1, min(limit, 200))
        sql = "SELECT project_id, subject_type, subject_id, snippet(fts_search, 3, '[', ']', '…', 12) AS snippet FROM fts_search WHERE fts_search MATCH ?"
        params: list[Any] = [query.strip()]
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        sql += " LIMIT ?"
        params.append(bounded_limit)
        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
            if not rows:
                fallback = "SELECT project_id, subject_type, subject_id, content AS snippet FROM fts_search WHERE content LIKE ?"
                fallback_params: list[Any] = [f"%{query.strip()}%"]
                if project_id:
                    fallback += " AND project_id = ?"
                    fallback_params.append(project_id)
                fallback += " LIMIT ?"
                fallback_params.append(bounded_limit)
                rows = connection.execute(fallback, fallback_params).fetchall()
        return [dict(row) for row in rows]
