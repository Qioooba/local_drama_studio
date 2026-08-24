"""Dedicated production read models; these are never used as write authority."""

from __future__ import annotations

import json
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.policies import missing_shot_fields
from local_drama.infrastructure.database.sqlite import Database


class ProductionReadModelService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def episode(self, episode_id: str, query: str | None = None, limit: int = 200, cursor: int = 0) -> dict[str, Any]:
        bounded_limit = max(1, min(limit, 1000))
        bounded_cursor = max(0, int(cursor))
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
            params.extend([bounded_limit + 1, bounded_cursor])
            rows = connection.execute(
                f"""SELECT s.id, s.code, s.order_key, s.target_duration_ms, s.shot_type, s.status,
                s.current_revision_id, s.revision, sr.revision_no, sr.fields_json,
                (SELECT COUNT(*) FROM media_assets ma WHERE ma.owner_id=s.id) AS media_asset_count,
                (SELECT COUNT(*) FROM generation_intents gi WHERE gi.owner_id=s.id) AS generation_intent_count,
                (SELECT COUNT(*) FROM generation_variants gv JOIN generation_intents gi ON gi.id=gv.intent_id WHERE gi.owner_id=s.id) AS variant_count,
                (SELECT COUNT(*) FROM jobs j WHERE j.subject_id=s.id AND j.state IN ('QUEUED','CLAIMED','RUNNING','CANCEL_REQUESTED')) AS running_job_count,
                (SELECT COUNT(*) FROM selections se JOIN media_assets ma ON ma.id=se.media_asset_id WHERE ma.owner_id=s.id) AS selected_media_count
                FROM shots s LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
                WHERE s.episode_id=? AND s.archived_at IS NULL {filter_sql} ORDER BY CAST(s.order_key AS REAL), s.code LIMIT ? OFFSET ?""",
                params,
            ).fetchall()
            binding = connection.execute(
                """SELECT
                EXISTS(SELECT 1 FROM project_profile_bindings ppb WHERE ppb.project_id=? AND ppb.status IN ('ACTIVE', 'SELECTED_CANDIDATE')) AS profile_bound,
                EXISTS(SELECT 1 FROM project_plan_bindings ppb WHERE ppb.project_id=?) AS plan_bound,
                EXISTS(SELECT 1 FROM delivery_targets dt WHERE dt.project_id=? AND dt.status='ACTIVE') AS delivery_bound""",
                (episode["project_id"], episode["project_id"], episode["project_id"]),
            ).fetchone()
        has_more = len(rows) > bounded_limit
        items: list[dict[str, Any]] = []
        for row in rows[:bounded_limit]:
            item = dict(row)
            fields = json.loads(item.pop("fields_json") or "{}")
            missing_director_fields = missing_shot_fields(fields)
            blockers: list[str] = []
            if not binding["profile_bound"]:
                blockers.append("PROFILE_NOT_BOUND")
            if not binding["plan_bound"]:
                blockers.append("PRODUCTION_PLAN_NOT_BOUND")
            if not binding["delivery_bound"]:
                blockers.append("DELIVERY_TARGET_NOT_BOUND")
            if item["status"] != "READY":
                blockers.append("SHOT_NOT_PRODUCTION_READY")
            readiness_state = "PRODUCTION_READY" if item["status"] in {"READY", "GENERATING", "REVIEW", "APPROVED"} else "DIRECTED" if item["status"] in {"DIRECTED", "BLOCKED"} else "OUTLINE"
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
                    "missing_director_fields": missing_director_fields,
                    "production_readiness": {"state": readiness_state, "missing_fields": missing_director_fields, "blockers": blockers},
                    "blockers": blockers,
                    "next_action": next_action,
                }
            )
            items.append(item)
        return {
            "episode": dict(episode),
            "items": items,
            "page": {"cursor": bounded_cursor, "limit": bounded_limit, "next_cursor": bounded_cursor + bounded_limit if has_more else None, "has_more": has_more},
            "request_shape": "bounded_cursor_read_model",
        }

    def summary(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS shot_count,
                SUM(CASE WHEN status='READY' THEN 1 ELSE 0 END) AS ready_count,
                SUM(CASE WHEN status='READY' THEN 0 ELSE 1 END) AS blocked_count,
                (SELECT COUNT(*) FROM media_assets ma JOIN shots s ON s.id=ma.owner_id WHERE s.episode_id=?) AS media_asset_count,
                (SELECT COUNT(*) FROM jobs j JOIN shots s ON s.id=j.subject_id WHERE s.episode_id=? AND j.state IN ('QUEUED','CLAIMED','RUNNING','CANCEL_REQUESTED')) AS running_job_count
                FROM shots WHERE episode_id=? AND archived_at IS NULL""",
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

    def continuity_context(self, shot_id: str) -> dict[str, Any]:
        """Return adjacent immutable shot revisions and safe reference metadata."""
        facet_aliases = {
            "appearance": ("appearance", "character_appearance"),
            "costume": ("costume", "wardrobe"),
            "props": ("props", "prop"),
            "lighting": ("lighting", "light"),
            "spatial_direction": ("spatial_direction", "screen_direction"),
            "continuity": ("continuity", "continuity_refs"),
        }
        with self.database.connect() as connection:
            current = connection.execute(
                """SELECT s.id,s.episode_id FROM shots s WHERE s.id=?""", (shot_id,)
            ).fetchone()
            if current is None:
                raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
            rows = connection.execute(
                """SELECT s.id,s.code,s.order_key,s.status,s.target_duration_ms,s.current_revision_id,
                sr.revision_no,sr.is_frozen,sr.fields_json
                FROM shots s LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
                WHERE s.episode_id=? ORDER BY CAST(s.order_key AS REAL),s.code""",
                (current["episode_id"],),
            ).fetchall()
            current_index = next(index for index, row in enumerate(rows) if row["id"] == shot_id)
            adjacent = [
                rows[index]
                for index in (current_index - 1, current_index, current_index + 1)
                if 0 <= index < len(rows)
            ]
            adjacent_ids = [str(row["id"]) for row in adjacent]
            placeholders = ",".join("?" for _ in adjacent_ids)
            media_rows = connection.execute(
                f"""SELECT ma.owner_id,ma.id AS media_asset_id,ma.purpose,ma.media_kind,
                CASE WHEN ma.approved_version_id IS NOT NULL THEN 'APPROVED' ELSE 'SELECTED' END AS selection_state,
                COALESCE(ma.approved_version_id,ma.selected_version_id) AS media_version_id,
                mv.version_no,mv.stage,mv.integrity_status
                FROM media_assets ma JOIN media_versions mv
                  ON mv.id=COALESCE(ma.approved_version_id,ma.selected_version_id)
                WHERE ma.owner_type='SHOT' AND ma.owner_id IN ({placeholders})
                ORDER BY ma.owner_id,ma.purpose,ma.id""",
                adjacent_ids,
            ).fetchall()
            boundary_ids = adjacent_ids
            transition_rows = connection.execute(
                f"""SELECT id,from_shot_id,to_shot_id,constraint_type,enforcement,
                compatibility_status,is_stale,stale_reason,from_anchor_id,to_anchor_id,boundary_revision
                FROM shot_transition_constraints
                WHERE from_shot_id IN ({placeholders}) AND to_shot_id IN ({placeholders})
                ORDER BY from_shot_id,to_shot_id,id""",
                [*boundary_ids, *boundary_ids],
            ).fetchall()

        media_by_shot: dict[str, list[dict[str, Any]]] = {item: [] for item in adjacent_ids}
        for media in media_rows:
            item = dict(media)
            media_by_shot[str(item.pop("owner_id"))].append(item)

        def project_shot(row: Any, position: str) -> dict[str, Any]:
            fields = json.loads(row["fields_json"] or "{}")
            facets: dict[str, object | None] = {}
            for facet, aliases in facet_aliases.items():
                facets[facet] = next((fields[key] for key in aliases if fields.get(key) not in (None, "", [])), None)
            return {
                "position": position,
                "id": row["id"],
                "code": row["code"],
                "order_key": row["order_key"],
                "status": row["status"],
                "target_duration_ms": row["target_duration_ms"],
                "revision": {"id": row["current_revision_id"], "revision_no": row["revision_no"], "is_frozen": bool(row["is_frozen"])},
                "facets": facets,
                "missing_facets": [key for key, value in facets.items() if value is None],
                "references": media_by_shot[str(row["id"])],
            }

        positions: dict[str, dict[str, Any] | None] = {"previous": None, "current": None, "next": None}
        if current_index > 0:
            positions["previous"] = project_shot(rows[current_index - 1], "previous")
        positions["current"] = project_shot(rows[current_index], "current")
        if current_index + 1 < len(rows):
            positions["next"] = project_shot(rows[current_index + 1], "next")
        return {
            "episode_id": current["episode_id"],
            "selected_shot_id": shot_id,
            "shots": positions,
            "transitions": [dict(row) for row in transition_rows],
            "read_only": True,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }


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
        needle = query.strip()[:200]
        if not needle:
            return []
        bounded_limit = max(1, min(limit, 200))
        with self.database.connect() as connection:
            tables = {str(row["name"]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            job_columns = {
                str(row["name"]) for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            } if "jobs" in tables else set()
            fragments: list[str] = []
            if {"projects"} <= tables:
                fragments.append("""SELECT p.id project_id,'PROJECT' subject_type,p.id subject_id,
                    p.code || ' · ' || p.title label,'项目' context,
                    '/projects/' || p.id route,p.code || ' ' || p.title search_text FROM projects p""")
            if {"seasons"} <= tables:
                fragments.append("""SELECT se.project_id,'SEASON',se.id,
                    se.code || ' · ' || COALESCE(se.title,'未命名季度'),'季度 · ' || p.code,
                    '/projects/' || se.project_id,se.code || ' ' || COALESCE(se.title,'')
                    FROM seasons se JOIN projects p ON p.id=se.project_id""")
            if {"episodes", "seasons"} <= tables:
                fragments.append("""SELECT se.project_id,'EPISODE',e.id,
                    e.code || ' · ' || COALESCE(e.title,'未命名分集'),'分集 · ' || p.code,
                    '/projects/' || se.project_id || '/episodes/' || e.id || '/plan',
                    e.code || ' ' || COALESCE(e.title,'') FROM episodes e
                    JOIN seasons se ON se.id=e.season_id JOIN projects p ON p.id=se.project_id""")
            if {"scenes"} <= tables:
                fragments.append("""SELECT sc.project_id,'SCENE',sc.id,
                    sc.code || ' · ' || COALESCE(sc.title,'未命名场景'),
                    '场景 · ' || p.code || CASE WHEN sc.location IS NULL THEN '' ELSE ' · ' || sc.location END,
                    '/projects/' || sc.project_id || '/story?scene=' || sc.id,
                    sc.code || ' ' || COALESCE(sc.title,'') || ' ' || COALESCE(sc.location,'') || ' ' || COALESCE(sc.time_of_day,'')
                    FROM scenes sc JOIN projects p ON p.id=sc.project_id""")
            if {"shots", "episodes", "seasons"} <= tables:
                fragments.append("""SELECT se.project_id,'SHOT',sh.id,
                    sh.code || ' · ' || sh.shot_type,'镜头 · ' || e.code || ' · ' || p.code,
                    '/projects/' || se.project_id || '/episodes/' || e.id || '/direct/' || sh.id,
                    sh.code || ' ' || sh.shot_type || ' ' || sh.status || ' ' || COALESCE(sr.fields_json,'')
                    FROM shots sh JOIN episodes e ON e.id=sh.episode_id JOIN seasons se ON se.id=e.season_id
                    JOIN projects p ON p.id=se.project_id LEFT JOIN shot_revisions sr ON sr.id=sh.current_revision_id""")
            if {"source_documents"} <= tables:
                source_index_join = """LEFT JOIN (
                    SELECT project_id,subject_id,group_concat(content,' ') content FROM fts_search
                    WHERE subject_type='SOURCE_DOCUMENT' GROUP BY project_id,subject_id
                    ) source_index ON source_index.project_id=sd.project_id AND source_index.subject_id=sd.id""" if "fts_search" in tables else ""
                fragments.append(f"""SELECT sd.project_id,'SOURCE_DOCUMENT',sd.id,
                    sd.code || ' · ' || sd.title,'源文本 · ' || p.code,
                    '/projects/' || sd.project_id || '/story?document=' || sd.id,
                    sd.code || ' ' || sd.title || ' ' || COALESCE(source_index.content,'')
                    FROM source_documents sd JOIN projects p ON p.id=sd.project_id {source_index_join}""" if source_index_join else """SELECT sd.project_id,'SOURCE_DOCUMENT',sd.id,
                    sd.code || ' · ' || sd.title,'源文本 · ' || p.code,
                    '/projects/' || sd.project_id || '/story?document=' || sd.id,
                    sd.code || ' ' || sd.title FROM source_documents sd JOIN projects p ON p.id=sd.project_id""")
            if {"story_assets"} <= tables:
                fragments.append("""SELECT sa.project_id,'STORY_ASSET',sa.id,
                    sa.code || ' · ' || sa.name,sa.kind || '资产 · ' || p.code,
                    '/projects/' || sa.project_id || '/assets?asset=' || sa.id,
                    sa.code || ' ' || sa.name || ' ' || sa.kind || ' ' || sa.description
                    FROM story_assets sa JOIN projects p ON p.id=sa.project_id WHERE sa.status='ACTIVE'""")
            if {"generation_variants", "generation_intents"} <= tables:
                fragments.append("""SELECT gi.project_id,'GENERATION_VARIANT',gv.id,
                    '候选 #' || gv.variant_no || ' · ' || gv.variant_type,
                    '生成候选 · ' || COALESCE(e.code,p.code) || CASE WHEN sh.code IS NULL THEN '' ELSE ' · ' || sh.code END,
                    CASE WHEN sh.id IS NULL THEN '/projects/' || gi.project_id
                         ELSE '/projects/' || gi.project_id || '/episodes/' || e.id || '/direct/' || sh.id END,
                    gv.variant_type || ' ' || gv.status || ' ' || gv.branch_reason || ' ' || gi.purpose || ' ' || COALESCE(sh.code,'')
                    FROM generation_variants gv JOIN generation_intents gi ON gi.id=gv.intent_id
                    JOIN projects p ON p.id=gi.project_id LEFT JOIN shots sh ON gi.owner_type='SHOT' AND sh.id=gi.owner_id
                    LEFT JOIN episodes e ON e.id=sh.episode_id""")
            if {"review_decisions", "media_versions", "media_assets"} <= tables:
                fragments.append("""SELECT ma.project_id,'REVIEW',rd.id,
                    '审核 · ' || rd.decision,'媒体审核 · ' || COALESCE(e.code,p.code) || CASE WHEN sh.code IS NULL THEN '' ELSE ' · ' || sh.code END,
                    CASE WHEN e.id IS NULL THEN '/projects/' || ma.project_id
                         ELSE '/projects/' || ma.project_id || '/episodes/' || e.id || '/review?review=' || rd.id END,
                    rd.decision || ' ' || rd.subject_type || ' ' || COALESCE(rd.comment,'') || ' ' || COALESCE(sh.code,'')
                    FROM review_decisions rd JOIN media_versions mv ON rd.subject_type='MEDIA_VERSION' AND mv.id=rd.subject_id
                    JOIN media_assets ma ON ma.id=mv.media_asset_id JOIN projects p ON p.id=ma.project_id
                    LEFT JOIN generation_variants gv ON ma.owner_type='GENERATION_VARIANT' AND gv.id=ma.owner_id
                    LEFT JOIN generation_intents gi ON gi.id=gv.intent_id
                    LEFT JOIN shots sh ON (ma.owner_type='SHOT' AND sh.id=ma.owner_id) OR (gi.owner_type='SHOT' AND sh.id=gi.owner_id)
                    LEFT JOIN episodes e ON e.id=sh.episode_id""")
            if {"jobs"} <= tables:
                job_error_search = "COALESCE(j.last_error_code,'')" if "last_error_code" in job_columns else "''"
                fragments.append(f"""SELECT j.project_id,'JOB',j.id,
                    j.type || ' · ' || j.state,'任务 · ' || p.code || ' · ' || j.channel,
                    '/jobs?project_id=' || j.project_id || '&job=' || j.id,
                    j.type || ' ' || j.state || ' ' || j.channel || ' ' || j.subject_type || ' ' || {job_error_search}
                    FROM jobs j JOIN projects p ON p.id=j.project_id""")
            if not fragments:
                return []
            sql = """SELECT project_id,subject_type,subject_id,label,context,route,
                label || ' · ' || context AS snippet FROM (""" + " UNION ALL ".join(fragments) + ") entities WHERE instr(lower(search_text),lower(?))>0"
            params: list[Any] = [needle]
            if project_id:
                sql += " AND project_id=?"
                params.append(project_id)
            sql += " ORDER BY CASE subject_type WHEN 'PROJECT' THEN 1 WHEN 'EPISODE' THEN 2 WHEN 'SCENE' THEN 3 WHEN 'SHOT' THEN 4 WHEN 'STORY_ASSET' THEN 5 WHEN 'GENERATION_VARIANT' THEN 6 WHEN 'REVIEW' THEN 7 WHEN 'JOB' THEN 8 ELSE 9 END,label,subject_id LIMIT ?"
            params.append(bounded_limit)
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
