"""SQLite-backed global search index and query facts."""

from __future__ import annotations

from typing import Any

from local_drama.infrastructure.database.sqlite import Database


class SqliteSearchRepository:
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

