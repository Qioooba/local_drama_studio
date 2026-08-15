from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation_contracts import CameraPlan, resolve_camera_plan
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
        season_count: int = 1,
        width: int | None = None,
        height: int | None = None,
        primary_language: str | None = None,
        subtitle_mode: str | None = None,
        subtitle_language: str | None = None,
        production_plan: dict[str, Any] | None = None,
        profile_bindings: list[dict[str, str]] | None = None,
        delivery_target: dict[str, Any] | None = None,
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
            season_count=season_count, width=width, height=height, primary_language=primary_language,
            subtitle_mode=subtitle_mode, subtitle_language=subtitle_language,
        )
        if not title or len(title) > 200:
            raise DomainRuleError("INVALID_PROJECT_TITLE", "项目标题必须是 1—200 个字符")
        if target_duration_ms <= 0:
            raise DomainRuleError("INVALID_TARGET_DURATION", "target_duration_ms 必须大于 0")
        profile_bindings = profile_bindings or []
        self._validate_creation_bindings(production_plan, profile_bindings, delivery_target,
                                         require_complete=not allow_unconfigured_capabilities)
        project_id = str(uuid.uuid4())
        final_root: Path | None = None
        try:
            final_root, _ = build_project_tree(self.projects_root, project_id, code, title, episode_count, season_count=season_count)
            now = _utc_now()
            with self.database.transaction() as connection:
                existing = connection.execute("SELECT id FROM projects WHERE code = ?", (code,)).fetchone()
                if existing:
                    raise DomainRuleError("PROJECT_CODE_EXISTS", "项目 code 已存在", {"code": code})
                connection.execute(
                    """INSERT INTO projects (id, code, title, status, template_version, root_rel, aspect_ratio, fps_num,
                    fps_den,width,height,primary_language,subtitle_mode,subtitle_language,created_at,updated_at,created_by)
                    VALUES (?, ?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (project_id, code, title, TEMPLATE_VERSION, code, aspect_ratio, fps_num, fps_den, width, height,
                     primary_language, subtitle_mode, subtitle_language, now, now, actor),
                )
                for season_number in range(1, season_count + 1):
                    season_id = str(uuid.uuid4())
                    season_code = f"SEASON_{season_number:03d}"
                    connection.execute(
                        """INSERT INTO seasons (id, project_id, number, code, title, display_order, created_at, updated_at, created_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (season_id, project_id, season_number, season_code, f"第 {season_number} 季", season_number, now, now, actor),
                    )
                    for episode_number in range(1, episode_count + 1):
                        global_number = (season_number - 1) * episode_count + episode_number
                        episode_code = f"EPISODE_{global_number:03d}"
                        connection.execute(
                            """INSERT INTO episodes (id, season_id, number, display_order, code, title, narrative_status,
                            production_status, target_duration_ms, source_range_json, created_at, updated_at, created_by)
                            VALUES (?, ?, ?, ?, ?, ?, 'OUTLINE', 'NOT_STARTED', ?, '{}', ?, ?, ?)""",
                            (str(uuid.uuid4()), season_id, episode_number, episode_number, episode_code,
                             f"第 {episode_number} 集", target_duration_ms, now, now, actor),
                        )
                if production_plan is not None:
                    plan_id, plan_version_id = str(uuid.uuid4()), str(uuid.uuid4())
                    connection.execute(
                        "INSERT INTO production_plans (id,code,title,created_at,updated_at,created_by) VALUES (?,?,?,?,?,?)",
                        (plan_id, production_plan["code"], production_plan["title"], now, now, actor),
                    )
                    connection.execute(
                        """INSERT INTO production_plan_versions (id,production_plan_id,version_no,plan_json,status,
                        created_at,updated_at,created_by) VALUES (?,?,1,?,'ACTIVE',?,?,?)""",
                        (plan_version_id, plan_id, _json(production_plan["plan"]), now, now, actor),
                    )
                    connection.execute(
                        "INSERT INTO project_plan_bindings (id,project_id,production_plan_version_id,created_at,updated_at,created_by) VALUES (?,?,?,?,?,?)",
                        (str(uuid.uuid4()), project_id, plan_version_id, now, now, actor),
                    )
                    connection.execute("UPDATE projects SET production_plan_version_id=? WHERE id=?", (plan_version_id, project_id))
                for binding in profile_bindings:
                    connection.execute(
                        """INSERT INTO project_profile_bindings (id,project_id,capability,execution_profile_version_id,status,
                        created_at,updated_at,created_by) VALUES (?,?,?,?,'ACTIVE',?,?,?)""",
                        (str(uuid.uuid4()), project_id, binding["capability"], binding["profile_version_id"], now, now, actor),
                    )
                if delivery_target is not None:
                    target_id, target_version_id = str(uuid.uuid4()), str(uuid.uuid4())
                    target_spec_json = _json(delivery_target["spec"])
                    connection.execute(
                        """INSERT INTO delivery_targets (id,project_id,code,title,transport,target_spec_json,status,
                        created_at,updated_at,created_by) VALUES (?,?,?,?, 'LOCAL_FILESYSTEM',?,'ACTIVE',?,?,?)""",
                        (target_id, project_id, delivery_target["code"], delivery_target["title"], target_spec_json, now, now, actor),
                    )
                    connection.execute(
                        """INSERT INTO delivery_target_versions (id,delivery_target_id,version_no,target_spec_json,status,
                        created_at,updated_at,created_by) VALUES (?,?,1,?,'ACTIVE',?,?,?)""",
                        (target_version_id, target_id, target_spec_json, now, now, actor),
                    )
                connection.execute(
                    """INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, request_id,
                    summary, metadata_redacted_json) VALUES (?, 'producer', 'PROJECT_CREATED', 'project', ?, ?, ?, ?)""",
                    (actor, project_id, request_id, f"创建项目 {code}", _json({"season_count": season_count,
                     "episode_count_per_season": episode_count, "total_episode_count": season_count * episode_count,
                     "profile_binding_count": len(profile_bindings), "production_plan_bound": production_plan is not None,
                     "delivery_target_created": delivery_target is not None})),
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

    def plan_project_creation(
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
        season_count: int = 1,
        width: int | None = None,
        height: int | None = None,
        primary_language: str | None = None,
        subtitle_mode: str | None = None,
        subtitle_language: str | None = None,
        production_plan: dict[str, Any] | None = None,
        profile_bindings: list[dict[str, str]] | None = None,
        delivery_target: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate a project creation request without creating files or rows."""
        validate_project_code(code)
        validate_project_spec(
            episode_count=episode_count, aspect_ratio=aspect_ratio, fps_num=fps_num,
            fps_den=fps_den, allow_unconfigured=allow_unconfigured_capabilities,
            season_count=season_count, width=width, height=height, primary_language=primary_language,
            subtitle_mode=subtitle_mode, subtitle_language=subtitle_language,
        )
        if not title or len(title) > 200:
            raise DomainRuleError("INVALID_PROJECT_TITLE", "项目标题必须是 1—200 个字符")
        if target_duration_ms <= 0:
            raise DomainRuleError("INVALID_TARGET_DURATION", "target_duration_ms 必须大于 0")
        profile_bindings = profile_bindings or []
        self._validate_creation_bindings(production_plan, profile_bindings, delivery_target,
                                         require_complete=False)
        with self.database.connect() as connection:
            code_exists = connection.execute("SELECT 1 FROM projects WHERE code=?", (code,)).fetchone() is not None
        target_root = self.projects_root / code
        disk = shutil.disk_usage(self.projects_root)
        estimated_bytes = max(1_048_576, episode_count * season_count * 65_536)
        checks = [
            {"code": "PROJECT_CODE_AVAILABLE", "passed": not code_exists},
            {"code": "PROJECT_ROOT_AVAILABLE", "passed": not target_root.exists()},
            {"code": "PROJECT_ROOT_SPACE", "passed": disk.free >= estimated_bytes, "free_bytes": disk.free, "required_bytes": estimated_bytes},
        ]
        configuration_blockers = ([] if profile_bindings else ["PROFILE_NOT_BOUND"]) + (
            [] if production_plan is not None else ["PRODUCTION_PLAN_NOT_BOUND"]
        ) + ([] if delivery_target is not None else ["DELIVERY_TARGET_NOT_BOUND"])
        hard_blockers = [str(check["code"]) for check in checks if not check["passed"]]
        if configuration_blockers and not allow_unconfigured_capabilities:
            hard_blockers.extend(configuration_blockers)
        return {
            "status": "READY_WITH_CONFIGURATION_BLOCKERS" if not hard_blockers and configuration_blockers else ("READY" if not hard_blockers else "BLOCKED"),
            "checks": checks,
            "blockers": hard_blockers,
            "configuration_blockers": configuration_blockers,
            "accepted_unconfigured": allow_unconfigured_capabilities,
            "target_root_rel": code,
            "estimated_bytes": estimated_bytes,
            "structure": {"season_count": season_count, "episode_count_per_season": episode_count,
                          "total_episode_count": season_count * episode_count},
            "presentation": {"aspect_ratio": aspect_ratio, "width": width, "height": height,
                             "fps": {"numerator": fps_num, "denominator": fps_den}, "primary_language": primary_language,
                             "subtitle_mode": subtitle_mode, "subtitle_language": subtitle_language},
            "would_create_project": True,
            "mutated": False,
            "runtime_contacted": False,
            "network_contacted": False,
        }

    def _validate_creation_bindings(
        self,
        production_plan: dict[str, Any] | None,
        profile_bindings: list[dict[str, str]],
        delivery_target: dict[str, Any] | None,
        *,
        require_complete: bool,
    ) -> None:
        if require_complete and (production_plan is None or not profile_bindings or delivery_target is None):
            raise DomainRuleError("PROJECT_CONFIGURATION_REQUIRED", "必须显式配置 ProductionPlan、至少一个 Published Profile 和交付目标")
        capabilities = [item.get("capability", "") for item in profile_bindings]
        if len(capabilities) != len(set(capabilities)):
            raise DomainRuleError("DUPLICATE_PROFILE_CAPABILITY", "同一 capability 只能绑定一个 Profile")
        if delivery_target is not None:
            path_rel = str(delivery_target.get("spec", {}).get("path_rel", ""))
            path = PurePosixPath(path_rel)
            if not path_rel or path.is_absolute() or ".." in path.parts:
                raise DomainRuleError("INVALID_DELIVERY_TARGET", "交付目标必须是项目内相对路径")
        with self.database.connect() as connection:
            if production_plan is not None and connection.execute(
                "SELECT 1 FROM production_plans WHERE code=?", (production_plan.get("code"),)
            ).fetchone():
                raise DomainRuleError("PRODUCTION_PLAN_CODE_EXISTS", "ProductionPlan code 已存在")
            for binding in profile_bindings:
                profile = connection.execute(
                    "SELECT capability,status FROM execution_profile_versions WHERE id=?", (binding.get("profile_version_id"),)
                ).fetchone()
                if profile is None:
                    raise DomainRuleError("PROFILE_NOT_FOUND", "Profile 版本不存在")
                if profile["status"] != "PUBLISHED":
                    raise DomainRuleError("PROFILE_NOT_PUBLISHED", "创建项目只能绑定 Published Profile")
                if str(profile["capability"]) != binding.get("capability"):
                    raise DomainRuleError("PROFILE_CAPABILITY_MISMATCH", "Profile capability 与绑定键不一致")

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
        return dict(row)

    def copy_as_template(
        self,
        source_project_id: str,
        *,
        code: str,
        title: str,
        actor: str = "local-user",
        request_id: str | None = None,
        simulate_failure: bool = False,
    ) -> dict[str, Any]:
        """Create a clean project from reusable structure and configuration only."""
        validate_project_code(code)
        if not title or len(title) > 200:
            raise DomainRuleError("INVALID_PROJECT_TITLE", "项目标题必须是 1—200 个字符")
        with self.database.connect() as connection:
            source = connection.execute("SELECT * FROM projects WHERE id=?", (source_project_id,)).fetchone()
            if source is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "源项目不存在", {"project_id": source_project_id})
            if connection.execute("SELECT 1 FROM projects WHERE code=?", (code,)).fetchone():
                raise DomainRuleError("PROJECT_CODE_EXISTS", "项目 code 已存在", {"code": code})
            episodes = connection.execute(
                """SELECT e.*, s.id AS source_season_id, s.number AS season_number,
                s.display_order AS season_display_order, s.code AS season_code, s.title AS season_title
                FROM episodes e JOIN seasons s ON s.id=e.season_id
                WHERE s.project_id=? ORDER BY s.display_order, e.display_order""",
                (source_project_id,),
            ).fetchall()
        if not episodes:
            raise DomainRuleError("PROJECT_TEMPLATE_EMPTY", "源项目没有可复制的分集结构")
        validate_project_spec(
            episode_count=len(episodes), aspect_ratio=source["aspect_ratio"], fps_num=source["fps_num"],
            fps_den=source["fps_den"], allow_unconfigured=True,
        )
        project_id = str(uuid.uuid4())
        final_root: Path | None = None
        try:
            season_episode_counts: dict[str, int] = {}
            for episode in episodes:
                season_key = str(episode["source_season_id"])
                season_episode_counts[season_key] = season_episode_counts.get(season_key, 0) + 1
            try:
                final_root, _ = build_project_tree(self.projects_root, project_id, code, title,
                                                   max(season_episode_counts.values()), season_count=len(season_episode_counts))
            except FileExistsError as error:
                raise DomainRuleError("PROJECT_ROOT_EXISTS", "目标项目目录已存在，未写入或删除该目录", {"code": code}) from error
            now = _utc_now()
            counts = {"seasons": 0, "episodes": 0, "scenes": 0, "shots": 0, "profiles": 0, "delivery_targets": 0}
            with self.database.transaction() as connection:
                if connection.execute("SELECT 1 FROM projects WHERE code=?", (code,)).fetchone():
                    raise DomainRuleError("PROJECT_CODE_EXISTS", "项目 code 已存在", {"code": code})
                connection.execute(
                    """INSERT INTO projects (id, code, title, status, template_version, root_rel, aspect_ratio, fps_num,
                    fps_den,timezone,width,height,primary_language,subtitle_mode,subtitle_language,created_at,updated_at,created_by)
                    VALUES (?, ?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (project_id, code, title, TEMPLATE_VERSION, code, source["aspect_ratio"], source["fps_num"],
                     source["fps_den"], source["timezone"], source["width"], source["height"], source["primary_language"],
                     source["subtitle_mode"], source["subtitle_language"], now, now, actor),
                )
                season_map: dict[str, str] = {}
                episode_map: dict[str, str] = {}
                for episode in episodes:
                    source_season_id = str(episode["source_season_id"])
                    if source_season_id not in season_map:
                        season_id = str(uuid.uuid4())
                        season_map[source_season_id] = season_id
                        connection.execute(
                            """INSERT INTO seasons (id, project_id, number, code, title, display_order, created_at, updated_at, created_by)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (season_id, project_id, episode["season_number"], episode["season_code"], episode["season_title"],
                             episode["season_display_order"], now, now, actor),
                        )
                        counts["seasons"] += 1
                    episode_id = str(uuid.uuid4())
                    episode_map[str(episode["id"])] = episode_id
                    connection.execute(
                        """INSERT INTO episodes (id, season_id, number, display_order, code, title, narrative_status,
                        production_status, target_duration_ms, source_range_json, created_at, updated_at, created_by)
                        VALUES (?, ?, ?, ?, ?, ?, 'OUTLINE', 'NOT_STARTED', ?, '{}', ?, ?, ?)""",
                        (episode_id, season_map[source_season_id], episode["number"], episode["display_order"], episode["code"],
                         episode["title"], episode["target_duration_ms"], now, now, actor),
                    )
                    counts["episodes"] += 1
                for scene in connection.execute("SELECT * FROM scenes WHERE project_id=? ORDER BY code", (source_project_id,)).fetchall():
                    connection.execute(
                        """INSERT INTO scenes (id, project_id, code, title, location, time_of_day, created_at, updated_at, created_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (str(uuid.uuid4()), project_id, scene["code"], scene["title"], scene["location"], scene["time_of_day"], now, now, actor),
                    )
                    counts["scenes"] += 1
                shots = connection.execute(
                    """SELECT sh.*, sr.fields_json FROM shots sh LEFT JOIN shot_revisions sr ON sr.id=sh.current_revision_id
                    JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id
                    WHERE s.project_id=? ORDER BY e.display_order, CAST(sh.order_key AS REAL), sh.code""",
                    (source_project_id,),
                ).fetchall()
                for shot in shots:
                    shot_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
                    connection.execute(
                        """INSERT INTO shots (id, episode_id, code, order_key, target_duration_ms, shot_type, status,
                        current_revision_id, created_at, updated_at, created_by) VALUES (?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?, ?, ?)""",
                        (shot_id, episode_map[str(shot["episode_id"])], shot["code"], shot["order_key"], shot["target_duration_ms"],
                         shot["shot_type"], revision_id, now, now, actor),
                    )
                    connection.execute(
                        """INSERT INTO shot_revisions (id, shot_id, revision_no, fields_json, is_frozen, created_at, updated_at, created_by)
                        VALUES (?, ?, 1, ?, 0, ?, ?, ?)""",
                        (revision_id, shot_id, shot["fields_json"] or "{}", now, now, actor),
                    )
                    counts["shots"] += 1
                plan = connection.execute(
                    """SELECT pp.code, pp.title, ppv.plan_json FROM project_plan_bindings ppb
                    JOIN production_plan_versions ppv ON ppv.id=ppb.production_plan_version_id
                    JOIN production_plans pp ON pp.id=ppv.production_plan_id WHERE ppb.project_id=?""",
                    (source_project_id,),
                ).fetchone()
                if plan:
                    plan_id, plan_version_id = str(uuid.uuid4()), str(uuid.uuid4())
                    plan_code = f"{code[:80]}_template_{project_id[:8]}"
                    connection.execute(
                        "INSERT INTO production_plans (id, code, title, created_at, updated_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
                        (plan_id, plan_code, plan["title"], now, now, actor),
                    )
                    connection.execute(
                        """INSERT INTO production_plan_versions (id, production_plan_id, version_no, plan_json, status, created_at, updated_at, created_by)
                        VALUES (?, ?, 1, ?, 'ACTIVE', ?, ?, ?)""",
                        (plan_version_id, plan_id, plan["plan_json"], now, now, actor),
                    )
                    connection.execute(
                        "INSERT INTO project_plan_bindings (id, project_id, production_plan_version_id, created_at, updated_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), project_id, plan_version_id, now, now, actor),
                    )
                    connection.execute("UPDATE projects SET production_plan_version_id=? WHERE id=?", (plan_version_id, project_id))
                profiles = connection.execute(
                    """SELECT ppb.capability, ppb.execution_profile_version_id FROM project_profile_bindings ppb
                    JOIN execution_profile_versions epv ON epv.id=ppb.execution_profile_version_id
                    WHERE ppb.project_id=? AND ppb.status='ACTIVE' AND epv.status='PUBLISHED'""",
                    (source_project_id,),
                ).fetchall()
                for profile in profiles:
                    connection.execute(
                        """INSERT INTO project_profile_bindings (id, project_id, capability, execution_profile_version_id, status,
                        created_at, updated_at, created_by) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)""",
                        (str(uuid.uuid4()), project_id, profile["capability"], profile["execution_profile_version_id"], now, now, actor),
                    )
                    counts["profiles"] += 1
                targets = connection.execute(
                    "SELECT * FROM delivery_targets WHERE project_id=? AND status='ACTIVE' AND transport='LOCAL_FILESYSTEM'",
                    (source_project_id,),
                ).fetchall()
                for target in targets:
                    target_id, target_version_id = str(uuid.uuid4()), str(uuid.uuid4())
                    connection.execute(
                        """INSERT INTO delivery_targets (id, project_id, code, title, transport, target_spec_json, status,
                        created_at, updated_at, created_by) VALUES (?, ?, ?, ?, 'LOCAL_FILESYSTEM', ?, 'ACTIVE', ?, ?, ?)""",
                        (target_id, project_id, target["code"], target["title"], target["target_spec_json"], now, now, actor),
                    )
                    latest = connection.execute(
                        "SELECT target_spec_json FROM delivery_target_versions WHERE delivery_target_id=? ORDER BY version_no DESC LIMIT 1",
                        (target["id"],),
                    ).fetchone()
                    connection.execute(
                        """INSERT INTO delivery_target_versions (id, delivery_target_id, version_no, target_spec_json, status,
                        created_at, updated_at, created_by) VALUES (?, ?, 1, ?, 'ACTIVE', ?, ?, ?)""",
                        (target_version_id, target_id, latest["target_spec_json"] if latest else target["target_spec_json"], now, now, actor),
                    )
                    counts["delivery_targets"] += 1
                metadata = {"source_project_id": source_project_id, "copied": counts,
                            "excluded": ["media", "workspace_asset_authorizations", "brand_kits", "jobs", "reviews", "deliveries", "audit_history"]}
                connection.execute(
                    """INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, request_id, summary, metadata_redacted_json)
                    VALUES (?, 'producer', 'PROJECT_TEMPLATE_COPIED', 'project', ?, ?, ?, ?)""",
                    (actor, project_id, request_id, f"从 {source['code']} 复制为新剧模板", _json(metadata)),
                )
                connection.execute(
                    "INSERT INTO outbox_events (type, project_id, subject_type, subject_id, payload_json) VALUES ('project.changed', ?, 'project', ?, ?)",
                    (project_id, project_id, _json({"status": "DRAFT", "revision": 1, "source_project_id": source_project_id})),
                )
                if simulate_failure:
                    raise RuntimeError("simulated project template copy failure")
        except Exception:
            if final_root and final_root.exists():
                shutil.rmtree(final_root, ignore_errors=True)
            raise
        return {"project": self.get_project(project_id), "copy_report": {"source_project_id": source_project_id, "copied": counts,
                "excluded": ["media", "workspace_asset_authorizations", "brand_kits", "jobs", "reviews", "deliveries", "audit_history"]}}

    def list_projects(self, limit: int = 50, *, search: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self.list_projects_page(limit, search=search, status=status)["items"])

    def list_projects_page(self, limit: int = 50, *, cursor: int = 0, search: str | None = None, status: str | None = None) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        cursor = max(0, int(cursor))
        if status is not None and status not in {"DRAFT", "ACTIVE", "PAUSED", "ARCHIVED"}:
            raise DomainRuleError("PROJECT_STATUS_INVALID", "项目状态筛选值无效", {"status": status})
        filters: list[str] = []
        parameters: list[object] = []
        normalized_search = search.strip() if search else ""
        if normalized_search:
            escaped = normalized_search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            filters.append("(title LIKE ? ESCAPE '\\' OR code LIKE ? ESCAPE '\\')")
            parameters.extend([f"%{escaped}%", f"%{escaped}%"])
        if status:
            filters.append("status=?")
            parameters.append(status)
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        parameters.extend([limit + 1, cursor])
        with self.database.connect() as connection:
            rows = connection.execute(f"SELECT * FROM projects{where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?", parameters).fetchall()
        has_more = len(rows) > limit
        return {"items": [dict(row) for row in rows[:limit]], "page": {"cursor": cursor, "limit": limit, "next_cursor": cursor + limit if has_more else None, "has_more": has_more}}

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

    def create_scene(
        self,
        project_id: str,
        code: str,
        title: str,
        location: str | None = None,
        time_of_day: str | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        normalized_code = code.strip()
        normalized_title = title.strip()
        if not normalized_code or not normalized_title:
            raise DomainRuleError("SCENE_FIELDS_REQUIRED", "母本场次 code 和 title 必填")
        scene_id = str(uuid.uuid4())
        now = _utc_now()
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            if connection.execute("SELECT 1 FROM scenes WHERE project_id=? AND code=?", (project_id, normalized_code)).fetchone():
                raise DomainRuleError("SCENE_CODE_CONFLICT", "同一项目的母本场次 code 必须唯一")
            connection.execute(
                """INSERT INTO scenes (id,project_id,code,title,location,time_of_day,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,1,'v2')""",
                (scene_id, project_id, normalized_code, normalized_title, location, time_of_day, now, now, actor),
            )
            connection.execute(
                """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'writer','MASTER_SCENE_CREATED','scene',?,'创建项目级母本场次',?)""",
                (actor, scene_id, _json({"project_id": project_id, "code": normalized_code})),
            )
        return self.get_scene(scene_id)

    def get_scene(self, scene_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM scenes WHERE id=?", (scene_id,)).fetchone()
        if row is None:
            raise DomainRuleError("SCENE_NOT_FOUND", "母本场次不存在", {"scene_id": scene_id})
        return dict(row)

    def list_scenes(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            rows = connection.execute("SELECT * FROM scenes WHERE project_id=? ORDER BY code,id", (project_id,)).fetchall()
        return [dict(row) for row in rows]

    def bind_episode_scene_range(
        self,
        episode_id: str,
        scene_id: str,
        ordinal: int,
        source_start: int,
        source_end: int,
        source_label: str | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if ordinal < 1 or source_start < 0 or source_end <= source_start:
            raise DomainRuleError("SCENE_RANGE_INVALID", "场次顺序及来源起止范围无效")
        range_id = str(uuid.uuid4())
        now = _utc_now()
        with self.database.transaction() as connection:
            episode = connection.execute(
                "SELECT e.id,s.project_id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=?", (episode_id,)
            ).fetchone()
            scene = connection.execute("SELECT id,project_id FROM scenes WHERE id=?", (scene_id,)).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
            if scene is None:
                raise DomainRuleError("SCENE_NOT_FOUND", "母本场次不存在", {"scene_id": scene_id})
            if str(episode["project_id"]) != str(scene["project_id"]):
                raise DomainRuleError("SCENE_EPISODE_PROJECT_MISMATCH", "母本场次与分集必须属于同一项目")
            if connection.execute("SELECT 1 FROM episode_scene_ranges WHERE episode_id=? AND scene_id=?", (episode_id, scene_id)).fetchone():
                raise DomainRuleError("SCENE_ALREADY_MAPPED_TO_EPISODE", "该母本场次已关联当前分集")
            if connection.execute("SELECT 1 FROM episode_scene_ranges WHERE episode_id=? AND ordinal=?", (episode_id, ordinal)).fetchone():
                raise DomainRuleError("SCENE_RANGE_ORDINAL_CONFLICT", "当前分集的场次顺序已被占用")
            connection.execute(
                """INSERT INTO episode_scene_ranges
                (id,episode_id,scene_id,ordinal,source_start,source_end,source_label,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,1,'v2')""",
                (range_id, episode_id, scene_id, ordinal, source_start, source_end, source_label, now, now, actor),
            )
            connection.execute(
                """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'writer','EPISODE_SCENE_RANGE_BOUND','episode_scene_range',?,'关联分集与项目级母本场次',?)""",
                (actor, range_id, _json({"episode_id": episode_id, "scene_id": scene_id, "ordinal": ordinal, "source_start": source_start, "source_end": source_end})),
            )
        return next(item for item in self.list_episode_scene_ranges(episode_id) if item["id"] == range_id)

    def list_episode_scene_ranges(self, episode_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM episodes WHERE id=?", (episode_id,)).fetchone() is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
            rows = connection.execute(
                """SELECT esr.*,sc.code AS scene_code,sc.title AS scene_title,sc.location,sc.time_of_day
                FROM episode_scene_ranges esr JOIN scenes sc ON sc.id=esr.scene_id
                WHERE esr.episode_id=? ORDER BY esr.ordinal,esr.id""",
                (episode_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_episodes(self, season_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM episodes WHERE season_id = ? ORDER BY display_order", (season_id,)).fetchall()
        return [dict(row) for row in rows]

    def list_shots(self, episode_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM shots WHERE episode_id = ? ORDER BY CAST(order_key AS REAL), code", (episode_id,)).fetchall()
        return [dict(row) for row in rows]

    def get_storyboard_workspace(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            episode = connection.execute("SELECT id,title FROM episodes WHERE id=?", (episode_id,)).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
            rows = connection.execute(
                """SELECT sh.*,sr.revision_no AS current_revision_no,sr.fields_json,sr.is_frozen
                FROM shots sh LEFT JOIN shot_revisions sr ON sr.id=sh.current_revision_id
                WHERE sh.episode_id=? ORDER BY CAST(sh.order_key AS REAL),sh.code""",
                (episode_id,),
            ).fetchall()
        items = []
        elapsed_ms = 0
        for ordinal, row in enumerate(rows, start=1):
            item = dict(row)
            item["fields"] = json.loads(str(item.pop("fields_json") or "{}"))
            item["display_ordinal"] = ordinal
            item["timeline_start_ms"] = elapsed_ms
            elapsed_ms += int(item["target_duration_ms"])
            item["timeline_end_ms"] = elapsed_ms
            items.append(item)
        return {
            "episode": dict(episode),
            "items": items,
            "views": ["TABLE", "STORYBOARD", "TIMELINE"],
            "identity_invariant": "shot.id and revision history never change during reorder",
            "total_duration_ms": elapsed_ms,
        }

    def plan_storyboard_batch(self, episode_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        workspace = self.get_storyboard_workspace(episode_id)
        items = workspace["items"]
        by_id = {str(item["id"]): item for item in items}
        current_ids = list(by_id)
        ordered_ids = [str(value) for value in payload.get("ordered_shot_ids", [])]
        edits = list(payload.get("edits", []))
        copies = list(payload.get("copies", []))
        issues: list[dict[str, Any]] = []
        if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != set(current_ids):
            issues.append({"code": "ORDER_SET_MISMATCH", "subject_id": episode_id, "message": "重排必须且只能包含当前集全部镜头一次"})
        edited_ids: set[str] = set()
        for index, edit in enumerate(edits):
            shot_id = str(edit.get("shot_id", ""))
            shot = by_id.get(shot_id)
            if shot is None:
                issues.append({"code": "SHOT_NOT_IN_EPISODE", "subject_id": shot_id, "item_index": index, "message": "批量编辑镜头不属于当前集"})
                continue
            if shot_id in edited_ids:
                issues.append({"code": "DUPLICATE_EDIT", "subject_id": shot_id, "item_index": index, "message": "同一镜头不能在一批中编辑两次"})
            edited_ids.add(shot_id)
            if int(edit.get("expected_revision", 0)) != int(shot["revision"]):
                issues.append({"code": "SHOT_REVISION_CONFLICT", "subject_id": shot_id, "item_index": index, "message": "镜头已被其他操作修改，请刷新后重试"})
            if edit.get("fields") is not None and not isinstance(edit.get("fields"), dict):
                issues.append({"code": "INVALID_FIELDS", "subject_id": shot_id, "item_index": index, "message": "fields 必须是对象"})
        existing_codes = {str(item["code"]).casefold() for item in items}
        copy_codes: set[str] = set()
        for index, copy in enumerate(copies):
            source_id = str(copy.get("source_shot_id", ""))
            code = str(copy.get("code", "")).strip()
            folded = code.casefold()
            if source_id not in by_id:
                issues.append({"code": "COPY_SOURCE_NOT_IN_EPISODE", "subject_id": source_id, "item_index": index, "message": "复制来源不属于当前集"})
            if not code:
                issues.append({"code": "COPY_CODE_REQUIRED", "subject_id": source_id, "item_index": index, "message": "复制镜头必须提供新编号"})
            elif folded in existing_codes or folded in copy_codes:
                issues.append({"code": "SHOT_CODE_CONFLICT", "subject_id": source_id, "item_index": index, "message": "复制后的镜头编号在当前集冲突"})
            copy_codes.add(folded)
        source_snapshot = [
            {"id": str(item["id"]), "order_key": str(item["order_key"]), "revision": int(item["revision"]), "current_revision_id": str(item["current_revision_id"])}
            for item in items
        ]
        canonical = {"episode_id": episode_id, "source_snapshot": source_snapshot, "ordered_shot_ids": ordered_ids, "edits": edits, "copies": copies}
        plan_hash = hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
        return {
            "episode_id": episode_id,
            "ordered_shot_ids": ordered_ids,
            "edits": edits,
            "copies": copies,
            "source_snapshot": source_snapshot,
            "plan_hash": plan_hash,
            "valid": not issues,
            "issues": issues,
            "summary": {"reordered": sum(shot_id != current_ids[index] for index, shot_id in enumerate(ordered_ids)) if len(ordered_ids) == len(current_ids) else 0, "edited": len(edits), "copied": len(copies)},
            "runtime_contacted": False,
            "network_contacted": False,
        }

    def commit_storyboard_batch(self, episode_id: str, payload: dict[str, Any], expected_plan_hash: str) -> dict[str, Any]:
        plan = self.plan_storyboard_batch(episode_id, payload)
        if plan["plan_hash"] != expected_plan_hash:
            raise DomainRuleError("STORYBOARD_PLAN_STALE", "批量计划已变化，请重新校验")
        if not plan["valid"]:
            raise DomainRuleError("STORYBOARD_BATCH_INVALID", "批量计划含校验问题", {"issues": plan["issues"]})
        now = _utc_now()
        changed_shot_ids: list[str] = []
        copied_ids: list[str] = []
        with self.database.transaction() as connection:
            for ordinal, shot_id in enumerate(plan["ordered_shot_ids"], start=1):
                connection.execute("UPDATE shots SET order_key=?,updated_at=? WHERE id=? AND episode_id=?", (str(ordinal), now, shot_id, episode_id))
            for edit in plan["edits"]:
                shot_id = str(edit["shot_id"])
                shot = connection.execute("SELECT * FROM shots WHERE id=? AND episode_id=?", (shot_id, episode_id)).fetchone()
                if shot is None or int(shot["revision"]) != int(edit["expected_revision"]):
                    raise DomainRuleError("SHOT_REVISION_CONFLICT", "镜头已被其他操作修改，请重新校验", {"shot_id": shot_id})
                updates: list[str] = []
                values: list[object] = []
                for column in ("target_duration_ms", "shot_type"):
                    if edit.get(column) is not None:
                        updates.append(f"{column}=?")
                        values.append(edit[column])
                if edit.get("fields") is not None:
                    current = connection.execute("SELECT fields_json FROM shot_revisions WHERE id=?", (shot["current_revision_id"],)).fetchone()
                    fields = json.loads(str(current["fields_json"])) if current else {}
                    fields.update(edit["fields"])
                    revision_no = int(connection.execute("SELECT COALESCE(MAX(revision_no),0) FROM shot_revisions WHERE shot_id=?", (shot_id,)).fetchone()[0]) + 1
                    revision_id = str(uuid.uuid4())
                    connection.execute("INSERT INTO shot_revisions (id,shot_id,revision_no,fields_json,is_frozen,created_at,updated_at,created_by) VALUES (?,?,?,?,0,?,?,'local-user')", (revision_id, shot_id, revision_no, _json(fields), now, now))
                    updates.append("current_revision_id=?")
                    values.append(revision_id)
                    updates.append("status='DIRECTED'")
                updates.extend(["revision=revision+1", "updated_at=?"])
                values.extend([now, shot_id])
                connection.execute(f"UPDATE shots SET {','.join(updates)} WHERE id=?", values)
                changed_shot_ids.append(shot_id)
            for copy in plan["copies"]:
                source = connection.execute("SELECT * FROM shots WHERE id=? AND episode_id=?", (copy["source_shot_id"], episode_id)).fetchone()
                source_revision = connection.execute("SELECT fields_json,is_frozen FROM shot_revisions WHERE id=?", (source["current_revision_id"],)).fetchone()
                shot_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
                maximum = float(connection.execute("SELECT COALESCE(MAX(CAST(order_key AS REAL)),0) FROM shots WHERE episode_id=?", (episode_id,)).fetchone()[0])
                connection.execute("""INSERT INTO shots (id,episode_id,code,order_key,target_duration_ms,shot_type,status,current_revision_id,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,?,?, ?,?,?, 'local-user',1,'v2')""", (shot_id, episode_id, str(copy["code"]).strip(), str(maximum + 1), source["target_duration_ms"], source["shot_type"], source["status"], revision_id, now, now))
                connection.execute("INSERT INTO shot_revisions (id,shot_id,revision_no,fields_json,is_frozen,created_at,updated_at,created_by) VALUES (?,?,1,?,?,?,?,'local-user')", (revision_id, shot_id, source_revision["fields_json"] if source_revision else "{}", source_revision["is_frozen"] if source_revision else 0, now, now))
                copied_ids.append(shot_id)
            connection.execute("""INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES ('local-user','writer','STORYBOARD_BATCH_COMMITTED','episode',?,'分镜批量计划已提交',?)""", (episode_id, _json({"plan_hash": expected_plan_hash, "changed_shot_ids": changed_shot_ids, "copied_shot_ids": copied_ids})))
        for shot_id in changed_shot_ids:
            ReviewService(self.database).mark_stale_for_owner(shot_id, "shot_revision_changed")
        return {"plan_hash": expected_plan_hash, "changed_shot_ids": changed_shot_ids, "copied_shot_ids": copied_ids, "storyboard": self.get_storyboard_workspace(episode_id)}

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
        if "camera_plan" in fields:
            camera_plan = CameraPlan.from_payload(fields["camera_plan"])
            # An unresolved plan may be saved as a truthful draft so the
            # director can see the blocker, but any executable mode must be
            # re-resolved against the current Published Profile server-side.
            if camera_plan.mode != "UNSUPPORTED":
                self._assert_camera_profile_resolution(camera_plan)
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

    def _assert_camera_profile_resolution(self, camera_plan: CameraPlan) -> None:
        """Re-resolve executable CameraPlans against the immutable Published Profile.

        The browser uses the profile resolver before saving, but the API must
        not trust a client-provided ``mode`` or profile id.  This check is
        deliberately local/read-only and mirrors GenerationService's later
        preflight gate so ShotRevision and Variant snapshots share one truth.
        """
        profile_version_id = camera_plan.profile_version_id
        if not profile_version_id:
            raise DomainRuleError("CAMERA_PROFILE_REQUIRED", "可执行 CameraPlan 必须绑定 Published ProfileVersion")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT status, parameter_schema_json FROM execution_profile_versions WHERE id=?",
                (profile_version_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "CameraPlan 绑定的 ProfileVersion 不存在", {"profile_version_id": profile_version_id})
        if str(row["status"]) != "PUBLISHED":
            raise DomainRuleError("PROFILE_NOT_PUBLISHED", "只有已发布 Profile 才能保存可执行 CameraPlan", {"profile_version_id": profile_version_id})
        try:
            schema = json.loads(str(row["parameter_schema_json"] or "{}"))
        except json.JSONDecodeError as error:
            raise DomainRuleError("PROFILE_CAMERA_CONTRACT_INVALID", "Profile parameter schema 不是有效 JSON") from error
        capabilities = schema.get("capabilities", {}) if isinstance(schema, dict) else {}
        camera_contract = capabilities.get("camera", {}) if isinstance(capabilities, dict) else {}
        support = str(camera_contract.get("support", "UNSUPPORTED")) if isinstance(camera_contract, dict) else "UNSUPPORTED"
        if support not in {"NATIVE", "PROMPT_FALLBACK", "UNSUPPORTED"}:
            raise DomainRuleError("PROFILE_CAMERA_CONTRACT_INVALID", "Profile camera capability support 无效")
        fallback = support == "PROMPT_FALLBACK" and camera_contract.get("prompt_fallback") is True
        if support == "PROMPT_FALLBACK" and not fallback:
            raise DomainRuleError("PROFILE_CAMERA_FALLBACK_INVALID", "Camera prompt fallback 必须由 Profile 显式声明")
        resolved = resolve_camera_plan(
            native_supported=support == "NATIVE",
            prompt_fallback_supported=fallback,
            shot_type=camera_plan.shot_type,
            movement=camera_plan.movement,
            prompt_text=camera_plan.prompt_text,
            direction=camera_plan.direction,
            intensity=camera_plan.intensity,
            curve=camera_plan.curve,
            profile_version_id=profile_version_id,
        )
        if resolved.mode == "UNSUPPORTED":
            raise DomainRuleError(
                "CAMERA_PLAN_UNSUPPORTED",
                "当前 Published Profile 不支持该结构化运镜，不能保存可执行 revision",
                {"profile_version_id": profile_version_id, "movement": camera_plan.movement},
            )
        if resolved.to_dict() != camera_plan.to_dict():
            raise DomainRuleError(
                "CAMERA_PLAN_RESOLUTION_STALE",
                "CameraPlan 与当前 Published Profile capability contract 不一致，请重新裁决",
                {"profile_version_id": profile_version_id},
            )

    def mark_shot_production_ready(self, shot_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            shot = connection.execute("SELECT * FROM shots WHERE id = ?", (shot_id,)).fetchone()
            if shot is None:
                raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
            revision = connection.execute("SELECT fields_json FROM shot_revisions WHERE id = ?", (shot["current_revision_id"],)).fetchone()
            fields = json.loads(revision["fields_json"]) if revision else {}
            validate_shot_ready(fields)
            self._assert_camera_profile_resolution(CameraPlan.from_payload(fields["camera_plan"]))
            require_transition(VALID_SHOT_TRANSITIONS, shot["status"], "READY", "Shot")
            connection.execute("UPDATE shots SET status = 'READY', updated_at = ?, revision = revision + 1 WHERE id = ?", (_utc_now(), shot_id))
        return self.get_shot(shot_id)
