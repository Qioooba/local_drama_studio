"""Reviewable, episode-scoped AI re-planning.

The ordinary breakdown flow is intentionally append-only and is therefore
correct for an empty episode.  A production-spec or target-duration change is
different: an episode can already have frozen shots, working media and a
timeline.  This service provides the missing review/apply boundary for that
case.  It reuses the immutable source range and the local LLM breakdown job,
then applies only after the creator confirms a deterministic diff.

No existing shot, shot revision, media version or timeline row is deleted or
rewritten.  Mutable pointers move to new revisions and superseded working
facts are marked stale in the same transaction.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from local_drama.application.breakdown_contracts import director_intent_fields
from local_drama.application.breakdown_revisions import load_effective_breakdown_draft
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.production_spec_resolution import effective_video_profile
from local_drama.config import Settings
from local_drama.domain.duration import TARGET_DURATION_TECHNICAL_TOLERANCE_MS
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.production_spec import canonical_production_plan
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _decode(value: object, fallback: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError):
        return fallback


class EpisodeReplanStorageError(RuntimeError):
    """Safe, stage-labelled storage failure for the replan apply boundary.

    SQLite's exception text is limited to engine/schema diagnostics (for
    example, a missing column or a placeholder count).  We deliberately keep
    only that text plus a fixed application stage; request payloads, SQL
    parameters, and story content never become part of the error message.
    """

    def __init__(self, stage: str, cause: sqlite3.OperationalError) -> None:
        self.stage = stage
        self.cause_type = type(cause).__name__
        self.cause_message = " ".join(str(cause).split())[:240] or "sqlite operation failed"
        super().__init__(
            f"episode_replan storage failure stage={stage} "
            f"cause={self.cause_type}: {self.cause_message}"
        )


class EpisodeReplanService:
    """Build and apply a whole-episode replan without touching history."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    @staticmethod
    def _storage_stage(stage: str, operation: Any) -> Any:
        """Run one storage phase and attach a safe diagnostic stage."""
        try:
            return operation()
        except sqlite3.OperationalError as error:
            raise EpisodeReplanStorageError(stage, error) from error

    @staticmethod
    def _episode(connection: sqlite3.Connection, episode_id: str) -> sqlite3.Row:
        row = connection.execute(
            """SELECT e.id,e.code,e.title,e.revision,e.target_duration_ms,e.source_range_json,
            s.project_id,p.production_plan_version_id
            FROM episodes e JOIN seasons s ON s.id=e.season_id JOIN projects p ON p.id=s.project_id
            WHERE e.id=?""",
            (episode_id,),
        ).fetchone()
        if row is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
        return row

    @staticmethod
    def _source_scope(raw: object) -> tuple[int, int]:
        value = _decode(raw, {})
        start = value.get("start_paragraph") or value.get("source_paragraph_start")
        end = value.get("end_paragraph") or value.get("source_paragraph_end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            raise DomainRuleError("EPISODE_SOURCE_RANGE_REQUIRED", "本集还没有已确认的原文范围，请先完成分集大纲。")
        return start, end

    @staticmethod
    def _current_shots(connection: sqlite3.Connection, episode_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            """SELECT s.*,sr.fields_json,sr.revision_no AS current_revision_no,sr.is_frozen
            FROM shots s LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
            WHERE s.episode_id=? AND s.archived_at IS NULL
            ORDER BY CAST(s.order_key AS REAL),s.code,s.id""",
            (episode_id,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            fields = _decode(row["fields_json"], {})
            result.append({**dict(row), "fields": fields if isinstance(fields, dict) else {}})
        return result

    def _project_context(self, connection: sqlite3.Connection, project_id: str) -> dict[str, Any]:
        source = connection.execute(
            """SELECT i.id AS import_session_id
            FROM import_sessions i
            WHERE i.project_id=? AND i.status IN ('COMMITTED','BREAKDOWN_READY')
            AND EXISTS (SELECT 1 FROM audit_events ae WHERE ae.action='IMPORT_SESSION_COMMITTED'
              AND ae.subject_type='import_session' AND ae.subject_id=i.id)
            ORDER BY i.updated_at DESC,i.id DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
        # Prefer the project's explicit story-parse profile.  The fallback is
        # kept for legacy projects whose profile predates project bindings.
        profile = connection.execute(
            """SELECT epv.id
            FROM project_profile_bindings ppb
            JOIN execution_profile_versions epv ON epv.id=ppb.execution_profile_version_id
            WHERE ppb.project_id=? AND ppb.capability='LLM_STORY_PARSE'
              AND ppb.status IN ('ACTIVE','SELECTED_CANDIDATE') AND epv.status='PUBLISHED'
            ORDER BY ppb.updated_at DESC,epv.version_no DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
        if profile is None:
            profile = connection.execute(
                """SELECT id FROM execution_profile_versions
                WHERE capability='LLM_STORY_PARSE' AND status='PUBLISHED'
                ORDER BY updated_at DESC,version_no DESC,id DESC LIMIT 1"""
            ).fetchone()
        return {
            "import_session_id": str(source["import_session_id"]) if source else None,
            "profile_version_id": str(profile["id"]) if profile else None,
        }

    @staticmethod
    def _plan_snapshot(connection: sqlite3.Connection, project_id: str) -> dict[str, Any]:
        row = connection.execute(
            """SELECT p.production_plan_version_id,ppv.version_no,ppv.plan_json
            FROM projects p LEFT JOIN production_plan_versions ppv ON ppv.id=p.production_plan_version_id
            WHERE p.id=?""",
            (project_id,),
        ).fetchone()
        if row is None or not row["production_plan_version_id"]:
            return {"version_id": None, "version_no": None, "presentation": None, "resolved_spec": None}
        raw = _decode(row["plan_json"], {})
        presentation = None
        if isinstance(raw, dict):
            try:
                presentation = canonical_production_plan(raw)["presentation"]
            except DomainRuleError:
                presentation = None
        profile = effective_video_profile(connection, project_id)
        resolved = None
        if profile:
            workflow = connection.execute(
                "SELECT status,content_json,node_bindings_json FROM workflow_versions WHERE id=?",
                (profile.get("workflow_version_id"),),
            ).fetchone()
            if workflow and str(workflow["status"]) == "PUBLISHED" and isinstance(raw, dict):
                try:
                    from local_drama.domain.production_spec import resolve_production_spec

                    resolved = resolve_production_spec(
                        raw,
                        _decode(workflow["node_bindings_json"], {}),
                        _decode(workflow["content_json"], {}),
                    )
                except DomainRuleError:
                    resolved = None
        return {
            "version_id": str(row["production_plan_version_id"]),
            "version_no": int(row["version_no"]),
            "presentation": presentation,
            "resolved_spec": resolved,
        }

    @staticmethod
    def _latest_ready_draft(
        connection: sqlite3.Connection,
        project_id: str,
        episode_id: str,
        target_duration_ms: int,
        source_scope: dict[str, Any] | None,
    ) -> sqlite3.Row | None:
        rows = connection.execute(
            """SELECT * FROM script_breakdown_drafts WHERE project_id=? AND status='DRAFT_READY'
            ORDER BY updated_at DESC,id DESC""",
            (project_id,),
        ).fetchall()
        for row in rows:
            confidence = _decode(row["confidence_json"], {})
            if not isinstance(confidence, dict) or str(confidence.get("target_episode_id") or "") != episode_id:
                continue
            # Several immutable DRAFT_READY rows may coexist. Only the one
            # generated for the current target and confirmed source range is
            # eligible for this episode's review/apply surface.
            try:
                draft_target_ms = int(round(float(confidence.get("target_duration_seconds")) * 1000))
            except (TypeError, ValueError):
                continue
            if draft_target_ms != int(target_duration_ms):
                continue
            if isinstance(source_scope, dict):
                start = source_scope.get("start_paragraph") or source_scope.get("source_paragraph_start")
                end = source_scope.get("end_paragraph") or source_scope.get("source_paragraph_end")
                if confidence.get("source_paragraph_start") != start or confidence.get("source_paragraph_end") != end:
                    continue
            return row
        return None

    @staticmethod
    def _proposal(scene: dict[str, Any], shot: dict[str, Any], source_revision_id: str | None) -> dict[str, Any]:
        duration = shot.get("duration_seconds")
        try:
            duration_ms = int(round(float(duration) * 1000))
        except (TypeError, ValueError):
            duration_ms = 0
        if duration_ms <= 0:
            raise DomainRuleError("REPLAN_DRAFT_INVALID", "AI 重规划草稿包含无效镜头时长", {"duration_seconds": duration})
        fields = director_intent_fields(
            shot,
            scene,
            duration_ms=duration_ms,
            source_revision_id=source_revision_id,
        )
        return {
            "scene_no": int(scene.get("scene_no") or 0),
            "shot_no": int(shot.get("shot_no") or 0),
            "target_duration_ms": duration_ms,
            "shot_type": fields["shot_type"],
            "fields": fields,
            "characters": [str(item).strip() for item in (shot.get("characters") or scene.get("characters") or []) if str(item).strip()],
            "props": [str(item).strip() for item in (shot.get("props") or []) if str(item).strip()],
            "scene_title": str(scene.get("title") or f"第{int(scene.get('scene_no') or 0)}场").strip(),
        }

    def _plan_from_draft(self, connection: sqlite3.Connection, episode_id: str, draft: sqlite3.Row) -> dict[str, Any]:
        episode = self._episode(connection, episode_id)
        payload, effective_revision = load_effective_breakdown_draft(connection, draft)
        scenes = payload.get("scenes") if isinstance(payload, dict) else None
        if not isinstance(scenes, list) or not scenes:
            raise DomainRuleError("REPLAN_DRAFT_INVALID", "AI 重规划草稿没有场次建议")
        proposals: list[dict[str, Any]] = []
        for scene in scenes:
            if not isinstance(scene, dict):
                continue
            for shot in scene.get("shots") or []:
                if isinstance(shot, dict):
                    proposals.append(self._proposal(scene, shot, str(effective_revision["id"]) if effective_revision else None))
        if not proposals:
            raise DomainRuleError("REPLAN_DRAFT_INVALID", "AI 重规划草稿没有镜头建议")
        current = self._current_shots(connection, episode_id)
        diff: list[dict[str, Any]] = []
        for index, proposal in enumerate(proposals):
            before = current[index] if index < len(current) else None
            if before is None:
                diff.append({"action": "ADD", "shot_id": None, "before": None, "after": proposal})
            elif bool(before.get("is_frozen")):
                # Preserve the frozen row and its media as immutable history;
                # the active plan receives a replacement row on apply.
                diff.append({"action": "REPLACE_PROTECTED", "shot_id": str(before["id"]), "expected_revision": int(before["revision"]), "before": self._before(before), "after": proposal, "reason": "冻结 revision 保留为历史；当前计划创建替代镜头"})
            else:
                diff.append({"action": "MODIFY", "shot_id": str(before["id"]), "expected_revision": int(before["revision"]), "before": self._before(before), "after": proposal})
        for before in current[len(proposals):]:
            if bool(before.get("is_frozen")):
                diff.append({"action": "PROTECTED", "shot_id": str(before["id"]), "before": self._before(before), "after": None, "reason": "冻结 revision 不允许归档"})
            else:
                diff.append({"action": "ARCHIVE", "shot_id": str(before["id"]), "expected_revision": int(before["revision"]), "before": self._before(before), "after": None, "reason": "当前草稿不再包含该镜头；apply 只归档"})
        summary = {action: sum(item["action"] == action for item in diff) for action in ("KEEP", "ADD", "MODIFY", "ARCHIVE", "PROTECTED", "REPLACE_PROTECTED")}
        invalid = [item for item in diff if item["action"] == "PROTECTED" and item.get("after") is None]
        target_ms = int(episode["target_duration_ms"] or 0)
        planned_ms = sum(int(item["after"]["target_duration_ms"]) for item in diff if item.get("after"))
        canonical = {
            "episode_id": episode_id,
            "episode_revision": int(episode["revision"]),
            "draft_id": str(draft["id"]),
            "draft_revision": int(draft["revision"]),
            "effective_draft_revision_id": str(effective_revision["id"]) if effective_revision else None,
            "diff": diff,
        }
        return {
            "status": "DRAFT_READY",
            "episode_id": episode_id,
            "project_id": str(episode["project_id"]),
            "draft_id": str(draft["id"]),
            "draft_revision": int(draft["revision"]),
            "effective_draft_revision_id": str(effective_revision["id"]) if effective_revision else None,
            "expected_episode_revision": int(episode["revision"]),
            "target_duration_ms": target_ms,
            "planned_duration_ms": planned_ms,
            "source_scope": _decode(episode["source_range_json"], {}),
            "summary": summary,
            "diff": diff,
            "valid": not invalid,
            "issues": [{"code": "FROZEN_SHOT_WOULD_BE_REMOVED", "message": str(item.get("reason") or "冻结镜头不能从当前计划移除"), "shot_id": item.get("shot_id")} for item in invalid],
            "plan_hash": _hash(canonical),
            "production_plan": self._plan_snapshot(connection, str(episode["project_id"])),
        }

    @staticmethod
    def _before(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": str(row["code"]),
            "target_duration_ms": int(row["target_duration_ms"]),
            "shot_type": str(row["shot_type"]),
            "fields": row.get("fields") if isinstance(row.get("fields"), dict) else {},
            "revision": int(row["revision"]),
            "revision_no": int(row.get("current_revision_no") or 0),
            "is_frozen": bool(row.get("is_frozen")),
        }

    def overview(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            episode = self._episode(connection, episode_id)
            shots = self._current_shots(connection, episode_id)
            draft = self._latest_ready_draft(
                connection, str(episode["project_id"]), episode_id,
                int(episode["target_duration_ms"] or 0), _decode(episode["source_range_json"], {}),
            )
            latest_applied = connection.execute(
                """SELECT metadata_redacted_json FROM audit_events
                WHERE subject_type='episode' AND subject_id=? AND action='EPISODE_REPLAN_APPLIED'
                ORDER BY occurred_at DESC,rowid DESC LIMIT 1""",
                (episode_id,),
            ).fetchone()
            project_plan = self._plan_snapshot(connection, str(episode["project_id"]))
            active_job = connection.execute(
                """SELECT id,state,input_snapshot_json FROM jobs
                WHERE project_id=? AND type='SCRIPT_BREAKDOWN_LOCAL_LLM'
                  AND json_extract(input_snapshot_json,'$.target_episode_id')=?
                  AND state IN ('QUEUED','CLAIMED','RUNNING','CANCEL_REQUESTED')
                ORDER BY created_at DESC,id DESC LIMIT 1""",
                (str(episode["project_id"]), episode_id),
            ).fetchone()
            plan_baseline = _decode(latest_applied["metadata_redacted_json"], {}) if latest_applied else {}
        planned_ms = sum(int(row["target_duration_ms"] or 0) for row in shots)
        target_ms = int(episode["target_duration_ms"] or 0)
        reasons: list[dict[str, Any]] = []
        if target_ms <= 0:
            reasons.append({"code": "TARGET_DURATION_INVALID", "message": "本集目标时长无效", "blocking": True})
        elif not shots:
            reasons.append({"code": "SHOT_PLAN_REQUIRED", "message": "本集尚无可重规划的镜头计划", "blocking": True})
        elif abs(planned_ms - target_ms) > TARGET_DURATION_TECHNICAL_TOLERANCE_MS:
            reasons.append({"code": "TARGET_DURATION_MISMATCH", "message": "当前分镜总时长与本集目标时长不匹配", "blocking": True, "target_duration_ms": target_ms, "planned_duration_ms": planned_ms, "tolerance_ms": TARGET_DURATION_TECHNICAL_TOLERANCE_MS})
        baseline_plan_id = plan_baseline.get("production_plan_version_id") if isinstance(plan_baseline, dict) else None
        if baseline_plan_id and str(baseline_plan_id) != str(project_plan.get("version_id")):
            reasons.append({"code": "PRODUCTION_PLAN_CHANGED", "message": "项目生产计划版本已变化", "blocking": True, "previous_version_id": baseline_plan_id, "current_version_id": project_plan.get("version_id")})
        # Legacy episodes have no replan baseline.  A changed plan is still
        # visible when the latest timeline/render snapshot records the old id.
        if not baseline_plan_id:
            with self.database.connect() as connection:
                timeline = connection.execute(
                    """SELECT input_snapshot_json FROM timeline_revisions WHERE episode_id=?
                    ORDER BY revision_no DESC,id DESC LIMIT 1""",
                    (episode_id,),
                ).fetchone()
            timeline_snapshot = _decode(timeline["input_snapshot_json"], {}) if timeline else {}
            old_plan_id = timeline_snapshot.get("production_plan_version_id") if isinstance(timeline_snapshot, dict) else None
            old_plan_id = old_plan_id or (timeline_snapshot.get("production_spec") or {}).get("production_plan_version_id") if isinstance(timeline_snapshot, dict) and isinstance(timeline_snapshot.get("production_spec"), dict) else old_plan_id
            if old_plan_id and str(old_plan_id) != str(project_plan.get("version_id")):
                reasons.append({"code": "PRODUCTION_PLAN_CHANGED", "message": "时间线使用的项目生产计划版本已变化", "blocking": True, "previous_version_id": old_plan_id, "current_version_id": project_plan.get("version_id")})
        replan_required = bool(reasons)
        draft_payload = None
        if draft is not None:
            # Build errors are surfaced as a review blocker instead of being
            # swallowed into a false READY state.
            with self.database.connect() as connection:
                try:
                    draft_payload = self._plan_from_draft(connection, episode_id, draft)
                except DomainRuleError as error:
                    draft_payload = {"status": "INVALID", "draft_id": str(draft["id"]), "error": {"code": error.code, "message": error.message}}
        return {
            "target_duration_ms": target_ms,
            "planned_duration_ms": planned_ms,
            "production_plan_version_id": project_plan.get("version_id"),
            "production_plan_version_no": project_plan.get("version_no"),
            "resolved_presentation": (project_plan.get("resolved_spec") or {}).get("delivery") or project_plan.get("presentation"),
            "resolved_production_spec": project_plan.get("resolved_spec"),
            "plan_stale": any(item["code"] == "PRODUCTION_PLAN_CHANGED" for item in reasons),
            "replan_required": replan_required,
            "replan_reasons": reasons,
            "replan_draft": draft_payload,
            "replan_job": {"id": str(active_job["id"]), "state": str(active_job["state"])} if active_job else None,
        }

    def request(self, episode_id: str, *, idempotency_key: str, actor: str = "local-user") -> dict[str, Any]:
        with self.database.connect() as connection:
            episode = self._episode(connection, episode_id)
            context = self._project_context(connection, str(episode["project_id"]))
        if not context["import_session_id"]:
            raise DomainRuleError("EPISODE_SOURCE_COMMIT_REQUIRED", "没有已确认提交的原文，无法生成本集重规划草稿。")
        if not context["profile_version_id"]:
            raise DomainRuleError("EPISODE_BREAKDOWN_MODEL_REQUIRED", "没有可用的项目 LLM_STORY_PARSE 能力，请先配置模型。")
        start, end = self._source_scope(episode["source_range_json"])
        job = LocalLLMService(self.database, self.settings).enqueue_breakdown(
            context["import_session_id"],
            context["profile_version_id"],
            idempotency_key,
            target_episode_id=episode_id,
            source_paragraph_start=start,
            source_paragraph_end=end,
            automatic_apply=False,
            actor=actor,
        )
        return {
            "status": "QUEUED",
            "episode_id": episode_id,
            "job_id": str(job["id"]),
            "idempotent_replay": bool(job.get("idempotent_replay")),
            "target_duration_ms": int(episode["target_duration_ms"] or 0),
        }

    def plan(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            episode = self._episode(connection, episode_id)
            draft = self._latest_ready_draft(
                connection, str(episode["project_id"]), episode_id,
                int(episode["target_duration_ms"] or 0), _decode(episode["source_range_json"], {}),
            )
            if draft is None:
                active = connection.execute(
                    """SELECT id,state FROM jobs WHERE project_id=? AND type='SCRIPT_BREAKDOWN_LOCAL_LLM'
                    AND json_extract(input_snapshot_json,'$.target_episode_id')=?
                    AND state IN ('QUEUED','CLAIMED','RUNNING','CANCEL_REQUESTED')
                    ORDER BY created_at DESC,id DESC LIMIT 1""",
                    (str(episode["project_id"]), episode_id),
                ).fetchone()
                return {"status": "NOT_READY", "episode_id": episode_id, "job": {"id": str(active["id"]), "state": str(active["state"])} if active else None}
            return self._plan_from_draft(connection, episode_id, draft)

    def apply(
        self,
        episode_id: str,
        *,
        expected_episode_revision: int,
        expected_plan_hash: str,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        scope = f"episode-replan:{episode_id}"
        payload_hash = _hash({"expected_episode_revision": expected_episode_revision, "expected_plan_hash": expected_plan_hash})
        stage = "transaction_begin"
        try:
            with self.database.transaction() as connection:
                previous = self._storage_stage(
                    "idempotency_lookup",
                    lambda: connection.execute(
                        "SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?",
                        (scope, idempotency_key),
                    ).fetchone(),
                )
                if previous:
                    if str(previous["payload_hash"]) != payload_hash:
                        raise DomainRuleError("IDEMPOTENCY_PAYLOAD_MISMATCH", "幂等键已用于不同的本集重规划请求")
                    stage = "transaction_commit"
                    return cast(dict[str, Any], _decode(previous["response_json"], {}))
                episode = self._storage_stage("episode_read", lambda: self._episode(connection, episode_id))
                if int(episode["revision"]) != expected_episode_revision:
                    raise DomainRuleError("EPISODE_REPLAN_REVISION_CONFLICT", "本集已变化，请重新生成并审核重规划差异", {"expected_revision": expected_episode_revision, "actual_revision": int(episode["revision"])})
                draft = self._storage_stage(
                    "draft_lookup",
                    lambda: self._latest_ready_draft(
                        connection, str(episode["project_id"]), episode_id,
                        int(episode["target_duration_ms"] or 0), _decode(episode["source_range_json"], {}),
                    ),
                )
                if draft is None:
                    raise DomainRuleError("REPLAN_DRAFT_NOT_READY", "AI 重规划草稿尚未就绪，不能应用")
                plan = self._storage_stage("plan_build", lambda: self._plan_from_draft(connection, episode_id, draft))
                if str(plan["plan_hash"]) != expected_plan_hash:
                    raise DomainRuleError("EPISODE_REPLAN_PLAN_STALE", "重规划差异已变化，请重新预览")
                if not plan.get("valid", True):
                    raise DomainRuleError(
                        "EPISODE_REPLAN_PLAN_INVALID",
                        "冻结镜头不能从当前计划移除",
                        {"issues": plan.get("issues") or []},
                    )
                now = _now()
                aliases = self._storage_stage("asset_aliases", lambda: self._asset_aliases(connection, str(episode["project_id"])))
                created_ids: list[str] = []
                modified_ids: list[str] = []
                archived_ids: list[str] = []
                protected_ids: list[str] = []
                for item in plan["diff"]:
                    action = str(item["action"])
                    shot_id = str(item["shot_id"]) if item.get("shot_id") else None
                    if action == "PROTECTED":
                        if shot_id:
                            protected_ids.append(shot_id)
                        continue
                    if action == "REPLACE_PROTECTED":
                        assert shot_id
                        row = self._storage_stage(
                            "shot_replace_lookup",
                            lambda shot_id=shot_id: connection.execute(
                                "SELECT revision FROM shots WHERE id=? AND episode_id=? AND archived_at IS NULL",
                                (shot_id, episode_id),
                            ).fetchone(),
                        )
                        if row is None or int(row["revision"]) != int(item["expected_revision"]):
                            raise DomainRuleError("EPISODE_REPLAN_REVISION_CONFLICT", "冻结镜头已变化，请重新预览", {"shot_id": shot_id})
                        # Archiving the shot pointer does not touch its frozen
                        # shot_revision. Historical media/timeline remain queryable.
                        self._storage_stage(
                            "shot_replace_archive",
                            lambda shot_id=shot_id, expected_revision=int(item["expected_revision"]): connection.execute(
                                "UPDATE shots SET archived_at=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=? AND archived_at IS NULL",
                                (now, now, shot_id, expected_revision),
                            ),
                        )
                        self._storage_stage(
                            "variants_stale_replace",
                            lambda shot_id=shot_id: self._mark_variants_stale(connection, shot_id, "episode_replan_frozen_replaced"),
                        )
                        proposal = item["after"]
                        replacement_id = self._storage_stage(
                            "shot_replace_insert",
                            lambda proposal=proposal: self._insert_shot(connection, episode, draft, proposal, aliases, now, actor),
                        )
                        created_ids.append(replacement_id)
                        # The old active pointer is archived as a supersession;
                        # its frozen revision and attached history remain intact.
                        archived_ids.append(shot_id)
                        continue
                    if action == "MODIFY":
                        assert shot_id
                        row = self._storage_stage(
                            "shot_modify_lookup",
                            lambda shot_id=shot_id: connection.execute(
                                "SELECT revision,current_revision_id FROM shots WHERE id=? AND episode_id=? AND archived_at IS NULL",
                                (shot_id, episode_id),
                            ).fetchone(),
                        )
                        if row is None or int(row["revision"]) != int(item["expected_revision"]):
                            raise DomainRuleError("EPISODE_REPLAN_REVISION_CONFLICT", "待更新镜头已变化，请重新预览", {"shot_id": shot_id})
                        proposal = item["after"]
                        revision_id = str(uuid.uuid4())
                        revision_no = int(self._storage_stage(
                            "shot_modify_revision_lookup",
                            lambda shot_id=shot_id: connection.execute(
                                "SELECT COALESCE(MAX(revision_no),0)+1 FROM shot_revisions WHERE shot_id=?",
                                (shot_id,),
                            ).fetchone()[0],
                        ))
                        self._storage_stage(
                            "shot_modify_revision_insert",
                            lambda revision_id=revision_id, shot_id=shot_id, revision_no=revision_no, proposal=proposal: connection.execute(
                                """INSERT INTO shot_revisions (id,shot_id,revision_no,fields_json,is_frozen,created_at,updated_at,created_by,revision,schema_version)
                                VALUES (?,?,?, ?,0,?,?,?,1,'v2')""",
                                (revision_id, shot_id, revision_no, _json(proposal["fields"]), now, now, actor),
                            ),
                        )
                        self._storage_stage(
                            "shot_modify_update",
                            lambda revision_id=revision_id, proposal=proposal, shot_id=shot_id: connection.execute(
                                "UPDATE shots SET current_revision_id=?,target_duration_ms=?,shot_type=?,status='DIRECTED',revision=revision+1,updated_at=? WHERE id=?",
                                (revision_id, int(proposal["target_duration_ms"]), str(proposal["shot_type"]), now, shot_id),
                            ),
                        )
                        self._storage_stage(
                            "variants_stale_modify",
                            lambda shot_id=shot_id: self._mark_variants_stale(connection, shot_id, "episode_replan_changed"),
                        )
                        self._storage_stage(
                            "dialogue_sync_modify",
                            lambda shot_id=shot_id, proposal=proposal: self._sync_dialogue(connection, episode_id, shot_id, proposal.get("fields", {}).get("dialogue"), now, actor),
                        )
                        modified_ids.append(shot_id)
                        continue
                    if action == "ADD":
                        proposal = item["after"]
                        shot_id = self._storage_stage(
                            "shot_add",
                            lambda proposal=proposal: self._insert_shot(connection, episode, draft, proposal, aliases, now, actor),
                        )
                        created_ids.append(shot_id)
                        continue
                    if action == "ARCHIVE":
                        assert shot_id
                        changed = self._storage_stage(
                            "shot_archive",
                            lambda shot_id=shot_id, expected_revision=int(item["expected_revision"]): connection.execute(
                                "UPDATE shots SET archived_at=?,updated_at=?,revision=revision+1 WHERE id=? AND episode_id=? AND revision=? AND archived_at IS NULL",
                                (now, now, shot_id, episode_id, expected_revision),
                            ),
                        )
                        if changed.rowcount != 1:
                            raise DomainRuleError("EPISODE_REPLAN_REVISION_CONFLICT", "待归档镜头已变化，请重新预览", {"shot_id": shot_id})
                        self._storage_stage(
                            "variants_stale_archive",
                            lambda shot_id=shot_id: self._mark_variants_stale(connection, shot_id, "episode_replan_archived"),
                        )
                        archived_ids.append(shot_id)
                stale_timelines = self._storage_stage(
                    "timeline_stale",
                    lambda: connection.execute(
                        "UPDATE timeline_revisions SET status='STALE',updated_at=?,revision=revision+1 WHERE episode_id=? AND status!='STALE'",
                        (now, episode_id),
                    ).rowcount,
                )
                self._storage_stage(
                    "episode_reset",
                    lambda: connection.execute(
                        "UPDATE episodes SET production_status='NOT_STARTED',updated_at=?,revision=revision+1 WHERE id=? AND revision=?",
                        (now, episode_id, expected_episode_revision),
                    ),
                )
                result = {
                    "status": "APPLIED",
                    "episode_id": episode_id,
                    "draft_id": str(draft["id"]),
                    "plan_hash": expected_plan_hash,
                    "created_shot_ids": created_ids,
                    "modified_shot_ids": modified_ids,
                    "archived_shot_ids": archived_ids,
                    "protected_shot_ids": protected_ids,
                    "stale_timeline_revisions": stale_timelines,
                    "historical_media_preserved": True,
                    "requires_generation": True,
                }
                production_plan_version_id = self._storage_stage(
                    "audit_plan_snapshot",
                    lambda: self._plan_snapshot(connection, str(episode["project_id"])).get("version_id"),
                )
                self._storage_stage(
                    "audit_write",
                    lambda: connection.execute(
                        """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                        VALUES (?,'writer','EPISODE_REPLAN_APPLIED','episode',?,'人工确认后应用本集 AI 重规划',?)""",
                        (actor, episode_id, _json({**result, "production_plan_version_id": production_plan_version_id})),
                    ),
                )
                self._storage_stage(
                    "outbox_write",
                    lambda: connection.execute(
                        """INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json)
                        VALUES ('episode.shot_plan.changed',?,'episode',?,?)""",
                        (str(episode["project_id"]), episode_id, _json(result)),
                    ),
                )
                self._storage_stage(
                    "idempotency_write",
                    lambda: connection.execute(
                        """INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json)
                        VALUES (?,?,?,?)""",
                        (scope, idempotency_key, payload_hash, _json(result)),
                    ),
                )
                stage = "transaction_commit"
                return result
        except EpisodeReplanStorageError:
            raise
        except sqlite3.OperationalError as error:
            raise EpisodeReplanStorageError(stage, error) from error

    def _insert_shot(
        self,
        connection: sqlite3.Connection,
        episode: sqlite3.Row,
        draft: sqlite3.Row,
        proposal: dict[str, Any],
        aliases: dict[str, dict[str, set[str]]],
        now: str,
        actor: str,
    ) -> str:
        """Insert one new active-plan shot while preserving old rows."""
        shot_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
        code = f"{episode['code']}-RP{int(draft['revision']):02d}-{uuid.uuid4().hex[:6].upper()}"
        scene_row = self._storage_stage(
            "shot_add_scene_lookup",
            lambda: connection.execute(
                "SELECT scene_id FROM episode_scene_ranges WHERE episode_id=? AND ordinal=?",
                (str(episode["id"]), int(proposal.get("scene_no") or 0)),
            ).fetchone(),
        )
        maximum = self._storage_stage(
            "shot_add_order_lookup",
            lambda: connection.execute(
                "SELECT COALESCE(MAX(CAST(order_key AS REAL)),0) FROM shots WHERE episode_id=?",
                (str(episode["id"]),),
            ).fetchone()[0],
        )
        self._storage_stage(
            "shot_add_insert",
            lambda: connection.execute(
                """INSERT INTO shots (id,episode_id,scene_id,code,order_key,target_duration_ms,shot_type,status,
                current_revision_id,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,'DIRECTED',?,?,?, ?,1,'v2')""",
                (
                    shot_id, str(episode["id"]), scene_row[0] if scene_row else None, code,
                    str(float(maximum) + 1), int(proposal["target_duration_ms"]),
                    str(proposal["shot_type"]), revision_id, now, now, actor,
                ),
            ),
        )
        self._storage_stage(
            "shot_add_revision_insert",
            lambda: connection.execute(
                """INSERT INTO shot_revisions
                (id,shot_id,revision_no,fields_json,is_frozen,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,1,?,0,?,?,?,1,'v2')""",
                (revision_id, shot_id, _json(proposal["fields"]), now, now, actor),
            ),
        )
        self._storage_stage(
            "shot_add_character_asset_binding",
            lambda: self._bind_assets(connection, shot_id, aliases, "CHARACTER", list(proposal.get("characters") or []), "main", now, actor),
        )
        self._storage_stage(
            "shot_add_scene_asset_binding",
            lambda: self._bind_assets(connection, shot_id, aliases, "SCENE", [str(proposal.get("scene_title") or "")], "location", now, actor),
        )
        self._storage_stage(
            "shot_add_prop_asset_binding",
            lambda: self._bind_assets(connection, shot_id, aliases, "PROP", list(proposal.get("props") or []), "prop", now, actor),
        )
        self._storage_stage(
            "dialogue_sync_add",
            lambda: self._sync_dialogue(connection, str(episode["id"]), shot_id, proposal.get("fields", {}).get("dialogue"), now, actor),
        )
        return shot_id

    @staticmethod
    def _mark_variants_stale(connection: sqlite3.Connection, shot_id: str, reason: str) -> None:
        connection.execute(
            """UPDATE generation_variants SET is_stale=1,stale_reason=?,updated_at=CURRENT_TIMESTAMP
            WHERE intent_id IN (SELECT id FROM generation_intents WHERE owner_type='SHOT' AND owner_id=?)""",
            (reason, shot_id),
        )

    @staticmethod
    def _asset_aliases(connection: sqlite3.Connection, project_id: str) -> dict[str, dict[str, set[str]]]:
        from local_drama.application.breakdown_apply import BreakdownApplyService

        return BreakdownApplyService._asset_aliases(connection, project_id)

    @staticmethod
    def _bind_assets(connection: sqlite3.Connection, shot_id: str, aliases: dict[str, dict[str, set[str]]], kind: str, names: list[str], role: str, now: str, actor: str) -> None:
        from local_drama.application.breakdown_apply import BreakdownApplyService

        BreakdownApplyService._bind_assets(connection, shot_id, aliases, kind, names, role, now, actor)

    @staticmethod
    def _sync_dialogue(connection: sqlite3.Connection, episode_id: str, shot_id: str, raw: object, now: str, actor: str) -> None:
        """Append immutable text revisions for existing lines and create new lines.

        The old text revisions remain queryable; the latest text revision is
        what downstream TTS freshness checks intentionally follow.
        """
        entries: list[dict[str, str]] = []
        if isinstance(raw, str):
            for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                if line.strip():
                    if "：" in line:
                        speaker, text = line.split("：", 1)
                        entries.append({"speaker": speaker.strip(), "text": text.strip()})
                    elif ":" in line:
                        speaker, text = line.split(":", 1)
                        entries.append({"speaker": speaker.strip(), "text": text.strip()})
                    else:
                        entries.append({"speaker": "旁白", "text": line.strip()})
        elif isinstance(raw, dict) and str(raw.get("text") or "").strip():
            entries.append({"speaker": str(raw.get("speaker") or "旁白"), "text": str(raw["text"]).strip()})
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and str(item.get("text") or "").strip():
                    entries.append({"speaker": str(item.get("speaker") or "旁白"), "text": str(item["text"]).strip()})
        lines = connection.execute("SELECT * FROM dialogue_lines WHERE episode_id=? AND shot_id=? ORDER BY code,id", (episode_id, shot_id)).fetchall()
        for index, entry in enumerate(entries):
            if index < len(lines):
                line = lines[index]
                next_no = int(connection.execute("SELECT COALESCE(MAX(revision_no),0)+1 FROM dialogue_text_revisions WHERE dialogue_line_id=?", (line["id"],)).fetchone()[0])
                text = entry["text"]
                text_hash = hashlib.sha256(_json({"text": text, "pronunciation": {}}).encode("utf-8")).hexdigest()
                connection.execute(
                    """INSERT INTO dialogue_text_revisions (id,dialogue_line_id,revision_no,text,pronunciation_json,text_hash,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,'{}',?,?,?, ?,1,'v2')""",
                    (str(uuid.uuid4()), line["id"], next_no, text, text_hash, now, now, actor),
                )
                connection.execute("UPDATE dialogue_lines SET speaker=?,updated_at=?,revision=revision+1 WHERE id=?", (entry["speaker"], now, line["id"]))
        for entry in entries[len(lines):]:
            seq = int(connection.execute("SELECT COUNT(*)+1 FROM dialogue_lines WHERE episode_id=?", (episode_id,)).fetchone()[0])
            line_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
            text_hash = hashlib.sha256(_json({"text": entry["text"], "pronunciation": {}}).encode("utf-8")).hexdigest()
            connection.execute(
                """INSERT INTO dialogue_lines (id,episode_id,shot_id,code,speaker,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?, ?,1,'v2')""",
                (line_id, episode_id, shot_id, f"RP-DL-{seq:04d}", entry["speaker"], now, now, actor),
            )
            connection.execute(
                """INSERT INTO dialogue_text_revisions (id,dialogue_line_id,revision_no,text,pronunciation_json,text_hash,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,1,?,'{}',?,?,?, ?,1,'v2')""",
                (revision_id, line_id, entry["text"], text_hash, now, now, actor),
            )
