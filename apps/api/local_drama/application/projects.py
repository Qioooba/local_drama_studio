from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.policies import (
    VALID_PROJECT_TRANSITIONS,
    VALID_SHOT_TRANSITIONS,
    require_transition,
    validate_project_code,
    validate_project_spec,
    validate_shot_ready,
)
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.template import TEMPLATE_VERSION, build_project_tree

from .reviews import ReviewService


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ProjectService:
    def __init__(self, database: Database, projects_root: Path) -> None:
        self.database = database
        self.projects_root = projects_root

    def create_project(
        self,
        *,
        code: str,
        title: str,
        episode_count: int,
        aspect_ratio: str | None,
        fps_num: int | None,
        fps_den: int | None,
        target_duration_ms: int,
        allow_unconfigured_capabilities: bool,
        actor: str = "local-user",
        request_id: str | None = None,
        simulate_failure: bool = False,
    ) -> dict[str, Any]:
        validate_project_code(code)
        validate_project_spec(
            episode_count=episode_count,
            aspect_ratio=aspect_ratio,
            fps_num=fps_num,
            fps_den=fps_den,
            allow_unconfigured=allow_unconfigured_capabilities,
        )
        if not title or len(title) > 200:
            raise DomainRuleError("INVALID_PROJECT_TITLE", "项目标题必须是 1—200 个字符")
        if target_duration_ms <= 0:
            raise DomainRuleError("INVALID_TARGET_DURATION", "target_duration_ms 必须大于 0")
        project_id = str(uuid.uuid4())
        final_root: Path | None = None
        try:
            final_root, _ = build_project_tree(self.projects_root, project_id, code, title, episode_count)
            now = _utc_now()
            season_id = str(uuid.uuid4())
            with self.database.transaction() as connection:
                existing = connection.execute("SELECT id FROM projects WHERE code = ?", (code,)).fetchone()
                if existing:
                    raise DomainRuleError("PROJECT_CODE_EXISTS", "项目 code 已存在", {"code": code})
                connection.execute(
                    """INSERT INTO projects (id, code, title, status, template_version, root_rel, aspect_ratio, fps_num,
                    fps_den, created_at, updated_at, created_by) VALUES (?, ?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (project_id, code, title, TEMPLATE_VERSION, code, aspect_ratio, fps_num, fps_den, now, now, actor),
                )
                connection.execute(
                    """INSERT INTO seasons (id, project_id, number, code, title, display_order, created_at, updated_at, created_by)
                    VALUES (?, ?, 1, 'SEASON_001', '第 1 季', 1, ?, ?, ?)""",
                    (season_id, project_id, now, now, actor),
                )
                for episode_number in range(1, episode_count + 1):
                    episode_code = f"EPISODE_{episode_number:03d}"
                    connection.execute(
                        """INSERT INTO episodes (id, season_id, number, display_order, code, title, narrative_status,
                        production_status, target_duration_ms, source_range_json, created_at, updated_at, created_by)
                        VALUES (?, ?, ?, ?, ?, ?, 'OUTLINE', 'NOT_STARTED', ?, '{}', ?, ?, ?)""",
                        (
                            str(uuid.uuid4()),
                            season_id,
                            episode_number,
                            episode_number,
                            episode_code,
                            f"第 {episode_number} 集",
                            target_duration_ms,
                            now,
                            now,
                            actor,
                        ),
                    )
                connection.execute(
                    """INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, request_id,
                    summary, metadata_redacted_json) VALUES (?, 'producer', 'PROJECT_CREATED', 'project', ?, ?, ?, ?)""",
                    (actor, project_id, request_id, f"创建项目 {code}", _json({"episode_count": episode_count})),
                )
                connection.execute(
                    """INSERT INTO outbox_events (type, project_id, subject_type, subject_id, payload_json)
                    VALUES ('project.changed', ?, 'project', ?, ?)""",
                    (project_id, project_id, _json({"status": "DRAFT", "revision": 1})),
                )
                if simulate_failure:
                    raise RuntimeError("simulated project creation failure")
        except Exception:
            if final_root and final_root.exists():
                shutil.rmtree(final_root, ignore_errors=True)
            raise
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
        return dict(row)

    def list_projects(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM projects ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def update_project_title(self, project_id: str, title: str, expected_revision: int, actor: str = "local-user") -> dict[str, Any]:
        if not title or len(title) > 200:
            raise DomainRuleError("INVALID_PROJECT_TITLE", "项目标题必须是 1—200 个字符")
        now = _utc_now()
        with self.database.transaction() as connection:
            result = connection.execute(
                "UPDATE projects SET title = ?, updated_at = ?, revision = revision + 1 WHERE id = ? AND revision = ?",
                (title, now, project_id, expected_revision),
            )
            if result.rowcount != 1:
                current = connection.execute("SELECT revision, title FROM projects WHERE id = ?", (project_id,)).fetchone()
                if current is None:
                    raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
                raise DomainRuleError(
                    "REVISION_CONFLICT",
                    "项目已被其他标签页或后台任务修改",
                    {"current_revision": current["revision"], "submitted_revision": expected_revision},
                )
            revision = expected_revision + 1
            connection.execute(
                """INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id,
                before_revision, after_revision, summary, metadata_redacted_json) VALUES (?, 'producer', 'PROJECT_UPDATED',
                'project', ?, ?, ?, '更新项目标题', '{}')""",
                (actor, project_id, expected_revision, revision),
            )
        return self.get_project(project_id)

    def transition_project(self, project_id: str, target: str, actor: str = "local-user") -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status, revision FROM projects WHERE id = ?", (project_id,)).fetchone()
            if row is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            require_transition(VALID_PROJECT_TRANSITIONS, row["status"], target, "Project")
            if target == "ARCHIVED":
                active = connection.execute(
                    "SELECT 1 FROM jobs WHERE project_id = ? AND state IN ('RUNNING','CLAIMED','CANCEL_REQUESTED') LIMIT 1",
                    (project_id,),
                ).fetchone()
                if active:
                    raise DomainRuleError("PROJECT_HAS_ACTIVE_JOBS", "存在活动任务，不能归档")
            connection.execute(
                "UPDATE projects SET status = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
                (target, _utc_now(), project_id),
            )
            connection.execute(
                """INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id,
                before_revision, after_revision, summary, metadata_redacted_json) VALUES (?, 'producer', ?, 'project', ?, ?, ?, ?, '{}')""",
                (actor, f"PROJECT_{target}", project_id, row["revision"], row["revision"] + 1, f"项目状态变为 {target}"),
            )
        return self.get_project(project_id)

    def create_shot(self, episode_id: str, code: str, target_duration_ms: int, shot_type: str = "OTHER") -> dict[str, Any]:
        if target_duration_ms <= 0:
            raise DomainRuleError("INVALID_TARGET_DURATION", "target_duration_ms 必须大于 0")
        shot_id = str(uuid.uuid4())
        revision_id = str(uuid.uuid4())
        now = _utc_now()
        with self.database.transaction() as connection:
            episode = connection.execute("SELECT id FROM episodes WHERE id = ?", (episode_id,)).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
            maximum = connection.execute("SELECT COALESCE(MAX(CAST(order_key AS REAL)), 0) FROM shots WHERE episode_id = ?", (episode_id,)).fetchone()[0]
            connection.execute(
                """INSERT INTO shots (id, episode_id, code, order_key, target_duration_ms, shot_type, status,
                current_revision_id, created_at, updated_at, created_by) VALUES (?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?, ?, ?)""",
                (shot_id, episode_id, code, str(float(maximum) + 1), target_duration_ms, shot_type, revision_id, now, now, "local-user"),
            )
            connection.execute(
                """INSERT INTO shot_revisions (id, shot_id, revision_no, fields_json, is_frozen, created_at, updated_at, created_by)
                VALUES (?, ?, 1, '{}', 0, ?, ?, 'local-user')""",
                (revision_id, shot_id, now, now),
            )
        return self.get_shot(shot_id)

    def get_shot(self, shot_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM shots WHERE id = ?", (shot_id,)).fetchone()
        if row is None:
            raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
        return dict(row)

    def list_seasons(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM seasons WHERE project_id = ? ORDER BY display_order", (project_id,)).fetchall()
        return [dict(row) for row in rows]

    def list_episodes(self, season_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM episodes WHERE season_id = ? ORDER BY display_order", (season_id,)).fetchall()
        return [dict(row) for row in rows]

    def list_shots(self, episode_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM shots WHERE episode_id = ? ORDER BY CAST(order_key AS REAL), code", (episode_id,)).fetchall()
        return [dict(row) for row in rows]

    def get_episode(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        if row is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
        return dict(row)

    def reorder_episode(self, episode_id: str, display_order: int) -> dict[str, Any]:
        if display_order < 1:
            raise DomainRuleError("INVALID_DISPLAY_ORDER", "display_order 必须大于 0")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
            if row is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
            connection.execute(
                "UPDATE episodes SET display_order = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
                (display_order, _utc_now(), episode_id),
            )
        return self.get_episode(episode_id)

    def create_shot_revision(self, shot_id: str, fields: dict[str, object], freeze: bool = False) -> dict[str, Any]:
        now = _utc_now()
        revision_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            shot = connection.execute("SELECT * FROM shots WHERE id = ?", (shot_id,)).fetchone()
            if shot is None:
                raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
            current = connection.execute("SELECT COALESCE(MAX(revision_no), 0) FROM shot_revisions WHERE shot_id = ?", (shot_id,)).fetchone()[0]
            revision_no = int(current) + 1
            connection.execute(
                """INSERT INTO shot_revisions (id, shot_id, revision_no, fields_json, is_frozen, created_at, updated_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'local-user')""",
                (revision_id, shot_id, revision_no, _json(fields), int(freeze), now, now),
            )
            connection.execute(
                "UPDATE shots SET current_revision_id = ?, status = 'DIRECTED', revision = revision + 1, updated_at = ? WHERE id = ?",
                (revision_id, now, shot_id),
            )
        ReviewService(self.database).mark_stale_for_owner(shot_id, "shot_revision_changed")
        return {"id": revision_id, "shot_id": shot_id, "revision_no": revision_no, "fields": fields, "is_frozen": freeze}

    def mark_shot_production_ready(self, shot_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            shot = connection.execute("SELECT * FROM shots WHERE id = ?", (shot_id,)).fetchone()
            if shot is None:
                raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
            revision = connection.execute("SELECT fields_json FROM shot_revisions WHERE id = ?", (shot["current_revision_id"],)).fetchone()
            fields = json.loads(revision["fields_json"]) if revision else {}
            validate_shot_ready(fields)
            require_transition(VALID_SHOT_TRANSITIONS, shot["status"], "READY", "Shot")
            connection.execute("UPDATE shots SET status = 'READY', updated_at = ?, revision = revision + 1 WHERE id = ?", (_utc_now(), shot_id))
        return self.get_shot(shot_id)
