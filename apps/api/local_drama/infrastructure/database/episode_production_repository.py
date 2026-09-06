"""Canonical Episode Production read model.

This repository intentionally does not infer a Job stage from its free-form
``type`` or an intent ``purpose``.  Production identity comes from the v2 Job
scope/stage columns; working media comes from shot-scoped slots.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any, Iterable

from local_drama.application.production_spec_resolution import effective_video_profile
from local_drama.domain.duration import TARGET_DURATION_TECHNICAL_TOLERANCE_MS
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.policies import (
    SHOT_READINESS_ACTIONS,
    is_shot_production_ready,
)
from local_drama.domain.production_spec import canonical_production_plan, resolve_production_spec
from local_drama.infrastructure.database.sqlite import Database

STAGES = ("SHOT_PLANNING", "SHOT_IMAGE", "VIDEO", "AUDIO_SUBTITLE", "COMPOSE_QC")
ACTIVE_JOB_STATES = {"QUEUED", "CLAIMED", "RUNNING", "CANCEL_REQUESTED"}
FAILED_JOB_STATES = {"FAILED", "DEAD", "ORPHANED", "NEEDS_ATTENTION"}
ATTENTION_STATES = {"BLOCKED", "FAILED", "NEEDS_REVIEW", "STALE"}


def _marks(values: Iterable[object]) -> str:
    return ",".join("?" for _ in values)


def _json(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class SqliteEpisodeProductionReadRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _episode(connection: sqlite3.Connection, episode_id: str) -> dict[str, Any]:
        row = connection.execute(
            """SELECT e.id,e.code,e.title,e.production_status,e.target_duration_ms,e.revision,
            e.source_range_json,se.project_id,p.production_plan_version_id
            FROM episodes e JOIN seasons se ON se.id=e.season_id JOIN projects p ON p.id=se.project_id
            WHERE e.id=?""",
            (episode_id,),
        ).fetchone()
        if row is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "分集不存在", {"episode_id": episode_id})
        return dict(row)

    def overview_facts(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            episode = self._episode(connection, episode_id)
            rows = connection.execute(
                """SELECT s.id,s.code,s.order_key,s.status,s.revision,s.current_revision_id,s.target_duration_ms,s.updated_at,
                sr.id AS shot_revision_id,sr.revision_no,sr.fields_json
                FROM shots s LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
                WHERE s.episode_id=? AND s.archived_at IS NULL
                ORDER BY CAST(s.order_key AS REAL),s.code,s.id""",
                (episode_id,),
            ).fetchall()
            episode_summary, key_characters, key_scenes = self._episode_creative_context(
                connection, episode_id, rows
            )
            projected = self._project(connection, episode, rows)
            planning_row = connection.execute(
                """SELECT id,state,last_error_code AS error_code,
                last_error_detail_redacted AS error_message FROM jobs
                WHERE scope_episode_id=? AND scope_shot_id IS NULL AND stage_code='SHOT_PLANNING'
                ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                (episode_id,),
            ).fetchone()
            planning_job = dict(planning_row) if planning_row else None
            active_jobs = int(
                connection.execute(
                    f"""SELECT COUNT(*) FROM jobs WHERE scope_episode_id=?
                    AND stage_code IN ({_marks(STAGES)})
                    AND state IN ('QUEUED','CLAIMED','RUNNING','CANCEL_REQUESTED')""",
                    (episode_id, *STAGES),
                ).fetchone()[0]
            )
            run = self._active_run(connection, episode_id, str(episode["project_id"]))
            contract = self._episode_contract(connection, episode, rows)
        counts: dict[str, int] = defaultdict(int)
        for item in projected:
            counts[str(item["overall_state"])] += 1
        attention_count = sum(counts.get(state, 0) for state in ATTENTION_STATES)
        if not projected and planning_job and planning_job["state"] in FAILED_JOB_STATES:
            attention_count += 1
        if contract["replan_required"]:
            next_action = "REPLAN_EPISODE"
        elif attention_count:
            next_action = "RESOLVE_ATTENTION"
        elif active_jobs:
            next_action = "MONITOR_ACTIVE_JOBS"
        elif projected and all(item["overall_state"] == "READY" for item in projected):
            next_action = "OPEN_POST_EDIT"
        elif projected:
            next_action = str(projected[0]["next_action"])
        else:
            next_action = "OPEN_SHOT_PLANNING"
        allowed = ["START_PRODUCTION_RUN"] if run is None else []
        if contract["replan_required"] and run is None:
            allowed = ["REQUEST_REPLAN", "REVIEW_REPLAN"]
        if run is not None:
            status = str(run["status"])
            if status == "RUNNING":
                allowed.extend(["PAUSE_RUN", "CANCEL_RUN"])
            elif status == "PAUSED_HITL":
                allowed.extend(["RESUME_RUN", "CANCEL_RUN"])
        return {
            "episode_id": episode_id,
            "episode_revision": int(episode["revision"]),
            "project_id": str(episode["project_id"]),
            "episode_code": str(episode["code"]),
            "episode_title": episode.get("title"),
            "episode_summary": episode_summary,
            "key_characters": key_characters,
            "key_scenes": key_scenes,
            "shot_count": len(projected),
            "attention_count": attention_count,
            "active_job_count": active_jobs,
            "planning_job": planning_job,
            "next_action": next_action,
            "state_counts": dict(sorted(counts.items())),
            "active_run": run,
            "allowed_actions": allowed,
            **contract,
        }

    @staticmethod
    def _episode_contract(
        connection: sqlite3.Connection,
        episode: dict[str, Any],
        shot_rows: list[sqlite3.Row],
    ) -> dict[str, Any]:
        """Resolve the episode contract and explain why a replan is needed.

        The projection intentionally uses persisted project bindings and
        immutable timestamps only.  It never infers a duration from another
        episode and never mutates a project while reading it.
        """
        target_ms = int(episode.get("target_duration_ms") or 0)
        planned_ms = sum(int(row["target_duration_ms"] or 0) for row in shot_rows)
        production_plan_version_id = episode.get("production_plan_version_id")
        production_plan_version_no: int | None = None
        presentation: dict[str, Any] | None = None
        resolved_spec: dict[str, Any] | None = None
        plan_updated_at: str | None = None
        if production_plan_version_id:
            plan_row = connection.execute(
                "SELECT version_no,plan_json,updated_at FROM production_plan_versions WHERE id=?",
                (production_plan_version_id,),
            ).fetchone()
            if plan_row is not None:
                production_plan_version_no = int(plan_row["version_no"])
                plan_updated_at = str(plan_row["updated_at"] or "")
                raw_plan = _json(plan_row["plan_json"])
                if isinstance(raw_plan, dict):
                    try:
                        canonical = canonical_production_plan(raw_plan)
                        presentation = canonical["presentation"]
                    except DomainRuleError:
                        canonical = None
                    profile = effective_video_profile(connection, str(episode["project_id"]))
                    if canonical is not None and profile is not None:
                        workflow = connection.execute(
                            "SELECT status,content_json,node_bindings_json FROM workflow_versions WHERE id=?",
                            (profile.get("workflow_version_id"),),
                        ).fetchone()
                        if workflow is not None and str(workflow["status"]) == "PUBLISHED":
                            try:
                                resolved_spec = resolve_production_spec(
                                    canonical,
                                    _json(workflow["node_bindings_json"]),
                                    _json(workflow["content_json"]),
                                )
                            except DomainRuleError:
                                resolved_spec = None

        reasons: list[dict[str, Any]] = []
        tolerance_ms = TARGET_DURATION_TECHNICAL_TOLERANCE_MS
        if shot_rows and target_ms <= 0:
            reasons.append({"code": "TARGET_DURATION_INVALID", "message": "本集目标时长无效", "blocking": True})
        elif shot_rows and abs(planned_ms - target_ms) > tolerance_ms:
            reasons.append({
                "code": "TARGET_DURATION_MISMATCH",
                "message": "当前分镜总时长与本集目标时长不匹配",
                "blocking": True,
                "target_duration_ms": target_ms,
                "planned_duration_ms": planned_ms,
                "tolerance_ms": tolerance_ms,
            })
        latest_shot_at = max((str(row["updated_at"] or "") for row in shot_rows), default="")
        if shot_rows and plan_updated_at and latest_shot_at and plan_updated_at > latest_shot_at:
            reasons.append({
                "code": "PRODUCTION_PLAN_CHANGED",
                "message": "项目生产计划版本在当前镜头计划之后发生变化",
                "blocking": True,
                "production_plan_version_id": str(production_plan_version_id),
                "production_plan_version_no": production_plan_version_no,
            })
        replan_required = bool(reasons)
        return {
            "target_duration_ms": target_ms,
            "planned_duration_ms": planned_ms,
            "production_plan_version_id": str(production_plan_version_id) if production_plan_version_id else None,
            "production_plan_version_no": production_plan_version_no,
            "resolved_presentation": (resolved_spec or {}).get("delivery") or presentation,
            "resolved_production_spec": resolved_spec,
            "plan_stale": any(item["code"] == "PRODUCTION_PLAN_CHANGED" for item in reasons),
            "replan_required": replan_required,
            "replan_reasons": reasons,
            "replan_draft": None,
            "replan_job": None,
        }

    @staticmethod
    def _episode_creative_context(
        connection: sqlite3.Connection,
        episode_id: str,
        shot_rows: list[sqlite3.Row],
    ) -> tuple[str | None, list[str], list[str]]:
        """Return a compact creator-facing projection from committed episode facts."""
        summary_parts: list[str] = []
        for row in shot_rows:
            fields = _json(row["fields_json"])
            value = next(
                (
                    str(fields[key]).strip()
                    for key in ("summary", "creative_intent", "subject_action", "action", "visual")
                    if fields.get(key) and str(fields[key]).strip()
                ),
                "",
            )
            if value and value not in summary_parts:
                summary_parts.append(value)
            if len(summary_parts) >= 3:
                break

        bound_characters = [
            str(row["name"])
            for row in connection.execute(
                """SELECT DISTINCT sa.name FROM story_assets sa
                JOIN shot_asset_bindings sab ON sab.asset_id=sa.id
                JOIN shots s ON s.id=sab.shot_id
                WHERE s.episode_id=? AND s.archived_at IS NULL
                AND sa.status='ACTIVE' AND sa.kind='CHARACTER'
                ORDER BY sa.name LIMIT 8""",
                (episode_id,),
            ).fetchall()
        ]
        dialogue_characters = [
            str(row["speaker"])
            for row in connection.execute(
                """SELECT DISTINCT dl.speaker FROM dialogue_lines dl
                WHERE dl.episode_id=? AND TRIM(dl.speaker)<>''
                AND dl.speaker NOT IN ('旁白','NARRATOR')
                ORDER BY dl.speaker LIMIT 8""",
                (episode_id,),
            ).fetchall()
        ]
        characters = list(dict.fromkeys([*bound_characters, *dialogue_characters]))[:8]
        asset_scenes = [
            str(row["name"])
            for row in connection.execute(
                """SELECT DISTINCT sa.name FROM story_assets sa
                JOIN shot_asset_bindings sab ON sab.asset_id=sa.id
                JOIN shots s ON s.id=sab.shot_id
                WHERE s.episode_id=? AND s.archived_at IS NULL
                AND sa.status='ACTIVE' AND sa.kind='SCENE'
                ORDER BY sa.name LIMIT 8""",
                (episode_id,),
            ).fetchall()
        ]
        master_scenes = [
            str(row["title"])
            for row in connection.execute(
                """SELECT DISTINCT sc.title FROM episode_scene_ranges esr
                JOIN scenes sc ON sc.id=esr.scene_id
                WHERE esr.episode_id=? ORDER BY esr.ordinal LIMIT 8""",
                (episode_id,),
            ).fetchall()
        ]
        scenes = list(dict.fromkeys([*asset_scenes, *master_scenes]))[:8]
        summary = "；".join(part.rstrip("；。") for part in summary_parts)
        if summary:
            summary += "。"
        return (summary[:600] or None), characters, scenes

    def shot_facts(
        self,
        episode_id: str,
        *,
        cursor: int,
        limit: int,
        states: set[str],
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            episode = self._episode(connection, episode_id)
            rows = connection.execute(
                """SELECT s.id,s.code,s.order_key,s.status,s.revision,s.current_revision_id,
                sr.id AS shot_revision_id,sr.revision_no,sr.fields_json
                FROM shots s LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
                WHERE s.episode_id=? AND s.archived_at IS NULL
                ORDER BY CAST(s.order_key AS REAL),s.code,s.id""",
                (episode_id,),
            ).fetchall()
            projected = self._project(connection, episode, rows)
        filtered = [item for item in projected if not states or item["overall_state"] in states]
        items = filtered[cursor : cursor + limit]
        next_cursor = cursor + limit if cursor + limit < len(filtered) else None
        return {
            "items": items,
            "cursor": cursor,
            "limit": limit,
            "total": len(filtered),
            "next_cursor": next_cursor,
            "filters": sorted(states),
        }

    def changes(self, episode_id: str, *, after: int, limit: int) -> dict[str, Any]:
        with self.database.connect() as connection:
            episode = self._episode(connection, episode_id)
            rows = connection.execute(
                """SELECT event_id,type,subject_type,subject_id,occurred_at,payload_json
                FROM outbox_events
                WHERE project_id=? AND event_id>? AND (
                  json_extract(payload_json,'$.episode_id')=?
                  OR subject_id=?
                  OR subject_id IN (SELECT id FROM shots WHERE episode_id=?)
                  OR subject_id IN (SELECT id FROM jobs WHERE scope_episode_id=?)
                ) ORDER BY event_id LIMIT ?""",
                (episode["project_id"], after, episode_id, episode_id, episode_id, episode_id, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        selected = rows[:limit]
        items = [
            {
                "sequence": int(row["event_id"]),
                "event_type": str(row["type"]),
                "subject_type": str(row["subject_type"]),
                "subject_id": str(row["subject_id"]),
                "occurred_at": str(row["occurred_at"]),
                "payload": _json(row["payload_json"]),
            }
            for row in selected
        ]
        return {
            "items": items,
            "after": after,
            "next_after": int(selected[-1]["event_id"]) if selected else after,
            "has_more": has_more,
        }

    @staticmethod
    def _active_run(connection: sqlite3.Connection, episode_id: str, project_id: str) -> dict[str, Any] | None:
        row = connection.execute(
            """SELECT r.id,r.status,r.revision,r.updated_at,
            r.pending_gate_json,r.machine_context_json
            FROM automation_workflow_runs r JOIN automation_workflows w ON w.id=r.workflow_id
            WHERE r.project_id=?
            AND json_extract(w.definition_json,'$.nodes[0].metadata.episode_id')=?
            AND r.status IN ('RUNNING','PAUSED_HITL')
            ORDER BY r.updated_at DESC,r.id DESC LIMIT 1""",
            (project_id, episode_id),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["pending_gate"] = _json(result.pop("pending_gate_json", "{}"))
        result["machine_context"] = _json(result.pop("machine_context_json", "{}"))
        return result

    def _project(
        self,
        connection: sqlite3.Connection,
        episode: dict[str, Any],
        shot_rows: list[sqlite3.Row],
    ) -> list[dict[str, Any]]:
        shot_ids = [str(row["id"]) for row in shot_rows]
        if not shot_ids:
            return []
        marks = _marks(shot_ids)
        jobs_by_shot_stage: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in connection.execute(
            f"""SELECT id,scope_shot_id,stage_code,state,last_error_code,created_at
            FROM jobs WHERE scope_episode_id=? AND scope_shot_id IN ({marks})
            AND stage_code IN ({_marks(STAGES)})
            ORDER BY created_at DESC,id DESC""",
            (episode["id"], *shot_ids, *STAGES),
        ).fetchall():
            jobs_by_shot_stage[(str(row["scope_shot_id"]), str(row["stage_code"]))].append(dict(row))

        slots_by_shot: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        selected_ids: list[str] = []
        for row in connection.execute(
            f"""SELECT ws.shot_id,ws.slot_type,ws.media_version_id,ws.revision,
            mv.revision AS media_revision,mv.integrity_status,mv.stage,mv.mime_type
            FROM shot_working_media_slots ws JOIN media_versions mv ON mv.id=ws.media_version_id
            WHERE ws.shot_id IN ({marks})""",
            shot_ids,
        ).fetchall():
            slots_by_shot[str(row["shot_id"])][str(row["slot_type"])] = dict(row)
            selected_ids.append(str(row["media_version_id"]))

        working_lineage_by_shot: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self._working_lineage(connection, shot_ids):
            working_lineage_by_shot[str(row["shot_id"])].append(row)

        candidate_counts: dict[tuple[str, str], int] = defaultdict(int)
        media_rows = connection.execute(
            f"""SELECT resolved.shot_id,ma.media_kind,mv.stage,COUNT(*) AS candidate_count
            FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id=ma.id
            JOIN (
              SELECT id AS owner_id,id AS shot_id,'SHOT' AS owner_type FROM shots WHERE id IN ({marks})
              UNION ALL
              SELECT gv.id,gi.owner_id,'GENERATION_VARIANT' FROM generation_variants gv
              JOIN generation_intents gi ON gi.id=gv.intent_id
              WHERE gi.owner_type='SHOT' AND gi.owner_id IN ({marks})
            ) resolved ON resolved.owner_type=ma.owner_type AND resolved.owner_id=ma.owner_id
            GROUP BY resolved.shot_id,ma.media_kind,mv.stage""",
            (*shot_ids, *shot_ids),
        ).fetchall()
        for row in media_rows:
            kind = "KEYFRAME" if str(row["media_kind"]) == "IMAGE" and str(row["stage"]) == "KEYFRAME" else None
            if str(row["media_kind"]) == "VIDEO" and str(row["stage"]) in {"PROXY", "FORMAL"}:
                kind = "VIDEO"
            if kind:
                candidate_counts[(str(row["shot_id"]), kind)] += int(row["candidate_count"])

        qc_by_media: dict[str, str] = {}
        reviews_by_media: dict[str, str] = {}
        self._load_quality(connection, selected_ids, qc_by_media, reviews_by_media)

        dialogue_by_shot: dict[str, dict[str, Any]] = defaultdict(lambda: {"lines": 0, "candidates": 0, "selected": []})
        for row in connection.execute(
            f"""SELECT dl.shot_id,dl.id AS line_id,
            (SELECT tr.id FROM dialogue_text_revisions tr WHERE tr.dialogue_line_id=dl.id
             ORDER BY tr.revision_no DESC LIMIT 1) AS latest_text_revision_id,
            (SELECT COUNT(*) FROM tts_candidates tc WHERE tc.dialogue_text_revision_id=(
              SELECT tr2.id FROM dialogue_text_revisions tr2 WHERE tr2.dialogue_line_id=dl.id
              ORDER BY tr2.revision_no DESC LIMIT 1)) AS candidate_count,
            dcs.id AS selection_id,dcs.source_text_revision_id,tc.media_version_id
            FROM dialogue_lines dl
            LEFT JOIN dialogue_candidate_selections dcs ON dcs.id=(
              SELECT d2.id FROM dialogue_candidate_selections d2 WHERE d2.dialogue_line_id=dl.id
              ORDER BY d2.created_at DESC,d2.id DESC LIMIT 1)
            LEFT JOIN tts_candidates tc ON tc.id=dcs.tts_candidate_id
            WHERE dl.shot_id IN ({marks}) ORDER BY dl.code,dl.id""",
            shot_ids,
        ).fetchall():
            fact = dialogue_by_shot[str(row["shot_id"])]
            fact["lines"] += 1
            fact["candidates"] += int(row["candidate_count"])
            if row["selection_id"]:
                fact["selected"].append(dict(row))
        audio_ids = [
            str(item["media_version_id"])
            for fact in dialogue_by_shot.values()
            for item in fact["selected"]
            if item.get("media_version_id")
        ]
        self._load_quality(connection, audio_ids, qc_by_media, reviews_by_media)

        constraints_by_shot: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in connection.execute(
            f"""SELECT id,from_shot_id,to_shot_id,boundary_revision,is_stale,stale_reason,
            compatibility_status,enforcement FROM shot_transition_constraints
            WHERE from_shot_id IN ({marks}) OR to_shot_id IN ({marks})""",
            (*shot_ids, *shot_ids),
        ).fetchall():
            item = dict(row)
            constraints_by_shot[str(row["from_shot_id"])].append(item)
            constraints_by_shot[str(row["to_shot_id"])].append(item)

        timeline = connection.execute(
            "SELECT id,revision_no FROM timeline_revisions WHERE episode_id=? ORDER BY revision_no DESC LIMIT 1",
            (episode["id"],),
        ).fetchone()
        timeline_media = set()
        if timeline is not None:
            timeline_media = {
                str(row[0])
                for row in connection.execute(
                    "SELECT media_version_id FROM timeline_items WHERE timeline_revision_id=? AND media_version_id IS NOT NULL",
                    (timeline["id"],),
                ).fetchall()
            }

        return [
            self._shot(
                dict(row), jobs_by_shot_stage, slots_by_shot, candidate_counts,
                qc_by_media, reviews_by_media, dialogue_by_shot,
                constraints_by_shot, timeline, timeline_media, working_lineage_by_shot,
            )
            for row in shot_rows
        ]

    @staticmethod
    def _working_lineage(connection: sqlite3.Connection, shot_ids: list[str]) -> list[dict[str, Any]]:
        """Load dependency revisions for canonical working media only.

        A shot may have many immutable historical variants.  Freshness belongs to
        the current working line, so this query starts from the shot-scoped slot
        and walks backwards to its owning variant and frozen inputs.
        """
        if not shot_ids:
            return []
        marks = _marks(shot_ids)
        rows = connection.execute(
            f"""SELECT ws.shot_id,ws.slot_type,ws.media_version_id,mv.revision AS media_revision,
            gv.id AS variant_id,gv.revision AS variant_revision,gv.is_stale,gv.stale_reason,
            gv.prompt_revision_id AS source_prompt_revision_id,spr.revision_no AS source_prompt_revision_no,
            cpr.id AS current_prompt_revision_id,cpr.revision_no AS current_prompt_revision_no,
            gv.capability_profile_version_id AS source_profile_version_id,
            source_profile.version_no AS source_profile_version_no,
            CASE
              WHEN shot_pref.id IS NOT NULL THEN CASE
                WHEN shot_pref_version.resolution_mode='EXPLICIT' AND shot_profile.status='PUBLISHED'
                  THEN shot_pref_version.execution_profile_version_id
                WHEN shot_pref_version.resolution_mode='AUTO' THEN auto_profile.id END
              WHEN episode_pref.id IS NOT NULL THEN CASE
                WHEN episode_pref_version.resolution_mode='EXPLICIT' AND episode_profile.status='PUBLISHED'
                  THEN episode_pref_version.execution_profile_version_id
                WHEN episode_pref_version.resolution_mode='AUTO' THEN auto_profile.id END
              WHEN project_pref.id IS NOT NULL THEN CASE
                WHEN project_pref_version.resolution_mode='EXPLICIT' AND project_profile.status='PUBLISHED'
                  THEN project_pref_version.execution_profile_version_id
                WHEN project_pref_version.resolution_mode='AUTO' THEN auto_profile.id END
              ELSE auto_profile.id END AS current_profile_version_id,
            CASE
              WHEN shot_pref.id IS NOT NULL THEN CASE
                WHEN shot_pref_version.resolution_mode='EXPLICIT' AND shot_profile.status='PUBLISHED'
                  THEN shot_profile.version_no
                WHEN shot_pref_version.resolution_mode='AUTO' THEN auto_profile.version_no END
              WHEN episode_pref.id IS NOT NULL THEN CASE
                WHEN episode_pref_version.resolution_mode='EXPLICIT' AND episode_profile.status='PUBLISHED'
                  THEN episode_profile.version_no
                WHEN episode_pref_version.resolution_mode='AUTO' THEN auto_profile.version_no END
              WHEN project_pref.id IS NOT NULL THEN CASE
                WHEN project_pref_version.resolution_mode='EXPLICIT' AND project_profile.status='PUBLISHED'
                  THEN project_profile.version_no
                WHEN project_pref_version.resolution_mode='AUTO' THEN auto_profile.version_no END
              ELSE auto_profile.version_no END AS current_profile_version_no,
            sr.id AS source_reference_id,sr.status AS source_reference_status,sr.revision AS source_reference_revision,
            sr.asset_state_id AS source_asset_state_id,source_state.revision AS source_asset_state_revision,
            COALESCE(shot_asset.asset_state_id,episode_asset.asset_state_id) AS current_asset_state_id,
            current_state.revision AS current_asset_state_revision,
            CASE WHEN sr.status='ACTIVE' THEN sr.id ELSE (
              SELECT current_ref.id FROM story_asset_references current_ref
              WHERE current_ref.story_asset_id=sr.story_asset_id
                AND current_ref.reference_kind=sr.reference_kind AND current_ref.status='ACTIVE'
                AND (current_ref.asset_state_id=COALESCE(shot_asset.asset_state_id,episode_asset.asset_state_id)
                     OR current_ref.asset_state_id IS NULL)
              ORDER BY current_ref.updated_at DESC,current_ref.id DESC LIMIT 1
            ) END AS current_reference_id,
            CASE WHEN sr.status='ACTIVE' THEN sr.revision ELSE (
              SELECT current_ref.revision FROM story_asset_references current_ref
              WHERE current_ref.story_asset_id=sr.story_asset_id
                AND current_ref.reference_kind=sr.reference_kind AND current_ref.status='ACTIVE'
                AND (current_ref.asset_state_id=COALESCE(shot_asset.asset_state_id,episode_asset.asset_state_id)
                     OR current_ref.asset_state_id IS NULL)
              ORDER BY current_ref.updated_at DESC,current_ref.id DESC LIMIT 1
            ) END AS current_reference_revision
            FROM shot_working_media_slots ws
            JOIN media_versions mv ON mv.id=ws.media_version_id
            JOIN media_assets ma ON ma.id=mv.media_asset_id AND ma.owner_type='GENERATION_VARIANT'
            JOIN generation_variants gv ON gv.id=ma.owner_id
            JOIN generation_intents gi ON gi.id=gv.intent_id AND gi.owner_type='SHOT' AND gi.owner_id=ws.shot_id
            LEFT JOIN prompt_revisions spr ON spr.id=gv.prompt_revision_id
            LEFT JOIN prompt_revisions cpr ON cpr.id=(
              SELECT latest_prompt.id FROM prompt_revisions latest_prompt
              WHERE latest_prompt.prompt_id=spr.prompt_id
              ORDER BY latest_prompt.revision_no DESC,latest_prompt.id DESC LIMIT 1)
            LEFT JOIN execution_profile_versions source_profile ON source_profile.id=gv.capability_profile_version_id
            LEFT JOIN generation_preference_sets shot_pref ON shot_pref.project_id=gi.project_id
              AND shot_pref.owner_type='SHOT' AND shot_pref.owner_id=ws.shot_id
              AND UPPER(shot_pref.capability)=UPPER(gi.purpose) AND shot_pref.status='ACTIVE'
            LEFT JOIN generation_preference_versions shot_pref_version ON shot_pref_version.id=shot_pref.current_version_id
            LEFT JOIN execution_profile_versions shot_profile ON shot_profile.id=shot_pref_version.execution_profile_version_id
            LEFT JOIN shots shot ON shot.id=ws.shot_id
            LEFT JOIN generation_preference_sets episode_pref ON episode_pref.project_id=gi.project_id
              AND episode_pref.owner_type='EPISODE' AND episode_pref.owner_id=shot.episode_id
              AND UPPER(episode_pref.capability)=UPPER(gi.purpose) AND episode_pref.status='ACTIVE'
            LEFT JOIN generation_preference_versions episode_pref_version ON episode_pref_version.id=episode_pref.current_version_id
            LEFT JOIN execution_profile_versions episode_profile ON episode_profile.id=episode_pref_version.execution_profile_version_id
            LEFT JOIN generation_preference_sets project_pref ON project_pref.project_id=gi.project_id
              AND project_pref.owner_type='PROJECT' AND project_pref.owner_id=gi.project_id
              AND UPPER(project_pref.capability)=UPPER(gi.purpose) AND project_pref.status='ACTIVE'
            LEFT JOIN generation_preference_versions project_pref_version ON project_pref_version.id=project_pref.current_version_id
            LEFT JOIN execution_profile_versions project_profile ON project_profile.id=project_pref_version.execution_profile_version_id
            LEFT JOIN execution_profile_versions auto_profile ON auto_profile.id=(
              SELECT candidate.id FROM execution_profile_versions candidate
              JOIN execution_profiles candidate_profile ON candidate_profile.id=candidate.execution_profile_id
              WHERE candidate.status='PUBLISHED' AND UPPER(candidate.capability)=UPPER(gi.purpose)
              ORDER BY candidate.updated_at DESC,candidate_profile.code,candidate.version_no DESC LIMIT 1)
            LEFT JOIN variant_input_bindings input ON input.variant_id=gv.id
            LEFT JOIN story_asset_references sr ON sr.media_version_id=input.media_version_id
            LEFT JOIN story_asset_states source_state ON source_state.id=sr.asset_state_id
            LEFT JOIN shot_asset_bindings shot_asset ON shot_asset.shot_id=ws.shot_id
              AND shot_asset.asset_id=sr.story_asset_id
            LEFT JOIN episode_asset_state_bindings episode_asset ON episode_asset.episode_id=shot.episode_id
              AND episode_asset.story_asset_id=sr.story_asset_id
            LEFT JOIN story_asset_states current_state
              ON current_state.id=COALESCE(shot_asset.asset_state_id,episode_asset.asset_state_id)
            WHERE ws.shot_id IN ({marks})
            ORDER BY ws.shot_id,ws.slot_type,gv.id,input.ordinal,input.id""",
            shot_ids,
        ).fetchall()
        return [dict(row) for row in rows]

    def _shot(
        self,
        shot: dict[str, Any],
        jobs: dict[tuple[str, str], list[dict[str, Any]]],
        slots: dict[str, dict[str, dict[str, Any]]],
        candidates: dict[tuple[str, str], int],
        qc: dict[str, str],
        reviews: dict[str, str],
        dialogue: dict[str, dict[str, Any]],
        constraints: dict[str, list[dict[str, Any]]],
        timeline: sqlite3.Row | None,
        timeline_media: set[str],
        working_lineage: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        shot_id = str(shot["id"])
        fields = _json(shot.get("fields_json"))
        stages: list[dict[str, Any]] = []
        blockers: list[dict[str, str]] = []

        planning_ready = is_shot_production_ready(
            shot.get("status"), shot.get("current_revision_id"), fields
        )
        if planning_ready:
            stages.append(self._stage("SHOT_PLANNING", "READY", "SHOT_REVISION_READY", None, list(SHOT_READINESS_ACTIONS)))
        else:
            stages.append(self._stage("SHOT_PLANNING", "BLOCKED", "SHOT_INTENT_INCOMPLETE", None, list(SHOT_READINESS_ACTIONS)))
            blockers.append(self._blocker("SHOT_INTENT_INCOMPLETE", "镜头意图尚未达到可生产状态。", "SHOT_STUDIO", "OPEN_DESIGN"))

        material_slots: list[dict[str, Any]] = []
        freshness: list[dict[str, Any]] = []
        for kind, stage_code in (("KEYFRAME", "SHOT_IMAGE"), ("VIDEO", "VIDEO")):
            slot = slots[shot_id].get(kind)
            selected_id = str(slot["media_version_id"]) if slot else None
            count = candidates[(shot_id, kind)]
            material_slots.append({
                "kind": kind,
                "candidate_count": count,
                "selected_version_id": selected_id,
                "machine_qc_state": qc.get(selected_id) if selected_id else None,
                "human_decision_id": reviews.get(selected_id) if selected_id else None,
            })
            material_stage = self._material_stage(stage_code, jobs[(shot_id, stage_code)], slot, count, planning_ready)
            lineage_rows = [row for row in working_lineage[shot_id] if row["slot_type"] == kind]
            lineage_edges = self._lineage_edges(lineage_rows)
            if material_stage["state"] == "READY" and any(edge["state"] == "STALE" for edge in lineage_edges):
                material_stage = self._stage(
                    stage_code, "STALE", "WORKING_MEDIA_DEPENDENCY_CHANGED", None, ["OPEN_SHOT_STUDIO"]
                )
                blockers.append(self._blocker(
                    "WORKING_MEDIA_STALE", "当前工作媒体的生成依赖已经变化。", "SHOT_STUDIO", "REGENERATE_WORKING_MEDIA"
                ))
            stages.append(material_stage)
            if slot:
                freshness.extend(lineage_edges or [{
                    "source_revision": f"shot:{shot.get('shot_revision_id') or 'none'}",
                    "target_revision": f"media:{selected_id}:r{slot['media_revision']}",
                    "state": "CURRENT",
                    "reason": "WORKING_SLOT_BOUND_TO_CURRENT_SHOT",
                }])

        audio = dialogue[shot_id]
        selected_audio = audio["selected"]
        audio_selected_id = str(selected_audio[0]["media_version_id"]) if selected_audio else None
        material_slots.append({
            "kind": "AUDIO",
            "candidate_count": int(audio["candidates"]),
            "selected_version_id": audio_selected_id,
            "machine_qc_state": qc.get(audio_selected_id) if audio_selected_id else None,
            "human_decision_id": reviews.get(audio_selected_id) if audio_selected_id else None,
        })
        stages.append(self._audio_stage(jobs[(shot_id, "AUDIO_SUBTITLE")], audio))
        for selected in selected_audio:
            current = str(selected["source_text_revision_id"]) == str(selected["latest_text_revision_id"])
            freshness.append({
                "source_revision": f"dialogue:{selected['latest_text_revision_id']}",
                "target_revision": f"audio-selection:{selected['selection_id']}",
                "state": "CURRENT" if current else "STALE",
                "reason": "AUDIO_MATCHES_LATEST_TEXT" if current else "DIALOGUE_TEXT_CHANGED",
            })

        video = slots[shot_id].get("VIDEO")
        video_id = str(video["media_version_id"]) if video else None
        compose_jobs = jobs[(shot_id, "COMPOSE_QC")]
        if compose_jobs and str(compose_jobs[0]["state"]) in ACTIVE_JOB_STATES:
            compose = self._stage("COMPOSE_QC", "RUNNING", "CANONICAL_JOB_ACTIVE", str(compose_jobs[0]["id"]), ["OPEN_SYSTEM_JOBS"])
        elif video_id and qc.get(video_id) in {"FAIL", "FAILED", "BLOCKED", "NEEDS_HITL"}:
            compose = self._stage("COMPOSE_QC", "BLOCKED", "MACHINE_QC_REQUIRES_ATTENTION", None, ["OPEN_REVIEW"])
            blockers.append(self._blocker("MACHINE_QC_REQUIRES_ATTENTION", "当前工作视频未通过机器质检。", "REVIEW", "OPEN_SELECTED_VIDEO"))
        elif video_id and timeline is not None and video_id in timeline_media:
            compose = self._stage("COMPOSE_QC", "READY", "CURRENT_VIDEO_IN_TIMELINE", None, ["OPEN_POST_EDIT"])
        elif video_id:
            compose = self._stage("COMPOSE_QC", "STALE", "TIMELINE_MISSING_CURRENT_VIDEO", None, ["OPEN_POST_EDIT"])
        else:
            compose = self._stage("COMPOSE_QC", "EMPTY", "WORKING_VIDEO_REQUIRED", None, ["OPEN_SHOT_STUDIO"])
        stages.append(compose)
        if video_id:
            assert video is not None
            included = timeline is not None and video_id in timeline_media
            freshness.append({
                "source_revision": f"media:{video_id}:r{video['media_revision']}",
                "target_revision": f"timeline:{timeline['id']}:r{timeline['revision_no']}" if timeline else None,
                "state": "CURRENT" if included else "STALE",
                "reason": "CURRENT_VIDEO_IN_TIMELINE" if included else "TIMELINE_MISSING_CURRENT_VIDEO",
            })

        for constraint in constraints[shot_id]:
            stale = bool(constraint["is_stale"])
            incompatible = str(constraint["compatibility_status"]).upper() in {"BLOCKED", "CONFLICT", "INCOMPATIBLE"}
            freshness.append({
                "source_revision": f"transition:{constraint['id']}:r{constraint['boundary_revision']}",
                "target_revision": f"shot:{shot.get('shot_revision_id') or 'none'}",
                "state": "STALE" if stale else "CURRENT",
                "reason": str(constraint.get("stale_reason") or constraint["compatibility_status"]),
            })
            if incompatible:
                blockers.append(self._blocker("CONTINUITY_CONFLICT", "相邻镜头衔接约束不兼容。", "SHOT_STUDIO", "OPEN_CONTINUITY"))
            elif stale:
                blockers.append(self._blocker("CONTINUITY_STALE", "相邻镜头衔接事实已过期。", "SHOT_STUDIO", "REFRESH_FRAME_BRIDGE"))

        overall = self._overall(stages, blockers)
        next_action = next(
            (action for stage in stages if stage["state"] in {"BLOCKED", "FAILED", "NEEDS_REVIEW", "STALE", "EMPTY"} for action in stage["allowed_actions"]),
            "OPEN_POST_EDIT",
        )
        return {
            "shot_id": shot_id,
            "shot_code": str(shot["code"]),
            "order_key": str(shot["order_key"]),
            "shot_readiness": {
                "status": str(shot["status"]),
                "ready": planning_ready,
                "allowed_actions": list(SHOT_READINESS_ACTIONS),
            },
            "overall_state": overall,
            "next_action": next_action,
            "stages": stages,
            "material_slots": material_slots,
            "freshness_edges": freshness,
            "blockers": blockers,
        }

    @staticmethod
    def _lineage_edges(rows: list[dict[str, Any]]) -> list[dict[str, str | None]]:
        if not rows:
            return []
        first = rows[0]
        media = f"media:{first['media_version_id']}:r{first['media_revision']}"
        edges: list[dict[str, str | None]] = []
        seen: set[tuple[str, str | None, str, str]] = set()

        def add(source: str, target: str | None, stale: bool, reason: str) -> None:
            key = (source, target, "STALE" if stale else "CURRENT", reason)
            if key not in seen:
                seen.add(key)
                edges.append({
                    "source_revision": source,
                    "target_revision": target,
                    "state": "STALE" if stale else "CURRENT",
                    "reason": reason,
                })

        add(
            f"variant:{first['variant_id']}:r{first['variant_revision']}",
            media,
            bool(first["is_stale"]),
            str(first.get("stale_reason") or ("VARIANT_STALE" if first["is_stale"] else "VARIANT_CURRENT")),
        )
        source_prompt = first.get("source_prompt_revision_id")
        if source_prompt:
            current_prompt = first.get("current_prompt_revision_id")
            add(
                f"prompt:{source_prompt}:r{first.get('source_prompt_revision_no')}",
                f"prompt:{current_prompt}:r{first.get('current_prompt_revision_no')}" if current_prompt else None,
                str(source_prompt) != str(current_prompt or ""),
                "PROMPT_CHANGED" if str(source_prompt) != str(current_prompt or "") else "PROMPT_CURRENT",
            )
        source_profile = first.get("source_profile_version_id")
        if source_profile:
            current_profile = first.get("current_profile_version_id")
            add(
                f"profile:{source_profile}:r{first.get('source_profile_version_no')}",
                f"profile:{current_profile}:r{first.get('current_profile_version_no')}" if current_profile else None,
                str(source_profile) != str(current_profile or ""),
                "PROFILE_CHANGED" if str(source_profile) != str(current_profile or "") else "PROFILE_CURRENT",
            )
        for row in rows:
            source_ref = row.get("source_reference_id")
            if source_ref:
                current_ref = row.get("current_reference_id")
                ref_stale = str(row.get("source_reference_status") or "").upper() != "ACTIVE"
                add(
                    f"asset-reference:{source_ref}:r{row.get('source_reference_revision')}",
                    f"asset-reference:{current_ref}:r{row.get('current_reference_revision')}" if current_ref else None,
                    ref_stale,
                    "ASSET_REFERENCE_CHANGED" if ref_stale else "ASSET_REFERENCE_CURRENT",
                )
            source_state = row.get("source_asset_state_id")
            if source_state:
                current_state = row.get("current_asset_state_id")
                state_stale = str(source_state) != str(current_state or "")
                add(
                    f"asset-state:{source_state}:r{row.get('source_asset_state_revision')}",
                    f"asset-state:{current_state}:r{row.get('current_asset_state_revision')}" if current_state else None,
                    state_stale,
                    "ASSET_STATE_CHANGED" if state_stale else "ASSET_STATE_CURRENT",
                )
        return edges

    @staticmethod
    def _stage(code: str, state: str, reason: str, job_id: str | None, actions: list[str]) -> dict[str, Any]:
        return {"stage_code": code, "state": state, "reason_code": reason, "active_job_id": job_id, "allowed_actions": actions}

    def _material_stage(self, code: str, jobs: list[dict[str, Any]], slot: dict[str, Any] | None, count: int, prerequisite: bool) -> dict[str, Any]:
        latest = jobs[0] if jobs else None
        active = latest if latest and str(latest["state"]) in ACTIVE_JOB_STATES else None
        failed = latest if latest and str(latest["state"]) in FAILED_JOB_STATES else None
        if active:
            return self._stage(code, "RUNNING", "CANONICAL_JOB_ACTIVE", str(active["id"]), ["OPEN_SYSTEM_JOBS"])
        if failed:
            return self._stage(code, "FAILED", str(failed.get("last_error_code") or "CANONICAL_JOB_FAILED"), str(failed["id"]), ["OPEN_SYSTEM_JOBS", "OPEN_SHOT_STUDIO"])
        if slot:
            return self._stage(code, "READY", "WORKING_SLOT_SELECTED", None, ["OPEN_SHOT_STUDIO"])
        if count:
            return self._stage(code, "NEEDS_REVIEW", "CANDIDATES_REQUIRE_WORKING_SELECTION", None, ["OPEN_SHOT_STUDIO", "OPEN_REVIEW"])
        if prerequisite:
            return self._stage(code, "EMPTY", "NO_CANDIDATES", None, ["OPEN_SHOT_STUDIO"])
        return self._stage(code, "BLOCKED", "SHOT_PLANNING_REQUIRED", None, ["OPEN_SHOT_STUDIO"])

    def _audio_stage(self, jobs: list[dict[str, Any]], audio: dict[str, Any]) -> dict[str, Any]:
        latest = jobs[0] if jobs else None
        active = latest if latest and str(latest["state"]) in ACTIVE_JOB_STATES else None
        failed = latest if latest and str(latest["state"]) in FAILED_JOB_STATES else None
        if active:
            return self._stage("AUDIO_SUBTITLE", "RUNNING", "CANONICAL_JOB_ACTIVE", str(active["id"]), ["OPEN_SYSTEM_JOBS"])
        if failed:
            return self._stage("AUDIO_SUBTITLE", "FAILED", str(failed.get("last_error_code") or "CANONICAL_JOB_FAILED"), str(failed["id"]), ["OPEN_SYSTEM_JOBS", "OPEN_SHOT_STUDIO"])
        if not audio["lines"]:
            return self._stage("AUDIO_SUBTITLE", "EMPTY", "NO_DIALOGUE_LINES", None, ["OPEN_SHOT_STUDIO"])
        current = sum(1 for item in audio["selected"] if str(item["source_text_revision_id"]) == str(item["latest_text_revision_id"]))
        if current == int(audio["lines"]):
            return self._stage("AUDIO_SUBTITLE", "READY", "ALL_LINES_HAVE_CURRENT_WORKING_AUDIO", None, ["OPEN_POST_AUDIO"])
        if audio["candidates"]:
            return self._stage("AUDIO_SUBTITLE", "NEEDS_REVIEW", "AUDIO_CANDIDATES_REQUIRE_SELECTION", None, ["OPEN_SHOT_STUDIO"])
        return self._stage("AUDIO_SUBTITLE", "EMPTY", "TTS_CANDIDATES_REQUIRED", None, ["OPEN_SHOT_STUDIO"])

    @staticmethod
    def _blocker(code: str, message: str, owner: str, action: str) -> dict[str, str]:
        return {"code": code, "message": message, "owner_route": owner, "repair_action": action}

    @staticmethod
    def _overall(stages: list[dict[str, Any]], blockers: list[dict[str, str]]) -> str:
        states = {str(stage["state"]) for stage in stages}
        if "BLOCKED" in states or any(item["code"] != "CONTINUITY_STALE" for item in blockers):
            return "BLOCKED"
        if blockers:
            return "STALE"
        for state in ("FAILED", "RUNNING", "NEEDS_REVIEW", "STALE", "EMPTY"):
            if state in states:
                return state
        return "READY"

    @staticmethod
    def _load_quality(
        connection: sqlite3.Connection,
        media_ids: list[str],
        qc: dict[str, str],
        reviews: dict[str, str],
    ) -> None:
        missing = list(dict.fromkeys(media_id for media_id in media_ids if media_id))
        if not missing:
            return
        marks = _marks(missing)
        for row in connection.execute(
            f"""SELECT m.subject_id,m.status FROM machine_check_runs m
            WHERE m.subject_type='MEDIA_VERSION' AND m.subject_id IN ({marks})
            AND m.id=(SELECT m2.id FROM machine_check_runs m2
              WHERE m2.subject_type='MEDIA_VERSION' AND m2.subject_id=m.subject_id
              ORDER BY m2.created_at DESC,m2.id DESC LIMIT 1)""",
            missing,
        ).fetchall():
            qc[str(row["subject_id"])] = str(row["status"])
        for row in connection.execute(
            f"""SELECT r.subject_id,r.id,r.decision,r.is_stale FROM review_decisions r
            WHERE r.subject_type='MEDIA_VERSION' AND r.subject_id IN ({marks})
            AND r.id=(SELECT r2.id FROM review_decisions r2
              WHERE r2.subject_type='MEDIA_VERSION' AND r2.subject_id=r.subject_id
              ORDER BY r2.created_at DESC,r2.id DESC LIMIT 1)""",
            missing,
        ).fetchall():
            if not bool(row["is_stale"]) and str(row["decision"]) != "VOIDED":
                reviews[str(row["subject_id"])] = str(row["id"])
